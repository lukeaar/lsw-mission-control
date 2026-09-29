from __future__ import annotations

import json
import os

import pytest

from lsw_mission_control import testing
from lsw_mission_control.agents import (
    FinishedStore,
    final_merge_labels,
    findings_of,
    label_names,
    latest_by_label,
    review_needs_fix,
    scan_agents,
    store_since,
    stored_ok,
    transcript_facts,
)
from lsw_mission_control.config import FinalMergeCfg
from lsw_mission_control.plan import EMPTY_PLAN, parse_plan

from conftest import NOW
from scenarios import HOUR, MIN, Project, ts


def test_scan_reads_journal_kinds_and_transcripts(tmp_path):
    p = Project(tmp_path)
    p.agent("build:a", status="done", start_ago=2 * HOUR, quiet_s=HOUR, findings=[{"severity": "minor"}])
    p.agent("review:a", status="failed", start_ago=50 * MIN, quiet_s=40 * MIN)
    p.agent("fix:a", start_ago=10 * MIN, quiet_s=5, action="Edit · src/a.py", writes_log="a-tests")
    p.finish_runs()
    agents = {a["label"]: a for a in scan_agents(p.projects, NOW)}
    assert agents["build:a"]["status"] == "done" and agents["build:a"]["result"] == {"findings": [{"severity": "minor"}]}
    assert agents["review:a"]["status"] == "failed"
    fix = agents["fix:a"]
    assert fix["status"] == "running" and fix["run"] == "wf_run-a" and fix["phase"] == "Fix"
    assert fix["t0"] == pytest.approx(NOW - 10 * MIN) and fix["t1"] == pytest.approx(NOW - 5)
    assert fix["action"] == "Edit · src/a.py" and fix["logs"] == frozenset({"a-tests"})


def test_scan_skips_runs_outside_the_window(tmp_path):
    p = Project(tmp_path)
    p.agent("build:a")
    p.finish_runs()
    d = next(p.projects.glob("*/subagents/workflows/wf_*"))
    os.utime(d, (NOW - 49 * HOUR, NOW - 49 * HOUR))
    assert scan_agents(p.projects, NOW) == []
    assert len(scan_agents(p.projects, NOW, window_s=50 * HOUR)) == 1


@pytest.mark.parametrize("cmd, logs", [
    ("pytest > run.log", {"run"}), ("pytest >> /tmp/x/run-2.log 2>&1", {"run-2"}), ("pytest | tee -a out.log", {"out"}),
    ("pytest | tee out.log", {"out"}), ("cat < in.log", set()), ("tail -f x.log", set()),
])
def test_log_writers(tmp_path, cmd, logs):
    f = tmp_path / "agent-x.jsonl"
    f.write_text(json.dumps({"timestamp": ts(NOW)}) + "\n" + json.dumps(
        {"timestamp": ts(NOW), "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}) + "\n")
    assert transcript_facts(f)[3] == frozenset(logs)


def test_the_transcript_cache_keeps_one_entry_per_transcript(tmp_path):
    """it was keyed by (path, size, mtime) and grew by one entry every time a transcript moved."""
    from lsw_mission_control import agents as ag

    p = Project(tmp_path)
    p.agent("build:x", start_ago=10 * MIN, quiet_s=5)
    p.finish_runs()
    transcript = next(p.projects.glob("*/subagents/workflows/*/agent-*.jsonl"))
    for i in range(5):
        with open(transcript, "a") as fh:
            fh.write(json.dumps({"timestamp": ts(NOW - 4 + i), "type": "user"}) + "\n")
        os.utime(transcript, (NOW - 4 + i, NOW - 4 + i))
        (a,) = scan_agents(p.projects, NOW)
        assert a["t1"] == NOW - 4 + i  # still re-read on every change
    assert [k for k in ag._ts_cache if k.startswith(str(p.projects))] == [str(transcript)]
    run_dir = transcript.parent
    os.utime(run_dir, (NOW - 50 * HOUR, NOW - 50 * HOUR))  # the run leaves the scan window
    assert scan_agents(p.projects, NOW) == []
    assert not [k for k in ag._ts_cache if k.startswith(str(p.projects))]


def test_transcript_facts_of_a_missing_or_odd_file(tmp_path):
    assert transcript_facts(tmp_path / "nope.jsonl") == (None, None, "", frozenset())
    f = tmp_path / "odd.jsonl"
    f.write_text("not json\n{also not\n")
    assert transcript_facts(f) == (None, None, "", frozenset())


def journal_dir(p: Project, run: str = "wf_lines"):
    d = p.projects / "sess-1" / "subagents" / "workflows" / run
    d.mkdir(parents=True)
    return d


@pytest.mark.parametrize("sep", [" ", " ", "\x85"])
def test_a_result_holding_a_line_separator_is_one_line(tmp_path, sep):
    """a JSON string may hold U+2028, U+2029 and U+0085 unescaped, and str.splitlines() splits at
    them: the result's line was lost, and the finished agent read as running, then failed"""
    d = journal_dir(Project(tmp_path))
    events = [{"type": "started", "key": "k1", "agentId": "a1", "label": "measure:x", "phase": "Build"},
              {"type": "result", "key": "k1", "agentId": "a1", "result": {"summary": f"one{sep}two", "findings": []}}]
    (d / "journal.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    assert sep in (d / "journal.jsonl").read_text()  # written raw, as the runtime writes it
    (a,) = scan_agents(tmp_path / "projects", NOW)
    assert a["status"] == "done" and a["result"]["summary"] == f"one{sep}two"


def test_a_journal_line_that_is_not_an_object_is_skipped(tmp_path):
    d = journal_dir(Project(tmp_path))
    (d / "journal.jsonl").write_text(
        '3\n"x"\n[1]\nnull\n{"type": "started", "key": "k1", "agentId": "a1", "label": "measure:x"}\n'
        '{"type": "result"\n{"type": "result", "key": "k1", "agentId": "a1", "result": {}}\n')
    (a,) = scan_agents(tmp_path / "projects", NOW)
    assert a["status"] == "done"


def plan_with(**kw):
    raw = {"release": "1", "items": [{"name": "Item A", "key": "a", "before": [["design", ["design:a", "research:a"], 10]]}],
           "other": [{"name": "Other O", "stages": [["s", "job:o", 5]]}],
           "next": {"release": "2", "items": [{"name": "Next N", "key": "n", "build": 5}]}}
    raw.update(kw)
    return parse_plan(raw)


def test_label_names():
    names = label_names(plan_with(), FinalMergeCfg(name="Merge it"))
    assert names == {"build:a": "Item A", "review:a": "Item A", "fix:a": "Item A", "design:a": "Item A",
                     "research:a": "Item A", "build:final-merge": "Merge it", "review:final-merge": "Merge it",
                     "fix:final-merge": "Merge it", "job:o": "Other O", "build:n": "Next N"}


def rec(label, status="done", run="wf_1", aid="x1", t0=NOW - 100, t1=NOW - 50, **kw):
    return {"id": aid, "label": label, "phase": "", "status": status, "run": run, "t0": t0, "t1": t1, "result": None,
            "action": "", "logs": frozenset(), **kw}


# the old single-file dashboard's predicate, verbatim: every record the store writes must pass it,
# or a dashboard of the old code sharing the cache would drop it
def _old_stored_ok(rec) -> bool:
    return (isinstance(rec, dict) and all(isinstance(rec.get(k), str) for k in ("id", "label", "run"))
            and rec.get("status") in ("done", "failed", "running")
            and all(rec.get(k) is None or (isinstance(rec.get(k), (int, float)) and not isinstance(rec.get(k), bool))
                    for k in ("t0", "t1")))


def test_finished_store(tmp_path):
    path = tmp_path / "finished.json"
    plan = plan_with()
    names = label_names(plan, FinalMergeCfg())
    store = FinishedStore(path)
    agents = [rec("build:a", aid="b1"), rec("untracked:z", aid="z1"), rec("fix:a", status="running", aid="f1", t1=None)]
    out = store.merge(agents, plan, names)
    saved = json.loads(path.read_text())
    assert saved["release"] == "1" and set(saved["agents"]) == {"wf_1/b1", "wf_1/f1"}
    assert all(_old_stored_ok(r) and stored_ok(r) for r in saved["agents"].values())
    assert out == agents
    # the journal has left the window: the store brings the stage back
    out = store.merge([], plan, names)
    assert {a["label"] for a in out} == {"build:a", "fix:a"}
    # a running record's last-seen time is rewritten at most every 10 min
    mtime = path.stat().st_mtime_ns
    store.merge([rec("fix:a", status="running", aid="f1", t1=None)], plan, names)
    assert path.stat().st_mtime_ns == mtime
    # malformed records are dropped
    data = json.loads(path.read_text())
    data["agents"]["bad"] = {"id": 1}
    path.write_text(json.dumps(data))
    store.merge([], plan, names)
    assert "bad" not in json.loads(path.read_text())["agents"]
    # a new release keeps only the stages of work still on the plan
    plan2 = parse_plan({"release": "2", "items": [{"name": "Item A", "key": "a"}]})
    names2 = label_names(plan2, FinalMergeCfg())
    assert {a["label"] for a in store.merge([], plan2, names2)} == {"build:a", "fix:a"}
    # saved at once: the store records when the new release began (it used to wait for the next change)
    pruned = json.loads(path.read_text())
    assert pruned["release"] == "2" and pruned["since"] == NOW
    store.merge([rec("review:a", aid="r1")], plan2, names2)
    kept = json.loads(path.read_text())
    assert kept["release"] == "2" and {r["label"] for r in kept["agents"].values()} == {"build:a", "fix:a", "review:a"}


def test_a_plan_that_never_loaded_never_prunes_or_writes_the_store(tmp_path):
    """A broken or missing plan at start-up is not a release change: the store keeps every record
    (it used to be pruned down to the final merge's, and a finished item fell back to 'queued')."""
    path = tmp_path / "finished.json"
    plan = plan_with()
    names = label_names(plan, FinalMergeCfg())
    store = FinishedStore(path)
    store.merge([rec("build:a", aid="b1")], plan, names)
    before = path.read_text()
    empty_names = label_names(EMPTY_PLAN, FinalMergeCfg())
    out = store.merge([rec("build:final-merge", status="running", aid="m1", t1=NOW)], EMPTY_PLAN, empty_names,
                      loaded=False)
    assert path.read_text() == before  # not pruned, not written
    assert [a["label"] for a in out] == ["build:final-merge"]
    # the plan is back: its stage is still there
    assert {a["label"] for a in store.merge([], plan, names)} == {"build:a"}


def test_the_engine_passes_whether_a_plan_loaded(tmp_path):
    from scenarios import midway

    p = Project(tmp_path)
    midway(p)
    e = p.engine()
    e.build_frame(120)
    kept = json.loads((p.cache / "finished.json").read_text())
    assert kept["release"] == "1.4.0" and len(kept["agents"]) > 1
    (p.dot / "status_plan.json").write_text("{ broken")
    p.agent("build:final-merge", run="wf_merge", start_ago=600, quiet_s=5)  # new: the store would change
    e2 = p.engine()  # a new process that finds the plan broken
    e2.build_frame(120)
    assert json.loads((p.cache / "finished.json").read_text()) == kept


def test_finished_store_readonly_writes_nothing(tmp_path):
    path = tmp_path / "finished.json"
    plan = plan_with()
    FinishedStore(path, readonly=True).merge([rec("build:a")], plan, label_names(plan, FinalMergeCfg()))
    assert not path.exists()


def test_latest_by_label():
    old = rec("build:a", aid="1", t0=NOW - 500)
    new = rec("build:a", aid="2", t0=NOW - 100, run="wf_2")
    silent = rec("review:a", status="running", aid="3", t1=NOW - 26 * MIN)
    alive = rec("fix:a", status="running", aid="4", t1=NOW - 60)
    out = latest_by_label([old, new, silent, alive], NOW, 25 * MIN)
    assert out["build:a"]["id"] == "2" and out["wf_1/build:a"]["id"] == "1" and out["wf_2/build:a"]["id"] == "2"
    assert out["review:a"]["status"] == "failed" and out["fix:a"]["status"] == "running"


def test_findings_and_review_needs_fix():
    assert findings_of(None) == [] and findings_of({"result": "free text"}) == []
    assert findings_of({"result": {"findings": [{"severity": "nit"}, "junk"]}}) == [{"severity": "nit"}]
    assert review_needs_fix(None) is None and review_needs_fix(rec("r", status="running")) is None
    assert review_needs_fix(rec("r", result={"findings": [{"severity": "nit"}]})) is False
    assert review_needs_fix(rec("r", result={"findings": [{"severity": "major"}]})) is True


# ── a release change and the final merge (every release has one of its own under the same labels) ──
FM = final_merge_labels(FinalMergeCfg())
SHIPPED_FM = [rec("build:final-merge", aid="m1", run="wf_merge", t0=NOW - 5 * HOUR, t1=NOW - 4 * HOUR),
              rec("review:final-merge", aid="m2", run="wf_merge", t0=NOW - 4 * HOUR, t1=NOW - 3.5 * HOUR),
              rec("fix:final-merge", aid="m3", run="wf_merge", t0=NOW - 3.5 * HOUR, t1=NOW - 3 * HOUR)]


def merge(store, agents, plan, **kw):
    return store.merge(agents, plan, label_names(plan, FinalMergeCfg()), release_bound=FM, **kw)


def shipped_store(tmp_path):
    """Release 1 shipped: its final merge is done and stored, and an item of the next release has begun."""
    path = tmp_path / "finished.json"
    plan1 = plan_with()
    store = FinishedStore(path)
    merge(store, SHIPPED_FM + [rec("build:a", aid="b1"), rec("build:n", aid="n1", t0=NOW - 2 * HOUR, t1=NOW - HOUR)], plan1)
    assert {r["label"] for r in json.loads(path.read_text())["agents"].values()} >= FM
    return path, store


def plan2():
    """Release 2: the next release's item N is now this one's (its build:n carries over)."""
    return parse_plan({"release": "2", "items": [{"name": "Next N", "key": "n", "build": 5}],
                       "next": {"release": "3", "items": []}})


def test_a_release_change_drops_the_final_merge_and_records_when_it_began(tmp_path):
    path, store = shipped_store(tmp_path)
    labels = latest_by_label(merge(store, [], plan2()), NOW, 25 * MIN)
    assert not FM & set(labels)  # the shipped release's final merge is not this one's
    saved = json.loads(path.read_text())
    assert saved["release"] == "2" and saved["since"] == NOW  # saved at once
    assert {r["label"] for r in saved["agents"].values()} == {"build:n"}


def test_a_label_moving_from_next_to_items_keeps_its_history(tmp_path):
    path, store = shipped_store(tmp_path)
    labels = latest_by_label(merge(store, [], plan2()), NOW, 25 * MIN)
    assert labels["build:n"]["status"] == "done" and labels["build:n"]["id"] == "n1"


def test_the_scan_never_brings_the_shipped_final_merge_back(tmp_path):
    """the journals still hold the shipped release's final merge (two days' window): found again,
    it must not read as this release's, nor be stored again. Agents at work still sees it."""
    path, store = shipped_store(tmp_path)
    p2 = plan2()
    merge(store, [], p2)
    before = path.read_text()
    out = merge(store, SHIPPED_FM + [rec("build:n", aid="n1", t0=NOW - 2 * HOUR, t1=NOW - HOUR)], p2)
    assert path.read_text() == before  # nothing new to store
    assert not FM & set(latest_by_label(out, NOW, 25 * MIN))
    assert sorted(a["label"] for a in out if a.get("earlier_release")) == sorted(FM)
    # a later refresh (the clock has moved) does not move `since`
    testing.freeze(NOW + HOUR)
    merge(store, SHIPPED_FM, p2)
    assert json.loads(path.read_text())["since"] == NOW


def test_a_final_merge_begun_after_the_change_is_tracked(tmp_path):
    path, store = shipped_store(tmp_path)
    p2 = plan2()
    merge(store, [], p2)
    testing.freeze(NOW + 3 * HOUR)
    new = rec("build:final-merge", status="running", aid="m9", run="wf_merge2", t0=NOW + 2 * HOUR, t1=NOW + 3 * HOUR - 30)
    out = merge(store, SHIPPED_FM + [new], p2)
    labels = latest_by_label(out, NOW + 3 * HOUR, 25 * MIN)
    assert labels["build:final-merge"]["id"] == "m9" and "review:final-merge" not in labels
    assert [r["id"] for r in json.loads(path.read_text())["agents"].values() if r["label"] in FM] == ["m9"]
    # its journal leaves the window: the store still has it
    labels = latest_by_label(merge(store, [], p2), NOW + 3 * HOUR, 25 * MIN)
    assert labels["build:final-merge"]["id"] == "m9"


def test_a_store_written_before_since_was_kept_still_works(tmp_path):
    """no "since": everything in it counts, as it always did (a store can be hand-migrated)."""
    path = tmp_path / "finished.json"
    plan = plan_with()
    old = {"release": "1", "agents": {f"{r['run']}/{r['id']}": {k: v for k, v in r.items() if k not in ("action", "logs")}
                                      for r in SHIPPED_FM}}
    path.write_text(json.dumps(old))
    store = FinishedStore(path)
    labels = latest_by_label(merge(store, SHIPPED_FM, plan), NOW, 25 * MIN)
    assert FM <= set(labels) and "since" not in json.loads(path.read_text())
    # a release change from it starts `since`
    merge(store, SHIPPED_FM, plan2())
    assert json.loads(path.read_text())["since"] == NOW
    # hand-migrated: since set, its final merge dropped
    migrated = {"release": "1", "since": NOW - 2 * HOUR, "agents": {}}
    path.write_text(json.dumps(migrated))
    assert not FM & set(latest_by_label(merge(store, SHIPPED_FM, plan), NOW, 25 * MIN))
    assert json.loads(path.read_text()) == migrated


@pytest.mark.parametrize("since, want", [(None, 0.0), (0, 0.0), (NOW, NOW), ("x", 0.0), (True, 0.0), (float("nan"), 0.0),
                                         (-5, 0.0), (NOW + 1, 0.0), (NOW * 1000, 0.0)])
def test_store_since(since, want):
    store = {"release": "1", "agents": {}} if since is None else {"release": "1", "since": since, "agents": {}}
    assert store_since(store) == want
    assert store_since(None) == 0.0


def test_a_since_later_than_now_never_hides_this_releases_final_merge(tmp_path):
    """milliseconds typed in a hand migration: read as no `since` (everything counts), where it
    would hide every final merge begun before it, this release's own included, with nothing to say why"""
    path = tmp_path / "finished.json"
    path.write_text(json.dumps({"release": "1", "since": NOW * 1000, "agents": {}}))
    running = rec("build:final-merge", status="running", aid="m9", run="wf_merge2", t0=NOW - 10 * MIN, t1=NOW - 30)
    labels = latest_by_label(merge(FinishedStore(path), [running], plan_with()), NOW, 25 * MIN)
    assert labels["build:final-merge"]["id"] == "m9"
    assert [r["id"] for r in json.loads(path.read_text())["agents"].values()] == ["m9"]


def at_release_one(tmp_path):
    """Release 1 (begun 6 h ago) with its final merge done, and an item of release 2 begun."""
    path = tmp_path / "finished.json"
    records = SHIPPED_FM + [rec("build:a", aid="b1"), rec("build:n", aid="n1", t0=NOW - 2 * HOUR, t1=NOW - HOUR)]
    path.write_text(json.dumps({"release": "1", "since": NOW - 6 * HOUR, "agents": {
        f"{r['run']}/{r['id']}": {k: v for k, v in r.items() if k not in ("action", "logs")} for r in records}}))
    return path, FinishedStore(path)


def test_a_release_moved_straight_back_gets_its_final_merge_back(tmp_path):
    """a typo in the plan's release fixed a minute later, or a move undone when the tag fails: the
    release gets back what the change dropped and its own `since`, even with its journals gone.
    A fresh `since` lost its final merge for good (the journals' copy read as an earlier release's)."""
    path, store = at_release_one(tmp_path)
    merge(store, [], plan2())
    moved = json.loads(path.read_text())
    assert moved["since"] == NOW and moved["previous"]["release"] == "1" and moved["previous"]["since"] == NOW - 6 * HOUR
    assert {r["label"] for r in moved["previous"]["agents"].values()} == FM | {"build:a"}
    testing.freeze(NOW + MIN)
    labels = latest_by_label(merge(store, [], plan_with()), NOW + MIN, 25 * MIN)  # the store alone
    assert FM <= set(labels) and labels["build:final-merge"]["id"] == "m1" and labels["build:a"]["id"] == "b1"
    back = json.loads(path.read_text())
    assert back["release"] == "1" and back["since"] == NOW - 6 * HOUR and back["previous"]["release"] == "2"
    # the journals still hold it: it counts, and is not an earlier release's
    out = merge(store, SHIPPED_FM, plan_with())
    assert not any(a.get("earlier_release") for a in out) and FM <= set(latest_by_label(out, NOW + MIN, 25 * MIN))
    # moved on again: release 2 is where it was, its `since` included
    merge(store, [], plan2())
    assert json.loads(path.read_text())["since"] == NOW


def test_a_release_moved_on_twice_starts_afresh(tmp_path):
    """only a move straight back restores: 1 -> 2 -> 3 is two new releases"""
    path, store = at_release_one(tmp_path)
    merge(store, [], plan2())
    testing.freeze(NOW + HOUR)
    p3 = parse_plan({"release": "3", "items": [{"name": "Next N", "key": "n", "build": 5}]})
    labels = latest_by_label(merge(store, SHIPPED_FM, p3), NOW + HOUR, 25 * MIN)
    assert not FM & set(labels) and labels["build:n"]["id"] == "n1"
    saved = json.loads(path.read_text())
    assert saved["since"] == NOW + HOUR and saved["previous"]["release"] == "2"


def test_a_stand_in_plan_never_starts_a_release(tmp_path):
    """a broken plan at start-up: its release "?" is no release change, so the final merge's records
    stay, `since` stays, and nothing is written."""
    path, store = shipped_store(tmp_path)
    before = path.read_text()
    out = store.merge(SHIPPED_FM, EMPTY_PLAN, label_names(EMPTY_PLAN, FinalMergeCfg()), loaded=False, release_bound=FM)
    assert path.read_text() == before and "since" not in json.loads(before)
    assert FM <= set(latest_by_label(out, NOW, 25 * MIN))
    # the same with a release already begun: its `since` still applies
    merge(store, [], plan2())
    before = path.read_text()
    out = store.merge(SHIPPED_FM, EMPTY_PLAN, label_names(EMPTY_PLAN, FinalMergeCfg()), loaded=False, release_bound=FM)
    assert path.read_text() == before and not FM & set(latest_by_label(out, NOW, 25 * MIN))


def test_readonly_never_writes_a_release_change(tmp_path):
    path, _store = shipped_store(tmp_path)
    before = path.read_text()
    out = merge(FinishedStore(path, readonly=True), SHIPPED_FM, plan2())
    assert path.read_text() == before and not FM & set(latest_by_label(out, NOW, 25 * MIN))

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
from scenarios import HOUR, MIN, Project, midway, ts


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
    assert transcript_facts(tmp_path / "nope.jsonl") == (None, None, "", frozenset(), False)
    f = tmp_path / "odd.jsonl"
    f.write_text("not json\n{also not\n")
    assert transcript_facts(f) == (None, None, "", frozenset(), False)
    # valid JSON that is not a record, last or among them, and a message that is not an object: skipped,
    # never a failed frame (they raised)
    f.write_text(json.dumps({"timestamp": ts(NOW - 60), "type": "user", "message": "go"}) + "\n3\n\"x\"\n[1]\n")
    assert transcript_facts(f) == (NOW - 60, NOW - 60, "", frozenset(), False)


@pytest.mark.parametrize("content, action", [
    ([{"type": "tool_use", "name": "Bash", "input": "pytest > run.log"}], "Bash · "),  # an input that is not an object
    ([{"type": "tool_use", "name": "Bash", "input": ["pytest > run.log"]}], "Bash · "),
    (["tool_use > run.log", {"type": "tool_use", "name": "Bash", "input": {"command": "pytest > b.log"}}],
     "Bash · pytest > b.log"),  # a block that is not an object, beside a call that writes a log
])
def test_an_odd_tool_call_is_read_past(tmp_path, content, action):
    """they raised, and the frame failed while the transcript was in the scan window"""
    f = tmp_path / "agent-x.jsonl"
    f.write_text(json.dumps({"timestamp": ts(NOW - 60), "type": "assistant", "message": {"content": content}}) + "\n")
    assert transcript_facts(f, {})[2:4] == (action, frozenset({"b"} if "b.log" in action else ()))


@pytest.mark.parametrize("stamp", ["yesterday", 1_790_000_000, ["2026"], {"t": 1}])
def test_a_last_record_whose_timestamp_is_not_a_time_is_read_past(tmp_path, stamp):
    """its last time is the latest record's that is a time (it raised: a failed frame)"""
    f = tmp_path / "agent-x.jsonl"
    f.write_text(record(NOW - 60, "user") + json.dumps({"timestamp": stamp, "type": "user"}) + "\n")
    assert transcript_facts(f, {})[:2] == (NOW - 60, NOW - 60)


def record(t: float, kind: str, stop_reason=None, content=("text",)) -> str:
    """One transcript record, as Claude Code writes it (only what the facts read)."""
    msg = {"role": kind, "content": [{"type": c, "name": "Bash", "input": {"description": "x"}} if c == "tool_use"
                                     else {"type": c} for c in content]}
    if kind == "assistant":
        msg["stop_reason"] = stop_reason
    return json.dumps({"timestamp": ts(t), "type": kind, "message": msg}) + "\n"


@pytest.mark.parametrize("last, ended", [
    (record(NOW, "assistant", "end_turn"), True),  # the model ended its turn: returned
    (record(NOW, "assistant", "end_turn", ("thinking", "text")), True),
    (record(NOW, "user", content=("tool_result",)), False),  # a tool's result: the model answers next
    (record(NOW, "assistant", None, ("tool_use",)), False),  # a tool call it waits on
    (record(NOW, "assistant", "tool_use", ("tool_use",)), False),
    (record(NOW, "assistant", None, ("text",)), False),  # a block of a message still coming in
    (record(NOW, "assistant", "max_tokens"), False),  # no measured end: the silence rule tells
    (json.dumps({"timestamp": ts(NOW), "type": "attachment"}) + "\n", False),
    (json.dumps({"timestamp": ts(NOW), "type": "assistant", "message": "end_turn"}) + "\n", False),
])
def test_a_transcript_whose_last_record_ends_the_models_turn_has_returned(tmp_path, last, ended):
    f = tmp_path / "agent-x.jsonl"
    f.write_text(record(NOW - 60, "user") + record(NOW - 30, "assistant", "end_turn") + last)
    assert transcript_facts(f)[4] is ended
    # a line that is not a record after it, or half a line still being written, leaves it the last
    f.write_text(f.read_text() + "3\n{\"timestamp\": \"2026")
    assert transcript_facts(f)[4] is ended


DELIVERED = '{"success":true,"message":"Report delivered to your caller."}'


def handback(t: float, call_id: str = "toolu_h") -> str:
    """A background agent handing its report back to its caller, as Claude Code writes the call."""
    return json.dumps({"timestamp": ts(t), "type": "assistant", "message": {"role": "assistant", "stop_reason": None, "content": [
        {"type": "tool_use", "id": call_id, "name": "SubagentHandback",
         "input": {"message": "Report.", "caller": {"type": "direct"}}}]}}) + "\n"


def result(t: float, call_id: str = "toolu_h", text: str = DELIVERED, is_error: bool = False) -> str:
    """A tool call's result, as Claude Code writes it."""
    item = {"tool_use_id": call_id, "type": "tool_result", "content": [{"type": "text", "text": text}]}
    if is_error:
        item["is_error"] = True
    return json.dumps({"timestamp": ts(t), "type": "user", "message": {"role": "user", "content": [item]}}) + "\n"


@pytest.mark.parametrize("ending, ended", [
    # Claude Code 2.1.286 and later: the handback and its result are a background agent's last records
    ([handback(NOW - 1), result(NOW)], True),
    ([handback(NOW)], True),  # the call, its result not written yet
    # 2.1.281: the model ended its turn after them
    ([handback(NOW - 3), result(NOW - 2), json.dumps({"timestamp": ts(NOW - 1), "type": "attachment"}) + "\n",
      record(NOW, "assistant", "end_turn")], True),
    # a record after them: a resumed agent's next prompt, or one the model answers next
    ([handback(NOW - 2), result(NOW - 1), json.dumps({"timestamp": ts(NOW), "type": "user", "message": {
        "role": "user", "content": "Go on: check the second log too."}}) + "\n"], False),
    ([handback(NOW - 2), result(NOW - 1), json.dumps({"timestamp": ts(NOW), "type": "attachment"}) + "\n"], False),
    ([handback(NOW - 3), result(NOW - 2), record(NOW - 1, "assistant", None, ("tool_use",)),
      record(NOW, "user", content=("tool_result",))], False),  # a later tool call: it went on working
    # a result that is an error (the model answers it), or another call's
    ([handback(NOW - 1), result(NOW, text="<tool_use_error>no caller</tool_use_error>", is_error=True)], False),
    ([handback(NOW - 1), result(NOW, call_id="toolu_other")], False),
    ([record(NOW - 1, "assistant", None, ("tool_use",)), result(NOW, call_id="toolu_h")], False),  # no handback
])
def test_a_background_agent_that_handed_its_report_back_has_returned(tmp_path, ending, ended):
    """From Claude Code 2.1.286 on, a background agent's transcript ends with its handback
    (SubagentHandback) and that call's result, and no turn ended after them: it read running for
    silent_stopped_min after it had returned."""
    f = tmp_path / "agent-x.jsonl"
    f.write_text(record(NOW - 60, "user") + record(NOW - 30, "assistant", None, ("tool_use",)) + "".join(ending))
    assert transcript_facts(f, {})[4] is ended


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


# ── a run's recorded end ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", ("killed", "completed", "failed"))
def test_an_attempt_its_run_stopped_reads_failed_at_once(tmp_path, status):
    """The Workflow runtime records a run's end (`<session>/workflows/<run>.json`) as it stops it, and
    the journal says nothing more. An attempt cut off then (its last line 2 s after the record's time)
    read running until it had been silent for `silent_stopped_min`: it reads failed at once. One begun
    after the record (a resumed run's), and one still writing two minutes after it, read as before; so
    does every attempt of a run with no record, with a status that is no end, or with a record that
    does not parse."""
    p = Project(tmp_path)
    p.agent("build:a", start_ago=HOUR, quiet_s=10 * MIN - 2)
    p.agent("review:a", status="done", start_ago=2 * HOUR, quiet_s=HOUR)
    p.agent("fix:a", start_ago=5 * MIN, quiet_s=20)
    p.agent("check:a", start_ago=HOUR, quiet_s=8 * MIN)
    p.run_end("wf_run-a", status, ago_s=10 * MIN)
    p.agent("build:b", start_ago=HOUR, quiet_s=10 * MIN, run="wf_run-b")
    p.agent("build:c", start_ago=HOUR, quiet_s=10 * MIN, run="wf_run-c")
    p.run_end("wf_run-c", "running", ago_s=10 * MIN)
    p.agent("build:d", start_ago=HOUR, quiet_s=10 * MIN, run="wf_run-d")
    p.run_end("wf_run-d", record="{not json")
    p.finish_runs()
    got = {a["label"]: a["status"] for a in scan_agents(p.projects, NOW)}
    assert got == {"build:a": "failed", "review:a": "done", "fix:a": "running", "check:a": "running",
                   "build:b": "running", "build:c": "running", "build:d": "running"}
    # the label reads it as an attempt that died: failed, where the silence rule would wait 15 min more
    assert latest_by_label(scan_agents(p.projects, NOW), NOW, 25 * MIN)["wf_run-b/build:b"]["status"] == "running"
    assert latest_by_label(scan_agents(p.projects, NOW), NOW, 25 * MIN)["build:a"]["status"] == "failed"


def test_a_run_end_with_no_time_is_the_records_own(tmp_path):
    """A record with no readable `timestamp` ended when it was written."""
    p = Project(tmp_path)
    p.agent("build:a", start_ago=HOUR, quiet_s=10 * MIN)
    p.run_end("wf_run-a", record={"status": "killed", "timestamp": "soon"})
    rec = p.projects / "sess-1" / "workflows" / "wf_run-a.json"
    os.utime(rec, (NOW - 10 * MIN, NOW - 10 * MIN))
    p.finish_runs()
    assert [a["status"] for a in scan_agents(p.projects, NOW)] == ["failed"]
    rec.write_text(json.dumps({"status": "killed"}))
    os.utime(rec, (NOW - 20 * MIN, NOW - 20 * MIN))  # written before the attempt went quiet: not its stop
    assert [a["status"] for a in scan_agents(p.projects, NOW)] == ["running"]


def test_agents_at_work_drops_an_agent_its_run_stopped_at_once(tmp_path):
    """midway's run a was stopped 4 min ago: build:startup, last heard from then, leaves Agents at work
    at once; fix:export, writing 40 s ago, long after it, stays (the record does not stop it)."""
    from lsw_mission_control.render.agents import agents_panel

    p = Project(tmp_path)
    midway(p)

    def agents_text() -> str:
        return testing.render_text(agents_panel(p.engine().build_frame(120), 120, []), 120)[0]

    assert "build:startup" in agents_text() and "fix:export" in agents_text()
    p.run_end("wf_run-a", "killed", ago_s=4 * MIN)
    assert "build:startup" not in agents_text() and "fix:export" in agents_text()


@pytest.mark.parametrize("quiet_after_end, stopped", ((60, True), (61, False)))
def test_an_attempt_heard_from_a_minute_after_its_runs_end_is_its_last(tmp_path, quiet_after_end, stopped):
    """The record stops an attempt last heard from up to a minute after the run's recorded end, no later."""
    p = Project(tmp_path)
    p.agent("build:a", start_ago=HOUR, quiet_s=10 * MIN - quiet_after_end)
    p.run_end("wf_run-a", "killed", ago_s=10 * MIN)
    p.finish_runs()
    assert [a["status"] for a in scan_agents(p.projects, NOW)] == ["failed" if stopped else "running"]


def test_a_record_in_another_session_never_stops_a_run(tmp_path):
    """A run's journal in one session and a record of the same run id in another: the record is not its."""
    p = Project(tmp_path)
    p.agent("build:a", start_ago=HOUR, quiet_s=10 * MIN, session="sess-B")
    p.run_end("wf_run-a", "killed", ago_s=10 * MIN, session="sess-A")
    p.finish_runs()
    assert [a["status"] for a in scan_agents(p.projects, NOW)] == ["running"]


# ── agents a session launched itself, outside any workflow ──────────────────────────────────────────
def test_a_sessions_own_agents_are_scanned_from_their_transcripts_and_meta_files(tmp_path):
    """Claude Code keeps them beside the session's workflow runs: `<session>/subagents/agent-<id>.jsonl`
    and its `.meta.json`. The description is the label, the agent type the phase, the session the run;
    running until the transcript's last record ends the model's turn."""
    from lsw_mission_control.agents import scan_session_agents

    p = Project(tmp_path)
    p.session_agent("Diagnose the  flaky\nexport test", agent_type="Explore", start_ago=20 * MIN, quiet_s=40,
                    action="Grep · def export", writes_log="flaky-export", agent_id="a1")
    p.session_agent("Check the docs links", returned=True, agent_id="a2", session="sess-2")
    p.session_agent("A forked conversation", meta=False, agent_id="a3")  # no meta file: not launched as an agent
    p.session_agent("Meta still being written", meta='{"agentType": "Expl', agent_id="a4")
    p.session_agent("Meta not an object", meta=[1, 2], agent_id="a5")
    p.session_agent("", meta={"agentType": None}, agent_id="a6")  # nothing to name it by
    p.agent("build:x", start_ago=10 * MIN, quiet_s=5)  # a workflow's agent: scan_agents gives it, not this
    p.finish_runs()
    got = {a["id"]: a for a in scan_session_agents(p.projects, NOW)}
    assert set(got) == {"a1", "a2", "a6"}
    a1 = got["a1"]
    assert a1["label"] == "Diagnose the flaky export test" and a1["phase"] == "Explore" and a1["status"] == "running"
    assert a1["run"] == "sess-1" and a1["session"] is True and a1["result"] is None and a1["seq"] is None
    assert a1["t0"] == pytest.approx(NOW - 20 * MIN) and a1["t1"] == pytest.approx(NOW - 40)
    assert a1["action"] == "Grep · def export" and a1["logs"] == frozenset({"flaky-export"})
    assert got["a2"]["status"] == "done" and got["a2"]["run"] == "sess-2"
    assert got["a6"]["label"] == "?" and got["a6"]["phase"] == ""
    # an id a workflow's journal gives is listed once, as that workflow's
    assert {a["id"] for a in scan_session_agents(p.projects, NOW, skip={"a1"})} == {"a2", "a6"}


def test_a_sessions_own_agent_outside_the_window_is_not_read(tmp_path):
    from lsw_mission_control import agents as ag
    from lsw_mission_control.agents import scan_session_agents

    p = Project(tmp_path)
    path = p.session_agent("Old errand", start_ago=50 * HOUR, quiet_s=49 * HOUR)
    assert scan_session_agents(p.projects, NOW) == []
    (a,) = scan_session_agents(p.projects, NOW, window_s=50 * HOUR)
    assert a["label"] == "Old errand" and [k for k in ag._session_cache if k.startswith(str(p.projects))] == [str(path)]
    # its cache entry goes when it leaves the window, and the workflows' scan never drops it (nor it theirs)
    p.agent("build:x", start_ago=10 * MIN, quiet_s=5)
    p.finish_runs()
    scan_agents(p.projects, NOW)
    assert str(path) in ag._session_cache and [k for k in ag._ts_cache if k.startswith(str(p.projects))]
    assert scan_session_agents(p.projects, NOW) == []
    assert not [k for k in ag._session_cache if k.startswith(str(p.projects))]
    assert [k for k in ag._ts_cache if k.startswith(str(p.projects))]


def test_a_sessions_own_agents_are_read_from_its_own_project_only(tmp_path):
    """A meta file whose transcript is not written yet is left for the next refresh; a workflow agent's
    meta file (in its run's folder, as Claude Code writes one beside each workflow transcript) is no
    session's agent; and another project's folder is never read, one whose name begins with this
    project's included (a worktree's or a subfolder's sessions)"""
    from lsw_mission_control.agents import scan_session_agents

    p = Project(tmp_path)
    d = p.projects / "sess-1" / "subagents"
    d.mkdir(parents=True)
    (d / "agent-anew.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Just launched"}))
    p.agent("build:x", start_ago=10 * MIN, quiet_s=5, agent_id="awf")
    p.finish_runs()
    run = p.projects / "sess-1" / "subagents" / "workflows" / "wf_run-a"
    (run / "agent-awf.meta.json").write_text(json.dumps({"agentType": "general-purpose", "description": "Build x"}))
    other = Project(tmp_path / "other")
    other.projects = tmp_path / f"{p.projects.name}-backend"
    other.session_agent("Another project's errand", start_ago=5 * MIN, quiet_s=5)
    assert scan_session_agents(p.projects, NOW) == []
    assert [a["label"] for a in scan_session_agents(other.projects, NOW)] == ["Another project's errand"]


def agents_text(p: Project, width: int = 120) -> str:
    from lsw_mission_control.render.agents import agents_panel

    return testing.render_text(agents_panel(p.engine().build_frame(width), width, []), width)[0]


def test_agents_at_work_lists_a_sessions_own_agents_beside_the_workflows(tmp_path):
    """midway's three workflow agents and its session's agent at work, and two more of a session's own
    (two of one description are two agents); one that returned, and one silent past silent_stopped_min
    (cut off), are not at work"""
    p = Project(tmp_path)
    midway(p)
    p.session_agent("Diagnose the flaky export test", agent_type="Explore", start_ago=12 * MIN, quiet_s=5,
                    action="Grep · def export")
    p.session_agent("Diagnose the flaky export test", start_ago=3 * MIN, quiet_s=50, session="sess-2")
    p.session_agent("Check the docs links", returned=True, quiet_s=20)
    p.session_agent("Survey the old benchmarks", start_ago=2 * HOUR, quiet_s=26 * MIN)
    text = agents_text(p)
    rows = [line for line in text.splitlines() if "Diagnose the flaky export test" in line]
    assert len(rows) == 2 and "Explore" in rows[0] and "12m" in rows[0] and "Grep · def export" in rows[0]
    assert "general-purpose" in rows[1] and "50s ago" in rows[1]
    assert "Check the docs links" not in text and "Survey the old benchmarks" not in text
    assert "fix:export" in text and "Find why the export test flakes" in text and "6 running" in text


def test_agents_at_work_lists_a_jobs_own_agents_from_extra_dirs(tmp_path):
    """[agents].extra_dirs: the agents a job launches outside Claude Code (Agent SDK passes billed to the
    API), laid out as Claude Code lays out a session's, are listed with a session's own: by their
    description, "for" their agent type; one that returned is not at work. A folder that does not exist
    reads as empty."""
    p = Project(tmp_path)
    midway(p)
    extra = tmp_path / "api-agents"
    projects, p.projects = p.projects, extra
    p.session_agent("API: backend-services-1", agent_type="Code quality pre-pass (API credits)", start_ago=4 * MIN,
                    quiet_s=10, action="Read · /x/app/services/library.py", session="quality-080")
    p.session_agent("API: backend-api-core", agent_type="Code quality pre-pass (API credits)", returned=True,
                    session="quality-080")
    p.projects = projects
    none = tmp_path / "none"
    p.write_config(f'[agents]\nextra_dirs = ["{extra}", "{none}"]\n')
    text = agents_text(p)
    row = next(line for line in text.splitlines() if "API: backend-services-1" in line)
    assert "Code quality pre-pass (API credits)" in row and "4m" in row and "10s ago" in row and "Read" in row
    assert "API: backend-api-core" not in text


def test_a_background_agent_that_handed_its_report_back_is_not_at_work(tmp_path):
    """Claude Code 2.1.286 and later end a background agent's transcript with its handback's result and
    no turn ended after it (measured on 2.1.286 and 2.1.289, both of them reported completed to their
    sessions). Three minutes after its handback it read running: listed as `SubagentHandback ·`,
    counted in `5 running`, until silent_stopped_min. Resumed (a prompt after it), it is at work again."""
    from lsw_mission_control.agents import scan_session_agents

    p = Project(tmp_path)
    midway(p)
    path = p.session_agent("Summarise the failing imports", start_ago=20 * MIN, quiet_s=3 * MIN, handed_back=True,
                           agent_id="ahb")
    (a,) = [a for a in scan_session_agents(p.projects, NOW) if a["id"] == "ahb"]
    assert a["status"] == "done" and a["action"] == "SubagentHandback · "
    text = agents_text(p)
    assert "Summarise the failing imports" not in text and "SubagentHandback" not in text and "4 running" in text
    with open(path, "a") as fh:
        fh.write(json.dumps({"timestamp": ts(NOW - 30), "type": "user", "message": {
            "role": "user", "content": "Also list the imports that passed."}}) + "\n")
    os.utime(path, (NOW - 30, NOW - 30))
    (a,) = [a for a in scan_session_agents(p.projects, NOW) if a["id"] == "ahb"]
    assert a["status"] == "running"
    row = next(line for line in agents_text(p).splitlines() if "Summarise the failing imports" in line)
    assert "30s ago" in row


def test_a_long_name_never_takes_the_row(tmp_path):
    """the agent column is at most a third of the width, cut with "…": a long description took the whole
    row at 80 columns, leaving its times and what it is doing no room"""
    p = Project(tmp_path)
    midway(p)
    p.session_agent("Audit duplication and abstraction across the render modules and the engine",
                    agent_type="Explore", start_ago=12 * MIN, quiet_s=5, action="Grep · def work_row")
    row = next(line for line in agents_text(p, 80).splitlines() if "Audit" in line)
    assert "Audit duplication and abs…" in row and "12m" in row and "5s ago" in row and "Grep" in row


def test_a_wide_row_shows_more_of_the_item_name(tmp_path):
    """"for" was held at 36 columns whatever the width: a wide window cut every item's name with "…" and
    left the end of the row empty after doing now (owner, 2026-10-25). Doing now keeps its whole text
    where it fits, "for" takes the rest of the row up to its whole name, and never less than 36."""
    from lsw_mission_control.render.agents import FOR_MIN

    p = Project(tmp_path)
    midway(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    name = ("Accessibility audit of every screen: keyboard order, focus rings, the names a screen reader reads "
            "and the contrast of every text")
    next(i for i in plan["items"] if i.get("key") == "a11y")["name"] = name
    p.plan(plan)
    p.agent("build:a11y", start_ago=8 * MIN, quiet_s=14, action="Bash · Read the focus ring rules")

    def row(width):
        return next(line for line in agents_text(p, width).splitlines() if line.startswith("│ build:a11y"))

    assert name in row(240) and "…" not in row(240)  # room for all of it
    wide = row(160)  # the name cut where doing now, whole, ends the row: no room left empty
    assert name[:60] in wide and "…" in wide and wide.rstrip(" │").endswith("Bash · Read the focus ring rules")
    narrow = row(100)
    assert name[:FOR_MIN - 1] + "…" in narrow  # never under FOR_MIN


def test_no_row_reads_a_sessions_own_agent(tmp_path):
    """a session's agent described as a plan label is not that stage's agent: no row reads it (its result
    is not kept, so a review of it would read as finding nothing) and the finished store never keeps it"""
    p = Project(tmp_path)
    midway(p)
    p.session_agent("build:a11y", start_ago=10 * MIN, quiet_s=5)
    p.session_agent("review:startup", returned=True)
    e = p.engine()
    f = e.build_frame(120)
    assert f.labels["build:a11y"]["status"] == "failed" and "review:startup" not in f.labels
    assert {a["label"] for a in f.session_agents} == {"build:a11y", "review:startup", "Find why the export test flakes"}
    assert not [a for a in f.agents if a.get("session")]
    kept = json.loads((p.cache / "finished.json").read_text())["agents"]
    assert not [r for r in kept.values() if r["run"] == "sess-1"]
    # Agents at work still lists it while it runs, for the item its description names
    row = next(line for line in agents_text(p).splitlines() if line.startswith("│ build:a11y"))
    assert "Accessibility audit" in row and "5s ago" in row


def test_a_workflow_agent_is_never_listed_twice(tmp_path):
    """a session's transcript under a workflow agent's id (a conversation forked from it, given a meta
    file) is that workflow agent's: it is listed once, as the workflow's"""
    p = Project(tmp_path)
    midway(p)
    p.session_agent("Continue the fix", start_ago=5 * MIN, quiet_s=3, agent_id="a" + "f" * 16)
    p.agent("fix:dark", start_ago=10 * MIN, quiet_s=4, agent_id="a" + "f" * 16)
    text = agents_text(p)
    assert "fix:dark" in text and "Continue the fix" not in text and "5 running" in text


def test_a_test_log_a_sessions_agent_writes_is_named_after_it(tmp_path):
    from lsw_mission_control.render.agents import agents_panel

    p = Project(tmp_path)
    midway(p)
    p.session_agent("Diagnose the flaky export test", start_ago=12 * MIN, quiet_s=5, writes_log="flaky-export")
    f = p.engine().build_frame(120)
    text = testing.render_text(agents_panel(f, 120, [("flaky-export", "3 failed, 9 passed", NOW - 2 * MIN)]), 120)[0]
    assert "Diagnose the flaky export test  3 failed, 9 passed" in text

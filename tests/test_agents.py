from __future__ import annotations

import json
import os

import pytest

from lsw_mission_control.agents import (
    FinishedStore,
    findings_of,
    label_names,
    latest_by_label,
    review_needs_fix,
    scan_agents,
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
    # (as it always was, the pruned store is saved with the next change, not on its own)
    assert json.loads(path.read_text())["release"] == "1"
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

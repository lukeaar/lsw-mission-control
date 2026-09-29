"""Several attempts of one agent label in one run. The Workflow runtime starts an agent again under
the same label (and key) after an API error, and a resumed run starts its unfinished agents again.
A killed attempt leaves a "failed" event, or no end at all. A label reads its LATEST attempt in a
run, in journal order:

- a later result supersedes an earlier failed or unfinished attempt;
- a later failed attempt after a result is the latest: the stage failed;
- an attempt begun after a result runs again (and a silent one reads as failed, as any does).

The bug they pin: the stage of a label whose retry returned read ✕ "needs rerun". The finished
store still held the killed attempt as running, and the attempts were ordered by their transcripts'
first timestamps, which need not follow the journal (a transcript not written yet, or one that
starts with a line that has no time)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lsw_mission_control import testing
from lsw_mission_control.agents import FinishedStore, label_names, latest_attempts, latest_by_label, scan_agents
from lsw_mission_control.config import FinalMergeCfg
from lsw_mission_control.plan import parse_plan
from lsw_mission_control.progress import stages_progress
from lsw_mission_control.render.agents import agents_panel
from lsw_mission_control.render.other import other_panel

from conftest import NOW
from scenarios import HOUR, MIN, Project, ts

RUN = "wf_retry-a"
LABEL = "measure:cost"
SILENT = 25 * MIN


class Journal:
    """A workflow run written event by event, in the order given, and a transcript per attempt."""

    def __init__(self, projects: Path, run: str = RUN, session: str = "sess-r") -> None:
        self.dir = projects / session / "subagents" / "workflows" / run
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []

    def start(self, aid: str, t0: float | None = None, t1: float | None = None, label: str = LABEL,
              key: str = "k1") -> Journal:
        self.events.append({"type": "started", "key": key, "agentId": aid, "label": label, "phase": "Build"})
        if t0 is not None:
            self.transcript(aid, t0, t0 if t1 is None else t1)
        return self

    def end(self, kind: str, aid: str | None, key: str = "k1", result=None) -> Journal:
        e: dict = {"type": kind, "key": key}
        if aid is not None:
            e["agentId"] = aid
        if kind == "result":
            e["result"] = {"findings": []} if result is None else result
        self.events.append(e)
        return self

    def transcript(self, aid: str, t0: float, t1: float) -> None:
        lines = [{"timestamp": ts(t0), "type": "user", "message": {"role": "user", "content": "go"}},
                 {"timestamp": ts(t1), "type": "assistant", "message": {"content": [
                     {"type": "tool_use", "name": "Bash", "input": {"description": "Price the batch"}}]}}]
        (self.dir / f"agent-{aid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))

    def write(self, upto: int | None = None) -> None:
        """The journal as it stood after its first `upto` events (all of them by default)."""
        events = self.events if upto is None else self.events[:upto]
        (self.dir / "journal.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
        os.utime(self.dir, (NOW - 10 * MIN, NOW - 10 * MIN))


def one_stage(labels: dict, spec: str = LABEL):
    return stages_progress([("measure", spec, 60)], labels, now=NOW, cal=None, default_fix_share=0.7)


def plan_for(label: str = LABEL):
    return parse_plan({"release": "1", "items": [], "other": [{"name": "Cost model", "stages": [["measure", label, 60]]}]})


def through_the_store(projects: Path, store: FinishedStore, label: str = LABEL) -> tuple[list[dict], dict]:
    """What a frame reads: the scan merged with the finished store, then each label's latest attempt."""
    plan = plan_for(label)
    agents = store.merge(scan_agents(projects, NOW), plan, label_names(plan, FinalMergeCfg()))
    return agents, latest_by_label(agents, NOW, SILENT)


# ── a result after failed and unfinished attempts ────────────────────────────────────────────────
# The last attempt's transcript: in order, starting before the killed attempt's (its first line is
# older, or has no time), or not written at all. Journal order decides, whatever its times say.
LAST_T0 = {"in order": NOW - 2.9 * HOUR, "starts earlier": NOW - 5 * HOUR, "no transcript": None}


def failed_stalled_result(projects: Path, last_t0: float | None) -> Journal:
    j = Journal(projects)
    j.start("a1", NOW - 4 * HOUR, NOW - 3.9 * HOUR).end("failed", "a1")
    j.start("a2", NOW - 3.8 * HOUR, NOW - 3 * HOUR)  # killed with no end event: retried below
    j.start("a3", last_t0, NOW - HOUR).end("result", "a3")
    return j


@pytest.mark.parametrize("last", list(LAST_T0))
def test_a_result_supersedes_a_failed_and_an_unfinished_attempt(tmp_path, last):
    failed_stalled_result(tmp_path, LAST_T0[last]).write()
    agents = scan_agents(tmp_path, NOW)
    by_id = {a["id"]: a for a in agents}
    # every attempt is kept; the unfinished one is over, since its key started again
    assert {k: a["status"] for k, a in by_id.items()} == {"a1": "failed", "a2": "failed", "a3": "done"}
    assert [by_id[k]["seq"] for k in ("a1", "a2", "a3")] == [0, 2, 3]
    labels = latest_by_label(agents, NOW, SILENT)
    assert labels[LABEL]["id"] == "a3" and labels[f"{RUN}/{LABEL}"]["id"] == "a3"
    p = one_stage(labels)
    assert p.current == "done" and not p.failed


@pytest.mark.parametrize("last", list(LAST_T0))
def test_the_store_never_brings_a_killed_attempt_back_over_its_retry(tmp_path, last):
    """The live shape: the store recorded the killed attempt as running while it was the latest;
    after its retry returned, that record must not win over the retry."""
    j = failed_stalled_result(tmp_path, LAST_T0[last])
    store = FinishedStore(tmp_path / "finished.json")
    j.write(upto=3)  # a1 failed, a2 running
    _agents, labels = through_the_store(tmp_path, store)
    assert labels[LABEL]["id"] == "a2"
    saved = json.loads((tmp_path / "finished.json").read_text())["agents"]
    assert saved[f"{RUN}/a2"]["status"] == "running" and saved[f"{RUN}/a2"]["seq"] == 2
    j.write()  # a3 returned
    _agents, labels = through_the_store(tmp_path, store)
    assert labels[LABEL]["id"] == "a3" and labels[LABEL]["status"] == "done"
    assert json.loads((tmp_path / "finished.json").read_text())["agents"][f"{RUN}/a2"]["status"] == "failed"
    # the journal leaves the scan window: the store alone still orders the attempts by the journal
    os.utime(j.dir, (NOW - 50 * HOUR, NOW - 50 * HOUR))
    agents, labels = through_the_store(tmp_path, store)
    assert scan_agents(tmp_path, NOW) == [] and {a["id"] for a in agents} == {"a1", "a2", "a3"}
    assert labels[LABEL]["id"] == "a3" and one_stage(labels).current == "done"


def test_a_store_record_without_a_journal_place_falls_back_to_start_times(tmp_path):
    """a store written before the journal order was kept: its records are ordered by their starts,
    as they always were"""
    old = {"id": "a2", "label": LABEL, "phase": "Build", "status": "running", "run": RUN, "t0": NOW - 3.8 * HOUR,
           "t1": NOW - 3 * HOUR, "result": {"findings": []}}
    new = dict(old, id="a3", status="done", t0=NOW - 2.9 * HOUR, t1=NOW - HOUR)
    (tmp_path / "finished.json").write_text(json.dumps({"release": "1", "agents": {f"{RUN}/a2": old, f"{RUN}/a3": new}}))
    _agents, labels = through_the_store(tmp_path, FinishedStore(tmp_path / "finished.json", readonly=True))
    assert labels[LABEL]["id"] == "a3" and labels[LABEL]["status"] == "done"


# ── after a result: a later failure, a later start ───────────────────────────────────────────────
def test_a_failed_attempt_after_a_result_is_the_latest(tmp_path):
    j = Journal(tmp_path)
    j.start("a1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "a1")
    j.start("a2", NOW - 50 * MIN, NOW - 40 * MIN).end("failed", "a2")
    j.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[LABEL]["id"] == "a2" and labels[LABEL]["status"] == "failed"
    p = one_stage(labels)
    assert p.failed and p.current == "measure failed"


@pytest.mark.parametrize("quiet, status", [(30.0, "running"), (SILENT + MIN, "failed")])
def test_an_attempt_begun_after_a_result_runs_again(tmp_path, quiet, status):
    j = Journal(tmp_path)
    j.start("a1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "a1")
    j.start("a2", NOW - 50 * MIN, NOW - quiet)
    j.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[LABEL]["id"] == "a2" and labels[LABEL]["status"] == status
    assert one_stage(labels).current == ("measure" if status == "running" else "measure failed")


# ── which attempt an end event ends ─────────────────────────────────────────────────────────────
def test_an_end_event_ends_the_attempt_it_names(tmp_path):
    """an earlier attempt's end, written after its retry started, never ends the retry"""
    j = Journal(tmp_path)
    j.start("a1", NOW - 2 * HOUR, NOW - HOUR)
    j.start("a2", NOW - HOUR, NOW - 30)
    j.end("failed", "a1")
    j.write()
    by_id = {a["id"]: a for a in scan_agents(tmp_path, NOW)}
    assert by_id["a1"]["status"] == "failed" and by_id["a2"]["status"] == "running"
    j.end("result", "a2")
    j.write()
    assert latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)[LABEL]["status"] == "done"


def test_an_end_event_that_names_no_attempt_ends_its_keys_latest(tmp_path):
    """no agentId (or one the journal never started): the latest attempt of its key, as before"""
    j = Journal(tmp_path)
    j.start("a1", NOW - 2 * HOUR, NOW - HOUR).start("a2", NOW - HOUR, NOW - 30).end("result", None)
    j.start("b1", NOW - 20 * MIN, NOW - 30, label="review:cost", key="k2").end("failed", "b-unknown", key="k2")
    j.write()
    by_id = {a["id"]: a for a in scan_agents(tmp_path, NOW)}
    assert (by_id["a1"]["status"], by_id["a2"]["status"], by_id["b1"]["status"]) == ("failed", "done", "failed")


def test_attempts_of_other_keys_are_not_retries(tmp_path):
    """one label called twice by the script (two keys): the later call's attempt is the latest, and
    the earlier call's attempt is not ended by it"""
    j = Journal(tmp_path)
    j.start("a1", NOW - 2 * HOUR, NOW - 30, key="k1")
    j.start("a2", NOW - HOUR, NOW - 20, key="k2")
    j.write()
    agents = scan_agents(tmp_path, NOW)
    assert {a["id"]: a["status"] for a in agents} == {"a1": "running", "a2": "running"}
    assert latest_by_label(agents, NOW, SILENT)[LABEL]["id"] == "a2"


# ── across runs ─────────────────────────────────────────────────────────────────────────────────
def test_run_qualified_labels_read_their_own_runs_latest_attempt(tmp_path):
    a = Journal(tmp_path, run="wf_retry-a")
    a.start("a1", NOW - 6 * HOUR, NOW - 5.5 * HOUR).end("failed", "a1")
    a.start("a2", NOW - 5.4 * HOUR, NOW - 5 * HOUR).end("result", "a2")
    a.write()
    b = Journal(tmp_path, run="wf_retry-b")
    b.start("b1", NOW - 2 * HOUR, NOW - HOUR).end("result", "b1")
    b.start("b2", NOW - 3 * HOUR, NOW - 2 * MIN).end("failed", "b2")  # a retry whose transcript starts earlier
    b.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[f"wf_retry-a/{LABEL}"]["id"] == "a2" and labels[f"wf_retry-a/{LABEL}"]["status"] == "done"
    assert labels[f"wf_retry-b/{LABEL}"]["id"] == "b2" and labels[f"wf_retry-b/{LABEL}"]["status"] == "failed"
    # the bare label: the run whose attempts began last, and that run's latest attempt
    assert labels[LABEL]["id"] == "b2"
    assert one_stage(labels, f"wf_retry-a/{LABEL}").current == "done"
    assert one_stage(labels, f"wf_retry-b/{LABEL}").failed and one_stage(labels).failed


def test_latest_attempts_orders_by_journal_within_a_run_and_by_start_across_runs():
    def rec(aid, run, t0, seq=None):
        r = {"id": aid, "label": LABEL, "run": run, "status": "done", "t0": t0, "t1": t0}
        return r if seq is None else dict(r, seq=seq)

    early_start_late_seq = rec("x2", "wf_1", NOW - HOUR, seq=5)
    got = latest_attempts([rec("x1", "wf_1", NOW, seq=1), early_start_late_seq, rec("y1", "wf_2", NOW - 2 * HOUR, seq=9)])
    assert got == {("wf_1", LABEL): early_start_late_seq, ("wf_2", LABEL): rec("y1", "wf_2", NOW - 2 * HOUR, seq=9)}
    # a record that does not know its place (an older store's, or a bad one) is ordered by its start
    for bad in (None, True, "3", 2.0):
        old = dict(rec("x0", "wf_1", NOW + 1), seq=bad)
        assert latest_attempts([early_start_late_seq, old])[("wf_1", LABEL)] is old


# ── the frame ───────────────────────────────────────────────────────────────────────────────────
def cost_row(p: Project) -> str:
    plain = testing.render_text(other_panel(p.engine().build_frame(120), 120), 120)[0]
    return next(line for line in plain.splitlines() if line.startswith("│ Cost model"))


def test_the_row_of_a_label_whose_retry_returned_reads_done(tmp_path):
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [
        {"name": "Cost model", "stages": [["measure", LABEL, 60], ["report", "report:cost", 20]]}]})
    j = failed_stalled_result(p.projects, NOW - 5 * HOUR)  # the retry's transcript starts earliest
    j.write(upto=3)
    cost_row(p)  # a frame while a2 was the latest: the store records it as running
    assert json.loads((p.cache / "finished.json").read_text())["agents"][f"{RUN}/a2"]["status"] == "running"
    j.write()
    row = cost_row(p)
    assert "●─○" in row and "queued" in row and "needs rerun" not in row  # measure done, report next
    # and a failed retry after that result does need a re-run
    j.start("a4", NOW - 40 * MIN, NOW - 30 * MIN).end("failed", "a4")
    j.write()
    assert "needs rerun" in cost_row(p)


def test_agents_at_work_lists_only_the_latest_attempt(tmp_path):
    """the attempt a retry replaced is not at work, even when it wrote a minute ago"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [{"name": "Cost model", "stages": [["measure", LABEL, 60]]}]})
    j = Journal(p.projects)
    j.start("a1", NOW - 30 * MIN, NOW - 60).start("a2", NOW - 40 * MIN, NOW - 20)  # a2's transcript starts earlier
    j.write()
    f = p.engine().build_frame(120)
    plain = testing.render_text(agents_panel(f, 120, []), 120)[0]
    assert "1 running" in plain and plain.count(LABEL) == 1
    assert [a["id"] for a in f.agents if a["status"] == "running"] == ["a2"]


def test_agents_at_work_lists_one_row_for_two_keys_of_one_label_both_running(tmp_path):
    """two keys under one label, both running in one run: the panel lists the latest attempt once"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [{"name": "Cost model", "stages": [["measure", LABEL, 60]]}]})
    j = Journal(p.projects)
    j.start("a1", NOW - 30 * MIN, NOW - 60, key="k1").start("a2", NOW - 40 * MIN, NOW - 20, key="k2")
    j.write()
    f = p.engine().build_frame(120)
    assert sorted(a["id"] for a in f.agents if a["status"] == "running") == ["a1", "a2"]
    plain = testing.render_text(agents_panel(f, 120, []), 120)[0]
    assert plain.count(LABEL) == 1

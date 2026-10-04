"""Several attempts of one agent label. The Workflow runtime starts an agent again under the same
label (and key) after an API error, and a resumed run starts its unfinished agents again. A killed
attempt leaves a "failed" event, or no end at all. A label reads its LATEST attempt in a run, in
journal order:

- a later result supersedes an earlier failed or unfinished attempt, and an earlier result;
- an attempt begun after a result runs again;
- an attempt that died (a "failed" or "error" event, or silent past the stop limit: its workflow
  stopped) never undoes a result or an attempt still running: the label reads the latest of those,
  and fails only when it has none;
- the attempt that died rides along on that earlier one, and a stage reads it where the earlier one
  no longer stands: in a held item (the owner stopped it), and for a result older than an earlier
  stage's re-run when the death came after that re-run (the stage ran again and died).

The bugs they pin: the stage of a label whose retry returned read ✕ "needs rerun". The finished
store still held the killed attempt as running, and the attempts were ordered by their transcripts'
first timestamps, which need not follow the journal (a transcript not written yet, or one that
starts with a line that has no time). Then two network outages killed the retries of agents that
had already returned, and their stages read ✕ "needs rerun" again: a workflow agent fails only when
it dies (a terminal API error after the runtime's retries, or a skip), never as a verdict on its
work, so its death says nothing about the result before it."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lsw_mission_control import testing
from lsw_mission_control.agents import (
    FinishedStore,
    died_after,
    final_merge_labels,
    label_names,
    latest_attempts,
    latest_by_label,
    review_needs_fix,
    scan_agents,
)
from lsw_mission_control.config import FinalMergeCfg
from lsw_mission_control.plan import parse_plan
from lsw_mission_control.progress import stages_progress
from lsw_mission_control.render.agents import agents_panel
from lsw_mission_control.render.next_release import next_panel
from lsw_mission_control.render.other import other_panel
from lsw_mission_control.render.release import release_panel

from conftest import NOW
from scenarios import HOUR, MIN, STUB_PLUGIN, Project, ts

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


# ── after a result: a later start, a later result, an attempt that died ─────────────────────────
def died(j: Journal, aid: str, how: str, t0: float | None, t1: float, label: str = LABEL, key: str = "k1") -> Journal:
    """An attempt that died: a "failed" or "error" event, or no end at all and silent past the stop
    limit (a stopped workflow writes nothing)."""
    j.start(aid, t0, t1, label=label, key=key)
    return j if how == "silent" else j.end(how, aid, key=key)


HOW = ["failed", "error", "silent"]


@pytest.mark.parametrize("how", HOW)
@pytest.mark.parametrize("retry_t0", [NOW - 50 * MIN, NOW - 5 * HOUR, None], ids=["in order", "starts earlier", "no time"])
def test_an_attempt_that_died_after_a_result_never_undoes_it(tmp_path, how, retry_t0):
    """the outages' shape: the fix returned, the resumed run started it again, and that retry died.
    The death is no verdict on the work: the stage is done, as it was."""
    j = Journal(tmp_path)
    j.start("a1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "a1")
    died(j, "a2", how, retry_t0, NOW - 40 * MIN)
    j.write()
    agents = scan_agents(tmp_path, NOW)
    assert {a["id"]: a["status"] for a in agents} == {"a1": "done", "a2": "running" if how == "silent" else "failed"}
    labels = latest_by_label(agents, NOW, SILENT)
    assert labels[LABEL]["id"] == "a1" and labels[f"{RUN}/{LABEL}"]["id"] == "a1"
    assert labels[LABEL]["status"] == "done"
    p = one_stage(labels)
    assert p.current == "done" and not p.failed


def test_an_attempt_begun_after_a_result_runs_again(tmp_path):
    j = Journal(tmp_path)
    j.start("a1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "a1")
    j.start("a2", NOW - 50 * MIN, NOW - 30)
    j.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[LABEL]["id"] == "a2" and labels[LABEL]["status"] == "running"
    assert one_stage(labels).current == "measure"


def test_a_later_result_still_replaces_an_earlier_one(tmp_path):
    """the latest result is the one read, whatever died after it: its findings decide the fix"""
    j = Journal(tmp_path)
    j.start("r1", NOW - 4 * HOUR, NOW - 3.5 * HOUR, label="review:cost").end("result", "r1", result={"findings": [{"severity": "major"}]})
    j.start("r2", NOW - 3 * HOUR, NOW - 2.5 * HOUR, label="review:cost").end("result", "r2", result={"findings": [{"severity": "nit"}]})
    died(j, "r3", "failed", NOW - HOUR, NOW - 55 * MIN, label="review:cost")
    j.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels["review:cost"]["id"] == "r2" and review_needs_fix(labels["review:cost"]) is False
    p = stages_progress([("review", "review:cost", 20), ("fix", "fix:cost", 30)], labels, now=NOW, cal=None,
                        default_fix_share=0.7)
    assert p.current == "done" and [m[0] for m in p.marks] == ["●", "–"]  # a nit needs no fix


def test_a_label_whose_attempts_all_died_needs_a_rerun(tmp_path):
    """unchanged: no result and nothing running, the stage failed"""
    j = Journal(tmp_path)
    died(j, "a1", "failed", NOW - 3 * HOUR, NOW - 2.9 * HOUR)
    died(j, "a2", "error", NOW - 2 * HOUR, NOW - 1.9 * HOUR)
    died(j, "a3", "silent", NOW - HOUR, NOW - SILENT - MIN)
    j.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[LABEL]["id"] == "a3" and labels[LABEL]["status"] == "failed"
    p = one_stage(labels)
    assert p.failed and p.current == "measure failed"


def test_an_attempt_that_died_never_undoes_one_still_running(tmp_path):
    """one label called twice by the script (two keys): the later call died, the earlier still runs"""
    j = Journal(tmp_path)
    j.start("a1", NOW - 2 * HOUR, NOW - 30, key="k1")
    died(j, "a2", "failed", NOW - HOUR, NOW - 50 * MIN, key="k2")
    j.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[LABEL]["id"] == "a1" and labels[LABEL]["status"] == "running"
    assert one_stage(labels).current == "measure"


def test_the_store_keeps_the_result_a_died_retry_did_not_undo(tmp_path):
    """the journal leaves the scan window: the store alone still reads the result"""
    j = Journal(tmp_path)
    j.start("a1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "a1")
    died(j, "a2", "failed", NOW - 50 * MIN, NOW - 40 * MIN)
    j.write()
    store = FinishedStore(tmp_path / "finished.json")
    _agents, labels = through_the_store(tmp_path, store)
    assert labels[LABEL]["id"] == "a1"
    os.utime(j.dir, (NOW - 50 * HOUR, NOW - 50 * HOUR))
    agents, labels = through_the_store(tmp_path, store)
    assert scan_agents(tmp_path, NOW) == [] and {a["id"] for a in agents} == {"a1", "a2"}
    assert labels[LABEL]["id"] == "a1" and one_stage(labels).current == "done"


def test_an_earlier_releases_final_merge_never_stands_in_for_this_ones(tmp_path):
    """the final merge's labels are every release's: the shipped release's result is no row's, so this
    release's merge whose only attempt died still needs a re-run"""
    path = tmp_path / "finished.json"
    path.write_text(json.dumps({"release": "2", "since": NOW - 5 * HOUR, "agents": {}}))
    plan = parse_plan({"release": "2", "items": []})
    shipped = {"id": "m1", "label": "build:final-merge", "phase": "", "status": "done", "run": "wf_merge-1",
               "t0": NOW - 9 * HOUR, "t1": NOW - 8 * HOUR, "result": None, "action": "", "logs": frozenset()}
    this = dict(shipped, id="m2", status="failed", run="wf_merge-2", t0=NOW - HOUR, t1=NOW - 50 * MIN)
    agents = FinishedStore(path).merge([shipped, this], plan, label_names(plan, FinalMergeCfg()),
                                       release_bound=final_merge_labels(FinalMergeCfg()))
    labels = latest_by_label(agents, NOW, SILENT)
    assert labels["build:final-merge"]["id"] == "m2" and labels["build:final-merge"]["status"] == "failed"


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
    b.start("b1", NOW - 2 * HOUR, NOW - HOUR).end("failed", "b1")
    b.start("b2", NOW - 3 * HOUR, NOW - 2 * MIN).end("failed", "b2")  # a retry whose transcript starts earlier
    b.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[f"wf_retry-a/{LABEL}"]["id"] == "a2" and labels[f"wf_retry-a/{LABEL}"]["status"] == "done"
    assert labels[f"wf_retry-b/{LABEL}"]["id"] == "b2" and labels[f"wf_retry-b/{LABEL}"]["status"] == "failed"
    assert one_stage(labels, f"wf_retry-a/{LABEL}").current == "done"
    assert one_stage(labels, f"wf_retry-b/{LABEL}").failed  # its own run has no result: it failed
    # the bare label: every attempt of the run that began last died, so the earlier run's result stands
    assert labels[LABEL]["id"] == "a2" and one_stage(labels).current == "done"


def test_a_bare_label_reads_the_run_whose_attempts_began_last(tmp_path):
    a = Journal(tmp_path, run="wf_retry-a")
    a.start("a1", NOW - 6 * HOUR, NOW - 5 * HOUR).end("result", "a1")
    a.write()
    b = Journal(tmp_path, run="wf_retry-b")
    b.start("b1", NOW - 2 * HOUR, NOW - HOUR).end("result", "b1")
    b.start("b2", NOW - 3 * HOUR, NOW - 30)  # running again; its transcript starts earlier
    b.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[LABEL]["id"] == "b2" and labels[LABEL]["status"] == "running"
    assert labels[f"wf_retry-a/{LABEL}"]["id"] == "a1" and labels[f"wf_retry-b/{LABEL}"]["id"] == "b2"


def test_a_bare_label_whose_last_run_died_reads_the_latest_attempt_that_did_not(tmp_path):
    """the run that began last ended in an attempt that died: the label reads the latest attempt of
    any run that returned or still runs (a newer result over an older one, an attempt begun after a
    result over that result)"""
    a = Journal(tmp_path, run="wf_retry-a")
    a.start("a1", NOW - 6 * HOUR, NOW - 5.5 * HOUR).end("result", "a1")
    died(a, "a2", "failed", NOW - HOUR, NOW - 50 * MIN)  # run a began last
    a.write()
    b = Journal(tmp_path, run="wf_retry-b")
    b.start("b1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "b1")
    b.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[f"wf_retry-a/{LABEL}"]["id"] == "a1"  # its own run: the result a2's death did not undo
    assert labels[f"wf_retry-b/{LABEL}"]["id"] == "b1"
    assert labels[LABEL]["id"] == "b1"  # the newest result of any run, not run a's older one
    c = Journal(tmp_path, run="wf_retry-c")
    c.start("c1", NOW - 1.5 * HOUR, NOW - 30)  # begun after b1 and still running
    c.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    assert labels[LABEL]["id"] == "c1" and labels[LABEL]["status"] == "running"
    assert one_stage(labels).current == "measure"


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
    # a retry that died after that result does not undo it; one that runs, runs
    j.start("a4", NOW - 40 * MIN, NOW - 30 * MIN).end("failed", "a4")
    j.write()
    assert cost_row(p) == row
    j.start("a5", NOW - 20 * MIN, NOW - 30)
    j.write()
    row = cost_row(p)
    assert "◉─○" in row and "measure" in row and "needs rerun" not in row


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


def test_agents_at_work_lists_an_attempt_still_running_when_a_later_one_died(tmp_path):
    """two keys under one label in one run: the later call died, the earlier still works. Its row
    reads running (an attempt that died undoes nothing), and Agents at work lists the one at work"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [{"name": "Cost model", "stages": [["measure", LABEL, 60]]}]})
    j = Journal(p.projects)
    j.start("a1", NOW - 30 * MIN, NOW - 60, key="k1")
    died(j, "a2", "failed", NOW - 20 * MIN, NOW - 15 * MIN, key="k2")
    j.write()
    plain = testing.render_text(agents_panel(p.engine().build_frame(120), 120, []), 120)[0]
    assert "1 running" in plain and plain.count(LABEL) == 1
    row = cost_row(p)
    assert "◉" in row and "measure" in row and "needs rerun" not in row


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


# ── the frame, after the outages: died retries, and stages whose agents run again ──────────────────
def row_of(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def release_text(p: Project) -> str:
    return testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)[0]


def search_index(p: Project, *, outage: bool) -> None:
    """A release item built, reviewed (a minor finding) and fixed; in the outage the resumed run
    started the fix again and that retry died."""
    p.plan({"release": "1.0.0", "items": [{"name": "Search index", "key": "index", "build": 60, "review": 20, "fix": 30}]})
    p.notes()
    j = Journal(p.projects, run="wf_index")
    j.start("b1", NOW - 5 * HOUR, NOW - 4 * HOUR, label="build:index", key="kb").end("result", "b1", key="kb")
    j.start("r1", NOW - 4 * HOUR, NOW - 3.5 * HOUR, label="review:index", key="kr").end(
        "result", "r1", key="kr", result={"findings": [{"severity": "minor"}]})
    j.start("f1", NOW - 3.5 * HOUR, NOW - 3 * HOUR, label="fix:index", key="kf").end("result", "f1", key="kf")
    if outage:
        died(j, "f2", "failed", NOW - HOUR, NOW - 55 * MIN, label="fix:index", key="kf")
    j.write()


def test_release_an_item_whose_fix_returned_is_done_whatever_died_after(tmp_path):
    """it read "fix failed · needs rerun", counted as failed, and the release's finish counted a re-run
    of the fix: now it is done, and the panel is the one a release with no outage draws"""
    clean, outage = Project(tmp_path / "clean"), Project(tmp_path / "outage")
    search_index(clean, outage=False)
    search_index(outage, outage=True)
    plain = release_text(outage)
    assert "1/1 items ready" in plain and "● 1 finished" in plain
    assert "failed" not in plain and "needs rerun" not in plain
    assert plain == release_text(clean)


FACTS = ["research-code:export", "research-data:export", "questions:export", "critic:export"]


def facts_then_work(p: Project, running: bool = True) -> None:
    """A "before" stage of four agents: the outage killed all four in the old run; the new run's two
    research agents run now (`running`), and it runs the other two after them."""
    p.plan({"release": "1.0.0", "items": [
        {"name": "Export to CSV", "key": "export", "build": 240, "review": 60, "fix": 60,
         "before": [["facts", FACTS, 90], ["your answers", None, 0]]},
        {"name": "Import from CSV", "key": "import", "build": 480, "review": 120, "fix": 120, "flags": ["after:export"]}]})
    p.notes()
    old = Journal(p.projects, run="wf_old")
    for i, label in enumerate(FACTS):
        died(old, f"o{i}", "failed", NOW - 3 * HOUR + i * MIN, NOW - 2.9 * HOUR, label=label, key=f"k{i}")
    old.write()
    if running:
        new = Journal(p.projects, run="wf_new")
        for i, label in enumerate(FACTS[:2]):
            new.start(f"n{i}", NOW - 20 * MIN, NOW - 30, label=label, key=f"k{i}")
        new.write()


def test_release_a_stage_runs_while_one_of_its_agents_runs(tmp_path):
    """it read "facts failed · needs rerun" while two of its agents worked. Running, its time left is
    its 90 min less the 20 its running agents have had (the died attempts' older starts are not its
    run's: no overrun), then build 240 + review 60 + fix 60 x 0.7; what runs after it waits that long."""
    p = Project(tmp_path)
    facts_then_work(p)
    plain = release_text(p)
    export = row_of(plain, "Export to CSV")
    assert "◉─○─○─○─○" in export and "facts " in export and "facts +" not in export
    assert "~6h52 · 21:05" in export and "needs rerun" not in export
    assert "failed" not in plain
    imports = row_of(plain, "Import from CSV")
    assert "after export" in imports and "~18h16" in imports  # 412 min, then 480 + 120 + 120 x 0.7


def test_release_a_stage_whose_agents_all_died_still_needs_a_rerun(tmp_path):
    """unchanged: the same stage with nothing running reads failed, and counts its re-run"""
    p = Project(tmp_path)
    facts_then_work(p, running=False)
    plain = release_text(p)
    export = row_of(plain, "Export to CSV")
    assert "✕─○─○─○─○" in export and "needs rerun" in export and "1 failed" in plain


def other_text(p: Project) -> str:
    return testing.render_text(other_panel(p.engine().build_frame(120), 120), 120)[0]


def test_other_a_stage_runs_while_one_of_its_agents_runs(tmp_path):
    """build running in a new run, review queued after it, both killed in the old run: running, with
    its 90 min less the 20 run, then 45 min (it read "needs rerun", and the panel "1 failed")"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [
        {"name": "Theme refresh", "stages": [["palette", ["build:theme", "review:theme"], 90], ["land", "fix:theme", 45]]}]})
    p.notes()
    old = Journal(p.projects, run="wf_old")
    died(old, "o1", "failed", NOW - 3 * HOUR, NOW - 2.9 * HOUR, label="build:theme", key="kb")
    died(old, "o2", "failed", NOW - 2.9 * HOUR, NOW - 2.8 * HOUR, label="review:theme", key="kr")
    old.write()
    new = Journal(p.projects, run="wf_new")
    new.start("n1", NOW - 20 * MIN, NOW - 30, label="build:theme", key="kb")
    new.write()
    plain = other_text(p)
    theme = row_of(plain, "Theme refresh")
    assert "◉─○" in theme and "palette" in theme and "~1h55 · 16:08" in theme
    assert "needs rerun" not in plain and "failed" not in plain and "next to finish: Theme refresh" in plain


def next_text(p: Project) -> str:
    return testing.render_text(next_panel(p.engine().build_frame(120), 120), 120)[0]


def test_next_rows_after_the_outage(tmp_path):
    """Search index: its fix returned, then its retry died (it read "fix failed · needs rerun"). New
    icon set: its review returned, then the review's retry and its fix died, and a new run runs the
    fix again (it read "fix · needs rerun"). Benchmarks: every attempt of its measurement died and
    nothing of it runs, so it still needs a re-run."""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "next": {"release": "1.1.0", "items": [
        {"key": "index", "name": "Search index", "build": 180, "review": 40, "fix": 40},
        {"key": "icons", "name": "New icon set", "build": 60, "review": 20, "fix": 20},
        {"key": "bench", "name": "Benchmarks", "before": [["measure", ["measure:cold", "measure:warm"], 480]],
         "build": 60}]}})
    p.notes()
    old = Journal(p.projects, run="wf_old")
    for kind in ("build", "review"):
        old.start(f"{kind}-i0", NOW - 30 * HOUR, NOW - 29 * HOUR, label=f"{kind}:index", key=f"{kind}-i").end(
            "result", f"{kind}-i0", key=f"{kind}-i")
    old.start("build-n0", NOW - 30 * HOUR, NOW - 29 * HOUR, label="build:icons", key="bn").end("result", "build-n0", key="bn")
    died(old, "fix-i0", "failed", NOW - 28 * HOUR, NOW - 27.9 * HOUR, label="fix:index", key="fi")
    died(old, "cold-0", "silent", NOW - 27 * HOUR, NOW - 26 * HOUR, label="measure:cold", key="mc")
    old.write()
    resumed = Journal(p.projects, run="wf_resumed")
    resumed.start("fix-i1", NOW - 4.5 * HOUR, NOW - 4 * HOUR, label="fix:index", key="fi").end("result", "fix-i1", key="fi")
    resumed.start("review-n1", NOW - 4.5 * HOUR, NOW - 3.8 * HOUR, label="review:icons", key="rn").end(
        "result", "review-n1", key="rn", result={"findings": [{"severity": "minor"}]})
    died(resumed, "fix-n1", "failed", NOW - 3.6 * HOUR, NOW - 3.5 * HOUR, label="fix:icons", key="fn")
    died(resumed, "cold-1", "failed", NOW - 3.4 * HOUR, NOW - 3.3 * HOUR, label="measure:cold", key="mc")
    died(resumed, "fix-i2", "failed", NOW - 2.9 * HOUR, NOW - 2.8 * HOUR, label="fix:index", key="fi")
    died(resumed, "review-n2", "failed", NOW - 2.9 * HOUR, NOW - 2.8 * HOUR, label="review:icons", key="rn")
    resumed.write()
    again = Journal(p.projects, run="wf_again")
    again.start("fix-n2", NOW - 20 * MIN, NOW - 30, label="fix:icons", key="fn")
    again.write()
    plain = next_text(p)
    icons, bench = row_of(plain, "New icon set"), row_of(plain, "Benchmarks")
    # Search index is done: the panel's one finished item, with no row of its own
    assert "Search index" not in plain and "● 1 finished" in plain and "1 done" in plain
    assert "●─●─◉" in icons and "fix" in icons and "needs rerun" not in icons and "~10m · 14:23" in icons
    assert "✕─○" in bench and "needs rerun" in bench and plain.count("needs rerun") == 1


# ── a died attempt after an earlier stage ran again; a held item's stopped re-run; a running stage's time ──
# The label reads the earlier attempt, carrying the one that died (died_after): a stage reads that
# one where the earlier one no longer stands (progress._stage_attempt).
BRF = [("build", "build:x", 60), ("review", "review:x", 20), ("fix", "fix:x", 30)]
MAJOR = {"findings": [{"severity": "major"}]}


def two_rounds(projects: Path, *, second_fix: str = "failed", one_run: bool = True) -> None:
    """build, review (a major finding) and fix all returned; a second review returned (major again);
    the second fix then died ("failed") or went silent ("silent"), and nothing of the item runs."""
    r1 = Journal(projects, run="wf_round1")
    r1.start("b1", NOW - 5 * HOUR, NOW - 4.5 * HOUR, label="build:x", key="kb").end("result", "b1", key="kb")
    r1.start("r1", NOW - 4.4 * HOUR, NOW - 4 * HOUR, label="review:x", key="kr1").end("result", "r1", key="kr1", result=MAJOR)
    r1.start("f1", NOW - 3.9 * HOUR, NOW - 3.5 * HOUR, label="fix:x", key="kf1").end("result", "f1", key="kf1")
    r2 = r1 if one_run else Journal(projects, run="wf_round2")
    r2.start("r2", NOW - 3 * HOUR, NOW - 2.5 * HOUR, label="review:x", key="kr2").end("result", "r2", key="kr2", result=MAJOR)
    died(r2, "f2", second_fix, NOW - 2 * HOUR, NOW - 1.9 * HOUR, label="fix:x", key="kf2")
    r1.write()
    if r2 is not r1:
        r2.write()


@pytest.mark.parametrize("how", ["failed", "silent"])
@pytest.mark.parametrize("one_run", [True, False], ids=["one run", "two runs"])
def test_a_fix_whose_second_round_died_after_a_rereview_needs_a_rerun(tmp_path, one_run, how):
    """the fix's only attempt since the review ran again died, and nothing runs: it needs a re-run.
    Its result from before that review is stale, and alone it read "queued", with no failure counted"""
    two_rounds(tmp_path, second_fix=how, one_run=one_run)
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    p = stages_progress(BRF, labels, now=NOW, cal=None, default_fix_share=0.7)
    assert [m[0] for m in p.marks] == ["●", "●", "✕"], (p.marks, p.current)
    assert p.failed and p.current == "fix failed"


def test_the_release_row_and_header_count_that_died_fix(tmp_path):
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [{"name": "Search index", "key": "x", "build": 60, "review": 20, "fix": 30}]})
    p.notes()
    two_rounds(p.projects)
    plain = release_text(p)
    row = row_of(plain, "Search index")
    assert "●─●─✕" in row and "needs rerun" in row and "1 failed" in plain, row


def check_stage(projects: Path, *, check_b_runs: bool) -> None:
    """check-a returned; measure ran again; check-a's attempt after that died. check-b's attempt
    after it returned, or (`check_b_runs`) runs now."""
    j = Journal(projects, run="wf_r")
    j.start("m1", NOW - 6 * HOUR, NOW - 5.5 * HOUR, label="measure:x", key="km1").end("result", "m1", key="km1")
    j.start("a1", NOW - 5 * HOUR, NOW - 4.5 * HOUR, label="check-a:x", key="ka1").end("result", "a1", key="ka1")
    j.start("m2", NOW - 3 * HOUR, NOW - 2.5 * HOUR, label="measure:x", key="km2").end("result", "m2", key="km2")
    died(j, "a2", "failed", NOW - 2 * HOUR, NOW - 1.9 * HOUR, label="check-a:x", key="ka2")
    if check_b_runs:
        j.start("c2", NOW - 20 * MIN, NOW - 30, label="check-b:x", key="kc2")
    else:
        j.start("c2", NOW - 2 * HOUR, NOW - 1.5 * HOUR, label="check-b:x", key="kc2").end("result", "c2", key="kc2")
    j.write()


CHECK = [("measure", "measure:x", 60), ("check", ["check-a:x", "check-b:x"], 30)]


def test_a_multi_label_stage_with_a_stale_result_and_a_died_retry_is_not_done(tmp_path):
    """nothing runs, and check-a's only result is older than measure's re-run: the stage needs a
    re-run (it read done)"""
    check_stage(tmp_path, check_b_runs=False)
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    p = stages_progress(CHECK, labels, now=NOW, cal=None, default_fix_share=0.7)
    assert p.current != "done" and p.failed, (p.marks, p.current)
    assert [m[0] for m in p.marks] == ["●", "✕"] and p.current == "check failed"


def test_that_stage_runs_while_another_of_its_labels_runs(tmp_path):
    """the same with check-b at work (the run at work on the stage runs check-a again): it runs, never
    fails, timed from check-b (30 min planned, 20 run: at least 10 min left)"""
    check_stage(tmp_path, check_b_runs=True)
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    p = stages_progress(CHECK, labels, now=NOW, cal=None, default_fix_share=0.7)
    assert not p.failed and p.current == "check" and [m[0] for m in p.marks] == ["●", "◉"]
    assert p.over == 0 and p.remaining == 10 * 60


def test_a_paused_item_whose_rerun_was_stopped_stays_paused(tmp_path):
    """the owner held the item while its fix ran again, and the run stopped: the fix is held (it read
    done, and the item left the release as finished)"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [
        {"name": "Search index", "key": "x", "build": 60, "review": 20, "fix": 30, "paused": True}]})
    p.notes()
    j = Journal(p.projects, run="wf_x")
    j.start("b1", NOW - 5 * HOUR, NOW - 4.5 * HOUR, label="build:x", key="kb").end("result", "b1", key="kb")
    j.start("r1", NOW - 4.4 * HOUR, NOW - 4 * HOUR, label="review:x", key="kr").end("result", "r1", key="kr", result=MAJOR)
    j.start("f1", NOW - 3.9 * HOUR, NOW - 3.5 * HOUR, label="fix:x", key="kf").end("result", "f1", key="kf")
    died(j, "f2", "silent", NOW - HOUR, NOW - 50 * MIN, label="fix:x", key="kf2")  # a fix re-run; held, its run stopped
    j.write()
    plain = release_text(p)
    assert "1 paused" in plain and "1/1 items ready" not in plain, plain
    assert "●─●─◉" in row_of(plain, "Search index")


def test_a_paused_other_row_whose_rerun_was_stopped_stays_paused(tmp_path):
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [
        {"name": "Cost model", "stages": [["measure", LABEL, 60], ["report", "report:cost", 20]], "paused": True}]})
    p.notes()
    j = Journal(p.projects)
    j.start("a1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "a1")
    j.start("r1", NOW - 2 * HOUR, NOW - 1.5 * HOUR, label="report:cost", key="k2").end("result", "r1", key="k2")
    died(j, "r2", "silent", NOW - HOUR, NOW - 50 * MIN, label="report:cost", key="k2")
    j.write()
    plain = other_text(p)
    row = row_of(plain, "Cost model")
    assert "1 paused" in plain and "●─◉" in row and "paused" in row and "done" not in row


@pytest.mark.parametrize("job, lead", [("done", "●"), ("running", "◉"), ("unknown", "○")])
def test_a_paused_row_after_a_live_job_holds_its_later_stages(tmp_path, job, lead):
    """a held row that follows a plugin's live job (after_server): its later stages read held, as any
    held row's do (a stage that died read ✕ there), so a stopped re-run is held, never done"""
    p = Project(tmp_path)
    p.write_config(f"""
        [plan]
        after_server = "stub"

        [[plugins]]
        name = "stub"
        file = "{STUB_PLUGIN}"
        class = "StubPlugin"

        [plugins.options]
        host = "stubhost"
        """)
    p.plan({"release": "1.0.0", "items": [], "other": [
        {"name": "Nightly data import", "after_server": True, "paused": True,
         "stages": [["import", None, 0], ["verify", "verify:import", 30]]}]})
    p.notes()
    p.plugin_state = {"job": job}
    j = Journal(p.projects)
    j.start("v1", NOW - 3 * HOUR, NOW - 2.5 * HOUR, label="verify:import").end("result", "v1")
    died(j, "v2", "silent", NOW - HOUR, NOW - 50 * MIN, label="verify:import")
    j.write()
    plain = other_text(p)
    row = row_of(plain, "Nightly data import")
    assert "1 paused" in plain and f"{lead}─◉" in row and "paused" in row, plain


def test_a_running_stage_is_not_timed_from_an_earlier_runs_result(tmp_path):
    """research returned in run one; a relaunch ran it again and that attempt died; run three runs
    questions now (20 min in). The stage runs from this round: the hours since research's result (the
    outage) are no overrun (it read "+4h30")"""
    one = Journal(tmp_path, run="wf_one")
    one.start("r1", NOW - 6 * HOUR, NOW - 5.5 * HOUR, label="research:x", key="kr").end("result", "r1", key="kr")
    one.write()
    two = Journal(tmp_path, run="wf_two")
    died(two, "r2", "failed", NOW - HOUR, NOW - 55 * MIN, label="research:x", key="kr")
    two.write()
    three = Journal(tmp_path, run="wf_three")
    three.start("q1", NOW - 20 * MIN, NOW - 30, label="questions:x", key="kq")
    three.write()
    labels = latest_by_label(scan_agents(tmp_path, NOW), NOW, SILENT)
    p = stages_progress([("facts", ["research:x", "questions:x"], 90), ("build", "build:x", 60)], labels,
                        now=NOW, cal=None, default_fix_share=0.7)
    assert not p.failed and p.current == "facts"
    assert p.over == 0, f"over {p.over / 60:.0f} min, remaining {p.remaining / 60:.0f} min"
    assert p.remaining == (70 + 60) * 60


def test_a_label_that_reads_an_earlier_attempt_carries_the_one_that_died(tmp_path):
    """the attempt that died rides on a copy (the agent itself is unchanged), and a label whose latest
    attempt did not die carries none"""
    j = Journal(tmp_path)
    j.start("a1", NOW - 3 * HOUR, NOW - 2 * HOUR).end("result", "a1")
    died(j, "a2", "silent", NOW - HOUR, NOW - 50 * MIN)
    j.start("b1", NOW - 3 * HOUR, NOW - 2 * HOUR, label="review:cost", key="k2").end("result", "b1", key="k2")
    j.write()
    agents = scan_agents(tmp_path, NOW)
    labels = latest_by_label(agents, NOW, SILENT)
    for name in (LABEL, f"{RUN}/{LABEL}"):
        assert labels[name]["id"] == "a1" and died_after(labels[name])["id"] == "a2"
        assert died_after(labels[name])["status"] == "failed"  # silent past the stop limit
    assert died_after(labels["review:cost"]) is None and all("died" not in a for a in agents)


# ── shapes that hold ────────────────────────────────────────────────────────────────────────────
def test_death_success_death_in_one_run_and_across_runs(tmp_path):
    j = Journal(tmp_path / "one", run="wf_a")
    died(j, "d1", "failed", NOW - 4 * HOUR, NOW - 3.9 * HOUR, label="build:x", key="k")
    j.start("s1", NOW - 3.8 * HOUR, NOW - 3 * HOUR, label="build:x", key="k").end("result", "s1", key="k")
    died(j, "d2", "error", NOW - HOUR, NOW - 55 * MIN, label="build:x", key="k")
    j.write()
    labels = latest_by_label(scan_agents(tmp_path / "one", NOW), NOW, SILENT)
    assert labels["build:x"]["id"] == "s1" and labels["wf_a/build:x"]["id"] == "s1"
    across = tmp_path / "across"
    a = Journal(across, run="wf_a")
    died(a, "d1", "failed", NOW - 4 * HOUR, NOW - 3.9 * HOUR, label="build:x", key="k")
    a.write()
    b = Journal(across, run="wf_b")
    b.start("s1", NOW - 3.8 * HOUR, NOW - 3 * HOUR, label="build:x", key="k").end("result", "s1", key="k")
    b.write()
    c = Journal(across, run="wf_c")
    died(c, "d2", "failed", NOW - HOUR, NOW - 55 * MIN, label="build:x", key="k")
    c.write()
    labels = latest_by_label(scan_agents(across, NOW), NOW, SILENT)
    assert labels["build:x"]["id"] == "s1" and labels["wf_c/build:x"]["status"] == "failed"
    assert labels["wf_a/build:x"]["status"] == "failed" and labels["wf_b/build:x"]["id"] == "s1"


def test_a_label_in_two_runs_at_once(tmp_path):
    """the old run still works on it; the new run's attempt of it died: the label runs, the row runs,
    and Agents at work lists the old run's agent"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [{"name": "Cost model", "stages": [["measure", "measure:x", 180]]}]})
    p.notes()
    old = Journal(p.projects, run="wf_old")
    old.start("o1", NOW - 2 * HOUR, NOW - 30, label="measure:x", key="k")
    old.write()
    new = Journal(p.projects, run="wf_new")
    died(new, "n1", "failed", NOW - HOUR, NOW - 55 * MIN, label="measure:x", key="k")
    new.write()
    f = p.engine().build_frame(120)
    assert f.labels["measure:x"]["id"] == "o1" and f.labels["measure:x"]["status"] == "running"
    other = testing.render_text(other_panel(f, 120), 120)[0]
    row = row_of(other, "Cost model")
    assert "measure" in row and "needs rerun" not in row and "failed" not in other
    at_work = testing.render_text(agents_panel(f, 120, []), 120)[0]
    assert "1 running" in at_work and "measure:x" in at_work


def test_a_stage_whose_only_label_died_while_another_workflow_runs_a_later_stage(tmp_path):
    """measure died in the old run; a new run is at work on report: measure needs a re-run, the
    header counts it, and the row reads needs rerun"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [], "other": [
        {"name": "Cost model", "stages": [["measure", "measure:x", 60], ["report", "report:x", 30]]}]})
    p.notes()
    old = Journal(p.projects, run="wf_old")
    died(old, "m1", "failed", NOW - 2 * HOUR, NOW - 1.9 * HOUR, label="measure:x", key="k")
    old.write()
    new = Journal(p.projects, run="wf_new")
    new.start("r1", NOW - 20 * MIN, NOW - 30, label="report:x", key="k")
    new.write()
    other = other_text(p)
    row = row_of(other, "Cost model")
    assert "✕─◉" in row and "needs rerun" in row and "1 failed" in other


def test_a_genuinely_failed_multi_label_stage_across_runs(tmp_path):
    """every attempt of every label died, in two runs, silent and failed, and nothing runs"""
    p = Project(tmp_path)
    p.plan({"release": "1.0.0", "items": [
        {"name": "Export", "key": "e", "build": 60, "review": 20, "fix": 30,
         "before": [["facts", ["research:e", "critic:e"], 90]]}]})
    p.notes()
    old = Journal(p.projects, run="wf_old")
    died(old, "o1", "failed", NOW - 5 * HOUR, NOW - 4.9 * HOUR, label="research:e", key="k1")
    died(old, "o2", "error", NOW - 4.8 * HOUR, NOW - 4.7 * HOUR, label="critic:e", key="k2")
    old.write()
    new = Journal(p.projects, run="wf_new")
    died(new, "n1", "silent", NOW - 2 * HOUR, NOW - 1.5 * HOUR, label="research:e", key="k1")  # silent past the stop limit
    new.write()
    plain = release_text(p)
    row = row_of(plain, "Export")
    assert "✕─○─○─○" in row and "needs rerun" in row and "1 failed" in plain

from __future__ import annotations

import json
import os

import pytest

from lsw_mission_control.plan import parse_plan
from lsw_mission_control.progress import (
    Calibration,
    Prog,
    calibrate,
    eta_from_json,
    in_wait_order,
    item_minutes,
    item_started,
    progress_of,
    short_name,
    stages_progress,
    stages_started,
    wait_for,
)

from conftest import NOW

MIN = 60.0


def a(label, status="done", t0=None, t1=None, findings=None):
    return {"id": label, "label": label, "status": status, "run": "wf", "t0": t0, "t1": t1, "phase": "",
            "result": {"findings": findings or []}}


def sp(stages, labels, cal=None, **kw):
    return stages_progress(stages, labels, now=NOW, cal=cal, default_fix_share=0.7, **kw)


STAGES = [("build", "build:x", 60.0), ("review", "review:x", 20.0), ("fix", "fix:x", 30.0)]


def test_queued():
    p = sp(STAGES, {})
    assert p.current == "queued" and p.waiting and p.remaining == (60 + 20 + 30 * 0.7) * 60
    assert [m[0] for m in p.marks] == ["○", "○", "○"] and p.fraction == 0.0


def test_running_within_plan():
    p = sp(STAGES, {"build:x": a("build:x", "running", NOW - 20 * MIN, NOW)})
    assert p.current == "build" and p.over == 0 and p.remaining == (40 + 20 + 21) * 60
    assert p.start == NOW - 20 * MIN and not p.waiting


def test_overrun_floor_and_growth():
    short = [("build", "build:x", 20.0)]
    p = sp(short, {"build:x": a("build:x", "running", NOW - 15 * MIN, NOW)})
    assert p.over == 0 and p.remaining == 10 * 60  # within 10 min of its plan: at least 10 min left
    p = sp(STAGES, {"build:x": a("build:x", "running", NOW - 65 * MIN, NOW)})
    assert p.over == 5 * MIN and p.remaining == (0.25 * 65 + 20 + 21) * 60  # the longer it has overrun, the longer it goes on


def test_failed_and_paused():
    p = sp(STAGES, {"build:x": a("build:x", "failed", NOW - 30 * MIN, NOW - 10 * MIN)})
    assert p.failed and p.current == "build failed" and p.marks[0][0] == "✕"
    p = sp(STAGES, {"build:x": a("build:x", "failed", NOW - 30 * MIN, NOW - 10 * MIN)}, paused=True)
    assert not p.failed and p.current == "build" and p.remaining == (40 + 20 + 21) * 60


def test_done_and_fix_not_needed():
    labels = {"build:x": a("build:x", t0=NOW - 3000, t1=NOW - 2000),
              "review:x": a("review:x", t0=NOW - 1900, t1=NOW - 1000, findings=[{"severity": "nit"}])}
    p = sp(STAGES, labels)
    assert p.current == "done" and [m[0] for m in p.marks] == ["●", "●", "–"]
    assert p.start == NOW - 3000 and p.end == NOW - 1000 and p.fraction == 1.0


def test_fix_share_weights_an_unknown_fix():
    cal = Calibration(0.7)
    cal.fix_share = 0.5
    p = sp(STAGES, {"build:x": a("build:x", t0=NOW - 3000, t1=NOW - 2000)}, cal=cal)
    assert p.remaining == (20 + 30 * 0.5) * 60
    labels = {"build:x": a("build:x", t0=NOW - 3000, t1=NOW - 2000),
              "review:x": a("review:x", t0=NOW - 1900, t1=NOW - 1000, findings=[{"severity": "major"}])}
    assert sp(STAGES, labels, cal=cal).remaining == 30 * 60  # a fix is needed: its whole time


def test_null_stage_done_once_a_later_one_starts():
    stages = [("your sign-off", None, 0.0)] + STAGES
    assert sp(stages, {}).marks[0][0] == "○"
    p = sp(stages, {"build:x": a("build:x", "running", NOW - 60, NOW)})
    assert p.marks[0][0] == "●"


def test_an_earlier_rerun_invalidates_later_stages():
    labels = {"build:x": a("build:x", "running", NOW - 10 * MIN, NOW), "review:x": a("review:x", t0=NOW - 60 * MIN, t1=NOW - 50 * MIN)}
    p = sp(STAGES, labels)
    assert [m[0] for m in p.marks] == ["◉", "○", "○"]


def test_after_text():
    p = sp(STAGES, {}, wait_before=600, after="merge")
    assert p.current == "after merge" and p.waiting and p.remaining == 600 + (60 + 20 + 30 * 0.7) * 60
    # a wait is shown whatever its length (the caller passes one only while the work is unfinished)
    assert sp(STAGES, {}, wait_before=0, after="merge").current == "after merge"
    assert sp(STAGES, {}).current == "queued"
    # the work it runs after has no finish time: neither has this row
    p = sp(STAGES, {}, wait_before=None, after="merge")
    assert p.current == "after merge" and p.remaining is None and p.waiting and p.fraction == 0.0
    assert p.waits and not sp(STAGES, {}).waits


def test_a_wait_sits_before_the_stages_not_begun():
    """begun early, a row still never finishes before the work it runs after: what has begun runs on
    beside the wait, what has not comes after it"""
    running = {"build:x": a("build:x", "running", NOW - 20 * MIN, NOW)}  # 40 min of build left
    p = sp(STAGES, running, wait_before=90 * MIN, after="merge")
    assert p.current == "build" and not p.waiting and p.remaining == (90 + 20 + 21) * 60
    p = sp(STAGES, running, wait_before=10 * MIN, after="merge")
    assert p.remaining == (40 + 20 + 21) * 60  # the wait ends first: its own build is what binds
    # between its stages (build done), nothing of its own runs: the wait shows
    built = {"build:x": a("build:x", t0=NOW - 30 * MIN, t1=NOW - 10 * MIN)}
    p = sp(STAGES, built, wait_before=90 * MIN, after="merge")
    assert p.current == "after merge" and p.waiting and p.remaining == (90 + 20 + 21) * 60
    # a failed stage's re-run and a held stage have begun too
    failed = {"build:x": a("build:x", "failed", NOW - 30 * MIN, NOW - 10 * MIN)}
    assert sp(STAGES, failed, wait_before=90 * MIN, after="merge").remaining == (90 + 20 + 21) * 60
    assert sp(STAGES, running, wait_before=90 * MIN, after="merge", paused=True).remaining == (90 + 20 + 21) * 60
    # no finish time for the wait: none for the row, begun or not
    assert sp(STAGES, running, wait_before=None, after="merge").remaining is None


def test_calibration_is_sticky():
    items = parse_plan({"release": "1", "items": [{"name": n, "key": n, "build": 60, "review": 30, "fix": 30} for n in "abcd"]}).items
    cal = Calibration(0.7)
    labels = {f"build:{k}": a(f"build:{k}", t0=NOW - 7200, t1=NOW) for k in "abc"}  # twice the plan
    calibrate(cal, items, labels)
    assert cal.factors["build"] == 2.0 and cal.n == 3
    # a re-run drops the count below 3: the factor stays
    labels["build:a"] = a("build:a", "running", NOW - 60, NOW)
    calibrate(cal, items, labels)
    assert cal.factors["build"] == 2.0 and cal.n == 2
    # clamped to 0.2
    labels = {f"build:{k}": a(f"build:{k}", t0=NOW - 60, t1=NOW) for k in "abcd"}
    calibrate(cal, items, labels)
    assert cal.factors["build"] == 0.2


def test_fix_share_from_three_reviews():
    items = parse_plan({"release": "1", "items": [{"name": n, "key": n, "build": 1, "review": 1, "fix": 1} for n in "abcd"]}).items
    cal = Calibration(0.7)
    labels = {f"review:{k}": a(f"review:{k}", t0=NOW - 60, t1=NOW) for k in "abc"}
    labels["fix:a"] = a("fix:a", "running", NOW - 10, NOW)
    calibrate(cal, items, labels)
    assert cal.fix_share == pytest.approx(1 / 3)


def test_calibration_scales_stages_named_build_review_fix_only():
    cal = Calibration()
    cal.factors["build"] = 2.0
    assert sp([("build", "b:x", 10.0), ("fix_share", "f:x", 10.0), ("n", "n:x", 10.0)], {}, cal=cal).remaining == 40 * 60


def test_progress_file(tmp_path):
    prog = tmp_path / "p.jsonl"
    stages = [("job", {"progress": str(prog), "total": 4}, 60.0)]
    assert progress_of(str(prog)) == (0, None, None)
    assert sp(stages, {}).remaining == 3600
    prog.write_text("\n".join(json.dumps(x) for x in [{"t": NOW - 300}, {"t": NOW - 100, "status": "ok"},
                                                      {"t": NOW - 50, "status": "error"}]) + "\nnot json\n")
    assert progress_of(str(prog)) == (2, NOW - 300, NOW - 100)
    p = sp(stages, {})
    assert p.current == "job 2/4" and p.remaining == 300 * 2 / 2
    eta = tmp_path / "eta.json"
    eta.write_text(json.dumps({"projected_finish_utc": "2026-09-21T15:13:20Z"}))
    os.utime(eta, (NOW - 60, NOW - 60))
    stages = [("job", {"progress": str(prog), "total": 4, "eta_json": str(eta)}, 60.0)]
    assert sp(stages, {}).remaining == 3600  # the job's own projection wins while it is fresh
    os.utime(eta, (NOW - 3600, NOW - 3600))
    assert eta_from_json(str(eta), "projected_finish_utc", NOW) is None
    with open(prog, "a") as fh:
        fh.write(json.dumps({"t": NOW - 10}) + "\n" + json.dumps({"t": NOW - 5}) + "\n")
    os.utime(prog, (NOW, NOW))
    p = sp(stages, {})
    assert p.current == "done" and p.start == NOW - 300 and p.end == NOW - 5


def test_a_progress_line_that_is_not_an_object_is_skipped(tmp_path):
    """a valid-JSON line that is not an object raised AttributeError, and every frame failed."""
    prog = tmp_path / "p.jsonl"
    prog.write_text("\n".join([json.dumps({"t": NOW - 60}), '"a bare string"', "3", "[1, 2]", "null",
                               json.dumps({"t": NOW - 30, "status": "done"})]) + "\n")
    assert progress_of(str(prog)) == (2, NOW - 60, NOW - 30)


def test_item_helpers(tmp_path):
    plan = parse_plan({"release": "1", "items": [
        {"name": "Merge the branches", "key": "merge", "build": 60, "review": 30, "fix": 30,
         "before": [["design", "design:merge", 20]]},
        {"name": "Other", "key": None}]})
    it = plan.items[0]
    cal = Calibration()
    assert item_minutes(plan, it, cal) == (20 + 120) * 60 and item_minutes(plan, plan.items[1], cal) == 0
    assert not item_started(plan, "merge", {}) and item_started(plan, "merge", {"design:merge": {}})
    assert item_started(plan, "merge", {"fix:merge": {}}) and not item_started(plan, None, {})
    assert short_name("merge", plan.items) == "merge" and short_name("Some Name", plan.items) == "some"
    assert short_name(None, plan.items) == ""
    assert short_name(" ", plan.items) == ""  # a blank name has no first word (it used to raise)


def prog(remaining, current="build"):
    return Prog(remaining, current)


def test_wait_for_a_target_with_a_finish_time_without_one_and_finished():
    assert wait_for([]) is None
    assert wait_for([("a", Prog(0.0, "done"))]) is None  # finished: nothing to wait for
    assert wait_for([("a", prog(600.0))]) == (600.0, "a")
    assert wait_for([("a", prog(0.0, "your pick"))]) == (0.0, "a")  # unfinished, even with no time left
    # several: the one that finishes last binds, wherever it is listed
    assert wait_for([("a", prog(600.0)), ("b", prog(3600.0)), ("c", Prog(0.0, "done"))]) == (3600.0, "b")
    # one has no finish time (stalled, paused, unknown): the wait has none either, and names it
    assert wait_for([("a", prog(3600.0)), ("b", prog(None, "stalled"))]) == (None, "b")
    assert wait_for([("b", Prog(0.0, "done")), ("a", prog(None, "paused"))]) == (None, "a")
    # failed: its row reads "needs rerun", with no time, so the wait has none; the release panel
    # (whose finish counts the re-run) asks for the re-run's time instead
    failed = Prog(1800.0, "build failed", failed=True)
    assert wait_for([("a", prog(3600.0)), ("f", failed)]) == (None, "f")
    assert wait_for([("a", prog(3600.0)), ("f", failed)], rerun=True) == (3600.0, "a")
    assert wait_for([("f", failed)], rerun=True) == (1800.0, "f")


def test_in_wait_order_computes_targets_first_and_breaks_a_cycle():
    seen = []

    def compute(i, wait):
        seen.append((i, wait))
        return prog(100.0 * (i + 1) + (wait[0] if wait and wait[0] is not None else 0))

    # 0 runs after 2 (listed later), 2 after 1: 1, then 2, then 0
    out = in_wait_order(3, {0: [2], 1: [], 2: [1]}.__getitem__, compute)
    assert [i for i, _w in seen] == [1, 2, 0]
    assert seen[1] == (2, (200.0, 1)) and seen[2] == (0, (500.0, 2))
    assert [p.remaining for p in out] == [600.0, 200.0, 500.0]
    # a cycle (a typo): broken where it closes, every row still computed once; an unknown index is ignored
    seen.clear()
    out = in_wait_order(3, {0: [1], 1: [0], 2: [2, 7]}.__getitem__, compute)
    assert sorted(i for i, _w in seen) == [0, 1, 2] and all(p is not None for p in out)
    assert dict(seen)[2] is None


def test_stages_started(tmp_path):
    prog_file = tmp_path / "p.jsonl"
    stages = [("wait", None, 0.0), ("job", {"progress": str(prog_file), "total": 3}, 10.0), ("check", ["c:1", "c:2"], 5.0)]
    assert not stages_started(stages, {})
    assert stages_started(stages, {"c:2": a("c:2", "running", NOW - 60, NOW)})
    prog_file.write_text(json.dumps({"t": NOW - 60}) + "\n")
    assert stages_started(stages, {})

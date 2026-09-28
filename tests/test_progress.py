from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from lsw_mission_control import progress
from lsw_mission_control.plan import parse_plan
from lsw_mission_control.progress import (
    Calibration,
    JobFile,
    Prog,
    calibrate,
    created_at,
    eta_from_json,
    in_wait_order,
    item_minutes,
    item_started,
    job_file,
    progress_of,
    short_name,
    stages_progress,
    stages_started,
    wait_for,
)

from conftest import NOW

MIN = 60.0
HOUR = 3600.0


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
    # the units' pace (300 s for 2: 300 s for the 2 left), with the plan's 55 min left weighed as
    # one unit against the 2 done
    assert p.current == "job 2/4" and p.remaining == (1 * 3300 + 2 * 300) / 3
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


# ── a detached job: running from the moment its progress file exists ───────────────────────
def job(path, total=1200, minutes=3900.0, **extra):
    return ("rebuild", {"progress": str(path), "total": total, **extra}, minutes)


def made(path, t):
    """An empty progress file made afresh at `t` (moving a new file's times back moves its birth
    time with them, on macOS; a birth time never moves forward). It is written beside the old one
    and moved over it, so it is a new file (a new inode) even where a filesystem reuses inodes."""
    new = path.with_name(path.name + ".new")
    new.write_text("")
    os.replace(new, path)
    os.utime(path, (t, t))


def units(path, ts, mtime):
    """Units finished at `ts`, appended; the file last written at `mtime`."""
    with open(path, "a") as fh:
        for t in ts:
            fh.write(json.dumps({"t": t}) + "\n")
    os.utime(path, (mtime, mtime))


def test_created_at_is_the_birth_time_where_the_filesystem_keeps_one():
    assert created_at(SimpleNamespace(st_birthtime=100.0, st_mtime=500.0)) == 100.0
    assert created_at(SimpleNamespace(st_mtime=500.0)) == 500.0  # none kept (Linux): the last write
    assert created_at(SimpleNamespace(st_birthtime=0, st_mtime=500.0)) == 500.0  # one that reports none


@pytest.mark.skipif(not hasattr(os.stat_result, "st_birthtime"), reason="the filesystem keeps no birth time")
def test_a_units_write_keeps_the_files_birth_time(tmp_path):
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - 2 * HOUR)
    with open(prog, "a") as fh:
        fh.write(json.dumps({"t": NOW - 60}) + "\n")
    os.utime(prog, (NOW - 60, NOW - 60))
    assert job_file(str(prog)) == JobFile(1, NOW - 60, NOW - 60, NOW - 2 * HOUR)


def test_a_job_with_no_progress_file_has_not_started(tmp_path):
    prog = tmp_path / "p.jsonl"
    assert job_file(str(prog)) is None
    p = sp([job(prog)], {})
    assert p.current == "queued" and p.waiting and p.start is None and p.over == 0
    assert [m[0] for m in p.marks] == ["○"] and p.remaining == 3900 * MIN and p.fraction == 0.0
    assert not stages_started([job(prog)], {})


def test_an_empty_progress_file_runs_from_its_creation(tmp_path):
    """The bug: a job's stage read "queued 0%" until its first unit finished, hours after launch."""
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - 2 * HOUR)
    assert job_file(str(prog)) == JobFile(0, None, None, NOW - 2 * HOUR)
    p = sp([job(prog)], {})
    assert p.current == "rebuild 0/1200" and not p.waiting and [m[0] for m in p.marks] == ["◉"]
    assert p.start == NOW - 2 * HOUR and p.over == 0
    assert p.remaining == (3900 - 120) * MIN  # its planned minutes, until units give a pace
    assert p.fraction == pytest.approx(120 / 3900)
    assert stages_started([job(prog)], {})
    # past its planned minutes, as an agent's stage: at least 10 min, or a quarter of its time so far
    made(prog, NOW - 70 * HOUR)
    p = sp([job(prog)], {})
    assert p.current == "rebuild 0/1200" and p.over == 5 * HOUR and p.remaining == 0.25 * 70 * HOUR
    made(prog, NOW - 3890 * MIN)
    assert sp([job(prog)], {}).remaining == 10 * MIN
    # the job's own projection, while fresh, beats the plan
    eta = tmp_path / "eta.json"
    eta.write_text(json.dumps({"projected_finish_utc": "2026-09-21T15:13:20Z"}))
    os.utime(eta, (NOW - 60, NOW - 60))
    p = sp([job(prog, eta_json=str(eta))], {})
    assert p.current == "rebuild 0/1200" and p.remaining == HOUR and p.over == 0


def test_a_file_made_after_now_has_run_for_no_time(tmp_path):
    prog = tmp_path / "p.jsonl"
    made(prog, NOW + 60)  # a clock that disagrees: never a negative time so far
    p = sp([job(prog, minutes=60.0)], {})
    assert p.current == "rebuild 0/1200" and p.remaining == HOUR and p.fraction == 0.0


def test_the_first_units_pace_is_weighed_against_the_plan(tmp_path, monkeypatch):
    """A long job's first unit (the largest first, several run at once) lands hours after launch:
    one unit is not yet a pace, so it must not multiply the job's time left."""
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - 3 * HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text("")
    before = sp([job(prog)], {})
    assert before.current == "rebuild 0/1200" and before.remaining == (3900 - 180) * MIN
    prog.write_text(json.dumps({"t": NOW - 60}) + "\n")
    assert job_file(str(prog)) == JobFile(1, NOW - 60, NOW - 60, NOW - 3 * HOUR)
    after = sp([job(prog)], {})
    # the pace is timed from the job's start (the old code timed it from the unit itself: 60 s
    # each), and weighed as one unit against the plan's 120 (a tenth of 1,200): alone, 3 h x 1,199
    # left would read 150 days
    assert after.current == "rebuild 1/1200" and after.start == NOW - 3 * HOUR and after.over == 0
    assert after.remaining == pytest.approx((120 * (3900 - 180) * MIN + 3 * HOUR * 1199) / 121)
    assert after.remaining < 3 * before.remaining and after.fraction > 0
    # with half its units done the pace has taken over: 30 h for 600, so 30 h for the 600 left
    # (the plan's 35 h left counts as 120 units against 600)
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - 30 * HOUR)
    prog.write_text("".join(json.dumps({"t": NOW - 60}) + "\n" for _ in range(600)))
    half = sp([job(prog)], {})
    assert half.current == "rebuild 600/1200"
    assert half.remaining == pytest.approx((120 * 35 * HOUR + 600 * 30 * HOUR) / 720)


def test_a_small_jobs_first_unit_counts_as_much_as_its_plan(tmp_path, monkeypatch):
    """A job of a few units weighs its plan as at least one unit: its first unit halves the gap."""
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text(json.dumps({"t": NOW - 60}) + "\n")
    p = sp([job(prog, total=4, minutes=120.0)], {})
    assert p.current == "rebuild 1/4" and p.remaining == pytest.approx((HOUR + 3 * HOUR) / 2)


def test_a_file_with_only_failed_lines_runs_on_its_plan(tmp_path, monkeypatch):
    lines = "".join(json.dumps(x) + "\n" for x in [{"t": NOW - 600, "status": "error"}, {"t": NOW - 300, "status": "failed"}])
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text(lines)
    assert job_file(str(prog)) == JobFile(0, None, None, NOW - HOUR) and progress_of(str(prog)) == (0, None, None)
    p = sp([job(prog, minutes=120.0)], {})
    assert p.current == "rebuild 0/1200" and p.start == NOW - HOUR and p.remaining == HOUR
    assert not p.failed and not p.waiting and [m[0] for m in p.marks] == ["◉"]
    # a filesystem with no birth time gives the last write: its earliest record, failed or not, is
    # when the job was surely running
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - 60)
    prog2 = tmp_path / "p2.jsonl"
    prog2.write_text(lines)
    assert job_file(str(prog2)).start == NOW - 600 and sp([job(prog2, minutes=120.0)], {}).remaining == 110 * MIN


def test_a_units_time_that_is_not_a_finite_number_is_no_time(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text('{"t": true}\n{"t": NaN}\n{"t": -Infinity}\n{"t": 1' + "0" * 400 + '}\n{"t": "x"}\n')
    assert job_file(str(prog)) == JobFile(5, None, None, NOW - HOUR)  # five units, none with a time
    # 1 h for 5 units: 1 h for the 5 left, against the plan's floor (past its 60 min: a quarter of
    # the hour so far) weighed as one unit
    assert sp([job(prog, total=10, minutes=60.0)], {}).remaining == (15 * MIN + 5 * HOUR) / 6


def test_a_units_time_outside_2000_to_2100_is_no_time(tmp_path, monkeypatch):
    """A placeholder 0 (a skipped unit's), a negative number or milliseconds is not epoch seconds:
    it neither dates the job to 1970 nor stretches it into the next century."""
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text("".join(json.dumps(x) + "\n" for x in [
        {"t": 0, "status": "skipped"}, {"t": -5}, {"t": NOW * 1000}, {"t": 946_684_799}, {"t": NOW - 60}]))
    assert job_file(str(prog)) == JobFile(4, NOW - 60, NOW - 60, NOW - HOUR)
    p = sp([job(prog, total=10, minutes=120.0)], {})
    assert p.start == NOW - HOUR and p.over == 0 and p.current == "rebuild 4/10"


def test_a_finished_job_ran_from_its_start_to_its_last_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - 2 * HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text(json.dumps({"t": NOW - HOUR}) + "\n" + json.dumps({"t": NOW - 30 * MIN}) + "\n")
    p = sp([job(prog, total=2, minutes=10.0), ("check", "c:x", 30.0)], {})
    assert [m[0] for m in p.marks] == ["●", "○"] and p.start == NOW - 2 * HOUR
    assert p.fraction == pytest.approx(90 / (90 + 30))  # 90 min behind it, the check's 30 to come


def test_a_stage_with_no_agent_is_behind_a_job_that_has_begun(tmp_path):
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - HOUR)
    p = sp([("launch", None, 0.0), job(prog, minutes=120.0)], {})
    assert [m[0] for m in p.marks] == ["●", "◉"] and p.current == "rebuild 0/1200"
    # finished, the row is done (it read "queued", its launch stage never behind it)
    prog.write_text(json.dumps({"t": NOW - 60}) + "\n" + json.dumps({"t": NOW - 30}) + "\n")
    assert sp([("launch", None, 0.0), job(prog, total=2)], {}).current == "done"
    assert sp([("launch", None, 0.0), job(tmp_path / "none.jsonl")], {}).marks[0][0] == "○"


def test_a_later_stage_that_ran_before_the_job_began_runs_again(tmp_path):
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - HOUR)
    stages = [job(prog, minutes=120.0), ("score", "score:x", 30.0)]
    old = {"score:x": a("score:x", "done", NOW - 5 * HOUR, NOW - 4 * HOUR)}  # a run before this job
    p = sp(stages, old)
    assert [m[0] for m in p.marks] == ["◉", "○"] and p.remaining == (60 + 30) * MIN
    new = {"score:x": a("score:x", "running", NOW - 10 * MIN, NOW)}  # begun early, beside the job
    assert [m[0] for m in sp(stages, new).marks] == ["◉", "◉"]


def test_with_no_birth_time_a_unit_does_not_move_the_jobs_start(tmp_path, monkeypatch):
    """A filesystem that keeps no birth time: the file's creation is its last write, which the first
    unit moves forward. The start seen before stays, and a later agent begun beside the job still runs."""
    monkeypatch.setattr(progress, "created_at", lambda st: st.st_mtime)
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - 2 * HOUR)
    stages = [job(prog, total=100, minutes=600.0), ("score", "score:x", 30.0)]
    labels = {"score:x": a("score:x", "running", NOW - HOUR, NOW)}
    p = sp(stages, labels)
    assert [m[0] for m in p.marks] == ["◉", "◉"] and p.start == NOW - 2 * HOUR
    units(prog, [NOW - 10 * MIN], NOW - 10 * MIN)
    p = sp(stages, labels)
    assert p.current == "score" and p.start == NOW - 2 * HOUR and [m[0] for m in p.marks] == ["◉", "◉"]
    assert job_file(str(prog)).start == NOW - 2 * HOUR
    # emptied in place, it is a fresh run from when it was emptied (a birth time would keep the old one)
    with open(prog, "w"):
        pass
    os.utime(prog, (NOW - MIN, NOW - MIN))
    assert job_file(str(prog)).start == NOW - MIN


def test_a_failed_lines_placeholder_time_does_not_date_the_job_to_1970(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text(json.dumps({"t": 0, "status": "skipped"}) + "\n")
    p = sp([job(prog, total=10, minutes=120.0)], {})
    assert p.start == NOW - HOUR and p.over == 0 and p.remaining == HOUR and p.current == "rebuild 0/10"


def test_a_finished_jobs_file_cleaned_up_after_the_work_after_it_began_is_behind_it(tmp_path, monkeypatch):
    """A cleanup that deletes a scored job's folder while the plan still lists the job: the row stays
    done, as a stage with no agent does. With nothing after it begun, a missing file is not started."""
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - 10 * HOUR)
    prog = tmp_path / "p.jsonl"
    prog.write_text("")
    units(prog, [NOW - 6 * HOUR, NOW - 5 * HOUR], NOW - 5 * HOUR)
    stages = [job(prog, total=2, minutes=3900.0), ("score", "score:x", 60.0)]
    labels = {"score:x": a("score:x", "done", NOW - 4 * HOUR, NOW - 3 * HOUR)}
    assert sp(stages, labels).current == "done"
    prog.unlink()
    p = sp(stages, labels)
    assert p.current == "done" and [m[0] for m in p.marks] == ["●", "●"]
    running = {"score:x": a("score:x", "running", NOW - HOUR, NOW)}
    p = sp(stages, running)
    assert [m[0] for m in p.marks] == ["●", "◉"] and p.current == "score" and p.remaining == 15 * MIN
    p = sp(stages, {})
    assert [m[0] for m in p.marks] == ["○", "○"] and p.current == "queued"
    assert p.remaining == (3900 + 60) * MIN


def test_a_deleted_file_is_not_started_even_after_it_was_read(tmp_path):
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - HOUR)
    units(prog, [NOW - 30 * MIN], NOW - 30 * MIN)
    assert sp([job(prog, total=5)], {}).current == "rebuild 1/5"
    prog.unlink()
    assert job_file(str(prog)) is None and not stages_started([job(prog, total=5)], {})
    p = sp([("launch", None, 0.0), job(prog, total=5)], {})
    assert [m[0] for m in p.marks] == ["○", "○"] and p.current == "queued"


@pytest.mark.skipif(not hasattr(os.stat_result, "st_birthtime"), reason="the filesystem keeps no birth time")
def test_a_file_made_again_restarts_the_job_and_its_later_stages(tmp_path):
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - 10 * HOUR)
    units(prog, [NOW - 9 * HOUR, NOW - 8 * HOUR], NOW - 8 * HOUR)
    stages = [job(prog, total=2, minutes=120.0), ("score", "score:x", 30.0)]
    labels = {"score:x": a("score:x", "done", NOW - 7 * HOUR, NOW - 6 * HOUR)}
    assert sp(stages, labels).current == "done"
    made(prog, NOW - HOUR)  # the re-run the docs ask for: the old file gone, a new one made
    p = sp(stages, labels)
    assert p.current == "rebuild 0/2" and [m[0] for m in p.marks] == ["◉", "○"] and p.start == NOW - HOUR


@pytest.mark.skipif(not hasattr(os.stat_result, "st_birthtime"), reason="the filesystem keeps no birth time")
def test_emptying_a_file_keeps_its_birth_time_as_documented(tmp_path):
    prog = tmp_path / "p.jsonl"
    made(prog, NOW - 10 * HOUR)
    units(prog, [NOW - 9 * HOUR], NOW - 9 * HOUR)
    assert job_file(str(prog)).start == NOW - 10 * HOUR
    with open(prog, "w"):
        pass  # emptied in place (the docs tell a job to delete it instead)
    os.utime(prog, (NOW - MIN, NOW - MIN))
    assert job_file(str(prog)).start == NOW - 10 * HOUR


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

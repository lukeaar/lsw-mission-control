"""A detached job's stage, drawn: it runs from the moment its progress file exists.

The bug they pin: a job whose first of 1,200 units finished hours after launch read
"queued 0%" under Other work all that time, and the next release counted it as not begun."""

from __future__ import annotations

import json
import os

from lsw_mission_control import progress, testing
from lsw_mission_control.render.next_release import next_panel
from lsw_mission_control.render.other import other_panel

from conftest import NOW
from scenarios import HOUR, MIN, Project, midway, release_plan

NAME = "Thumbnail rebuild · 1,200 files"


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def made(path, t) -> None:
    """An empty progress file made afresh at `t` (on macOS its birth time moves back with it),
    moved over any old one so that it is a new file."""
    new = path.with_name(path.name + ".new")
    new.write_text("")
    os.replace(new, path)
    os.utime(path, (t, t))


def thumbnails(p: Project, prog) -> None:
    midway(p)
    plan = release_plan()
    plan["other"] = [{"name": NAME, "stages": [
        ["launch", "run:thumbs", 90], ["rebuild", {"progress": str(prog), "total": 1200}, 3900], ["check", "check:thumbs", 60]]}]
    p.plan(plan)
    p.agent("run:thumbs", status="done", start_ago=3 * HOUR, quiet_s=2.5 * HOUR, run="wf_run-s")


def other_text(p: Project) -> str:
    return testing.render_text(other_panel(p.engine().build_frame(120), 120), 120)[0]


def test_other_a_job_with_no_progress_file_is_queued(tmp_path):
    p = Project(tmp_path)
    thumbnails(p, tmp_path / "progress.jsonl")
    line = row(other_text(p), NAME)
    assert "○─○" in line and "queued" in line and "~2d18h" in line  # 3,900 + 60 min to come


def test_other_a_job_runs_from_its_empty_progress_file(tmp_path, monkeypatch):
    p = Project(tmp_path)
    prog = tmp_path / "progress.jsonl"
    made(prog, NOW - 2 * HOUR)
    thumbnails(p, prog)
    plain = other_text(p)
    line = row(plain, NAME)
    # its planned 3,900 min less the 2 h it has run, then the check's hour; the fill is the 2 h
    # it has run and the launch stage's 30 min (of the work behind it and still to come)
    assert "rebuild 0/1200" in line and "queued" not in line and "~2d16h" in line
    assert " 4%" in line
    assert "1 in progress" in plain and f"next to finish: {NAME}" in plain
    # its first unit: the pace since the job began (one unit in 2 h) counts as one unit against the
    # plan's 120 (the birth time a write keeps on macOS, pinned so that a filesystem without one
    # reads the same): (120 × 63 h + 2 h × 1,199) ÷ 121 + the check's hour, not 2 h × 1,199 (99 days)
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - 2 * HOUR)
    prog.write_text(json.dumps({"t": NOW - 60}) + "\n")
    os.utime(prog, (NOW - 60, NOW - 60))
    line = row(other_text(p), NAME)
    assert "rebuild 1/1200" in line and "~3d11h" in line


def test_other_a_job_past_its_plan_with_no_unit_shows_the_overrun(tmp_path):
    p = Project(tmp_path)
    prog = tmp_path / "progress.jsonl"
    made(prog, NOW - 70 * HOUR)
    thumbnails(p, prog)
    line = row(other_text(p), NAME)
    assert "rebuild 0/1200" in line and "≥" in line  # the stage column cuts the "+5h00"


def test_next_release_an_item_whose_job_has_begun_is_under_way(tmp_path):
    p = Project(tmp_path)
    prog = tmp_path / "progress.jsonl"
    midway(p)
    plan = release_plan()
    plan["next"] = {"release": "1.5.0", "items": [
        {"key": "sizes", "name": "Measure the sizes", "group": "first",
         "before": [["measure", {"progress": str(prog), "total": 40}, 120]], "build": 30}]}
    p.plan(plan)

    def next_text() -> str:
        return testing.render_text(next_panel(p.engine().build_frame(120), 120), 120)[0]

    line = row(next_text(), "Measure the sizes")
    assert "planned" in line and line.rstrip(" │").endswith("—")  # not begun: no finish time
    made(prog, NOW - 30 * MIN)
    plain = next_text()
    line = row(plain, "Measure the sizes")
    assert "measure 0/40" in line and "~2h00" in line and "1 under way" in plain  # 90 + 30 min

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


def thumbnails(p: Project, prog, launched_ago: float = 3 * HOUR) -> None:
    """The launch stage ran from `launched_ago` for half an hour."""
    midway(p)
    plan = release_plan()
    plan["other"] = [{"name": NAME, "stages": [
        ["launch", "run:thumbs", 90], ["rebuild", {"progress": str(prog), "total": 1200}, 3900], ["check", "check:thumbs", 60]]}]
    p.plan(plan)
    p.agent("run:thumbs", status="done", start_ago=launched_ago, quiet_s=launched_ago - 0.5 * HOUR, run="wf_run-s")


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
    """launched 71 h ago, its file made 70 h ago: 70 h of its own against its 65 h plan"""
    prog = tmp_path / "progress.jsonl"
    made(prog, NOW - 70 * HOUR)
    (tmp_path / "a").mkdir()
    p = Project(tmp_path / "a")
    thumbnails(p, prog, launched_ago=71 * HOUR)
    line = row(other_text(p), NAME)
    assert "rebuild 0/1200" in line and "≥" in line  # the stage column cuts the "+5h00"
    # launched 3 h ago, after its file was made: its own start is when the launch finished, 2.5 h ago
    # (timed from its file, it read 5 h past its plan the moment its turn came)
    (tmp_path / "b").mkdir()
    p = Project(tmp_path / "b")
    thumbnails(p, prog)
    line = row(other_text(p), NAME)
    assert "rebuild 0/1200" in line and "≥" not in line and "~2d15h" in line  # 3,900 min - 2.5 h, + 1 h


def test_a_job_whose_file_was_made_at_launch_waits_for_its_turn(tmp_path, monkeypatch):
    """A pipeline that makes every job's progress file at launch: the full run's file is 70 h old while
    the sample before it still runs. The full run has not started: no overrun (it read `≥` with the
    stage `full 0/353 +1d…`, 70 h since its file against its 44.5 h plan), and its whole plan is still
    to come. Once the sample has finished its turn has come, and it is timed from the sample's end."""
    monkeypatch.setattr(progress, "created_at", lambda st: NOW - 70 * HOUR)
    p = Project(tmp_path)
    midway(p)
    sample, full, eta = tmp_path / "sample.jsonl", tmp_path / "full.jsonl", tmp_path / "eta.json"
    sample.write_text("".join(json.dumps({"t": NOW - 60 * HOUR + i * 60}) + "\n" for i in range(40)))
    full.write_text("")
    eta.write_text(json.dumps({"sample_end": "2026-09-22T00:13:20Z"}))  # 10 h from now
    os.utime(eta, (NOW - 60, NOW - 60))
    plan = release_plan()
    plan["other"] = [{"name": NAME, "stages": [
        ["sample", {"progress": str(sample), "total": 80, "eta_json": str(eta), "eta_key": "sample_end"}, 600],
        ["full", {"progress": str(full), "total": 353}, 2670], ["check", "check:thumbs", 60]]}]
    p.plan(plan)
    line = row(other_text(p), NAME)
    # the sample's 10 h, then the full run's whole 44.5 h, then the check's hour
    assert "◉─◉─○" in line and "full 0/353" in line and "+" not in line and "≥" not in line and "~2d07h" in line
    # the sample's last unit 2 h ago: the full run's turn came then (42.5 h left), and the check's hour
    sample.write_text("".join(json.dumps({"t": NOW - 2 * HOUR}) + "\n" for _ in range(80)))
    line = row(other_text(p), NAME)
    assert "●─◉─○" in line and "full 0/353" in line and "+" not in line and "~1d19h" in line


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

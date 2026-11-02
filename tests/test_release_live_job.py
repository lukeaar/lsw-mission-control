"""A release item that waits on a plugin's live job (`after_server` on `items`): its first before stage
mirrors the job as an other item's first stage does, and what follows it counts the job's time left.

The bug they pin: a release row could wait on a live job only through a stage of fixed minutes with
no agent, which read "queued" and counted its minutes from now, so its finish, and the release's
behind it, slid forward with the clock (an adopter's release read "in 9d16h" for two hours)."""

from __future__ import annotations

import pytest

from lsw_mission_control import testing
from lsw_mission_control.plan import parse_plan
from lsw_mission_control.plugin import LiveJob
from lsw_mission_control.progress import Calibration, item_progress
from lsw_mission_control.render.other import other_panel
from lsw_mission_control.render.release import release_panel

from conftest import NOW
from scenarios import HOUR, LIVE_ITEMS, Project, live_release, release_plan

JOB_ROW = "Reimport the catalogue"


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def release_text(p: Project) -> str:
    return testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)[0]


def columns(line: str) -> tuple[str, str, str]:
    """(stage dots, stage, eta) of a work row: the cells between its name and its bar, and the end."""
    cells = [c for c in line.strip("│ ").split("  ") if c.strip()]
    return cells[1].strip(), cells[2].strip(), cells[-1].strip()


def set_job(p: Project, job: str) -> None:
    """the plugin's live job in a state; "error": its plugin fails"""
    p.plugin_state["job"] = "running" if job == "error" else job
    p.plugin_state["fail"] = "live_job" if job == "error" else ""


# Each state of the job: the job's row (its dots, its stage, its time left), then what follows it. The
# job's 2h00 counts before the row's own 59 min (30 + 15 + 20 x 0.7); Ranking tune-up's 57 min come after
# both, Security review's 47 after that, then the final merge's 3h30 and the tag row's 47 min.
STATES = {
    "running": ("◉─○─○─○", "import", "~2h59 · 17:12", "~3h56 · 18:09", "~4h43 · 18:56",
                "from 18:56", "done 22:26", "out 23:13", "release out 23:13 (in 9h00)"),
    "done": ("●─○─○─○", "queued", "~59m · 15:12", "~1h56 · 16:09", "~2h43 · 16:56",
             "from 16:56", "done 20:26", "out 21:13", "release out 21:13 (in 7h00)"),
    # no finish time for the job: none for what follows it, the final merge's start and the release
    "not-live": ("◉─○─○─○", "import?", "—", "—", "—", "from —", "done —", "out —", "release out —"),
    "stalled": ("◉─○─○─○", "import stalled", "—", "—", "—", "from —", "done —", "out —", "release out —"),
    "unknown": ("○─○─○─○", "import?", "—", "—", "—", "from —", "done —", "out —", "release out —"),
    "error": ("✕─○─○─○", "plugin error", "—", "—", "—", "from —", "done —", "out —", "release out —"),
}


@pytest.mark.parametrize("job", sorted(STATES))
def test_a_release_item_after_a_live_job_in_each_of_its_states(tmp_path, job):
    dots, stage, eta, ranking, security, merge_from, merge_done, tag_out, out = STATES[job]
    p = Project(tmp_path)
    live_release(p)
    set_job(p, job)
    plain = release_text(p)
    assert columns(row(plain, JOB_ROW)) == (dots, stage, eta)
    rank = row(plain, "Ranking tune-up")  # after:catalogue
    assert "after reimport" in rank and rank.rstrip(" │").endswith(ranking)
    sec = row(plain, "Security review")  # after_all
    assert "after all" in sec and sec.rstrip(" │").endswith(security)
    merge, tag = row(plain, "Final merge"), row(plain, "Tag 1.4.0")
    assert merge_from in merge and merge.rstrip(" │").endswith(merge_done)
    assert tag.rstrip(" │").endswith(tag_out)
    assert out in plain and ("(in" in plain) == (eta != "—")


@pytest.mark.parametrize("job", sorted(STATES))
def test_the_job_reads_as_an_other_items_first_stage_does(tmp_path, job):
    """the same mark and the same stage as midway's other item after the job, whose first stage is
    named alike ("import"), in every state"""
    p = Project(tmp_path)
    live_release(p)
    set_job(p, job)
    f = p.engine().build_frame(120)
    release = columns(row(testing.render_text(release_panel(f, 120)[0], 120)[0], JOB_ROW))
    other = columns(row(testing.render_text(other_panel(f, 120), 120)[0], "Nightly data import"))
    assert release[0][0] == other[0][0] and release[1] == other[1]


def test_the_release_finish_counts_the_jobs_time_left_and_holds_still_as_the_clock_moves(tmp_path):
    """Two frozen clocks an hour apart, the job an hour nearer its end: every finish behind the job (its
    own row's, the after:<key> and after_all rows', the final merge's start, the release's) is the same,
    and only the time left drops by the hour. With a stage of fixed minutes in its place, each moved an
    hour later."""
    p = Project(tmp_path)
    live_release(p)
    first = release_text(p)
    testing.freeze(NOW + HOUR)
    p.plugin_state["job"] = "an-hour-on"
    later = release_text(p)
    for name, (was, now_) in {JOB_ROW: ("~2h59 · 17:12", "~1h59 · 17:12"),
                              "Ranking tune-up": ("~3h56 · 18:09", "~2h56 · 18:09"),
                              "Security review": ("~4h43 · 18:56", "~3h43 · 18:56")}.items():
        assert row(first, name).rstrip(" │").endswith(was) and row(later, name).rstrip(" │").endswith(now_)
    for plain in (first, later):
        assert "from 18:56" in row(plain, "Final merge") and row(plain, "Tag 1.4.0").rstrip(" │").endswith("out 23:13")
    assert "release out 23:13 (in 9h00)" in first and "release out 23:13 (in 8h00)" in later


def test_the_calibration_never_scales_the_live_jobs_time():
    """the job's time left is measured, not planned: a calibration that doubles every build, review and
    fix doubles the stages after it (60 + 30 + 40 x 0.7 min), never the job's 2h00, though its stage is
    named build"""
    plan = parse_plan({"release": "1", "items": [
        {"name": "Server build", "key": "srv", "build": 30, "review": 15, "fix": 20, "after_server": True,
         "before": [["build", None, 0]]}]})
    cal = Calibration()
    cal.factors = {"build": 2.0, "review": 2.0, "fix": 2.0}
    job = LiveJob(left=400, frac=0.6, eta=2 * HOUR, eta_life=3 * HOUR, live=True)
    p = item_progress(plan, plan.items[0], {}, now=NOW, cal=cal, default_fix_share=0.7, job=job)
    assert p.current == "build" and p.remaining == pytest.approx(2 * HOUR + (60 + 30 + 40 * 0.7) * 60)


def with_items(p: Project, **change) -> None:
    """live_release, the job's row changed"""
    items = [dict(it) for it in LIVE_ITEMS]
    items[1].update(change)
    p.plan(release_plan(items=items))


@pytest.mark.parametrize("job", ["running", "stalled", "error", "done"])
def test_while_the_job_is_unfinished_the_row_reads_it_never_the_owners_stage(tmp_path, job):
    """a stage of the owner's after the job ("your check") is the row's next once the job is done, not
    while the job runs, stalls or its plugin fails: the job's state stays in view"""
    p = Project(tmp_path)
    live_release(p)
    with_items(p, before=[["import", None, 0], ["your check", None, 0]])
    set_job(p, job)
    plain = release_text(p)
    stage = columns(row(plain, JOB_ROW))[1]
    if job == "done":
        assert stage == "your check" and "1 wait on you" in plain
    else:
        assert stage == {"running": "import", "stalled": "import stalled", "error": "plugin error"}[job]
        assert "wait on you" not in plain


def test_an_after_all_go_ahead_waits_for_the_job(tmp_path):
    """an owner_ok item after all of the rest that waits on the job reads the job while it runs, and
    your go-ahead once it is done and nothing of its own has begun"""
    p = Project(tmp_path)
    live_release(p)
    items = [dict(it) for it in LIVE_ITEMS]
    items[3].update(flags=["after_all", "owner_ok"], after_server=True, before=[["import", None, 0]])
    p.plan(release_plan(items=items))
    for job, stage in (("running", "import"), ("done", "your go-ahead")):
        set_job(p, job)
        assert columns(row(release_text(p), "Security review"))[1] == stage


def test_a_held_row_after_the_job_keeps_its_time_and_a_hold_that_ended_reads_the_job(tmp_path):
    """held, the row reads paused as any release row does, and what runs after it still counts the
    job's time; its hold over with nothing of its own run since, it reads the job that still runs, not
    "hold ended", which it reads once the job is done"""
    import datetime as dt

    p = Project(tmp_path)
    live_release(p)
    with_items(p, paused=True)
    plain = release_text(p)
    assert columns(row(plain, JOB_ROW))[1] == "paused" and "1 paused" in plain
    assert row(plain, "Ranking tune-up").rstrip(" │").endswith("~3h56 · 18:09")
    with_items(p, paused_until=dt.datetime.fromtimestamp(NOW - HOUR, tz=dt.timezone.utc).isoformat())
    assert columns(row(release_text(p), JOB_ROW)) == ("◉─○─○─○", "import", "~2h59 · 17:12")
    p.plugin_state["job"] = "done"
    assert columns(row(release_text(p), JOB_ROW))[1:] == ("hold ended", "not resumed")

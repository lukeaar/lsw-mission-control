"""A hold with an end (`paused_until`) ends. The bug they pin: at a usage reset, rows held until it
kept reading "paused" until their `paused_until` was taken out of the plan by hand (a hold was read
from the plan alone, whatever the time). Once that time has passed, a row reads its real stage, or,
while nothing of it has run since, says the hold ended and nothing has resumed it."""

from __future__ import annotations

import datetime as dt

import pytest

from lsw_mission_control import testing
from lsw_mission_control.plan import parse_plan
from lsw_mission_control.render.agents import agents_panel
from lsw_mission_control.render.release import release_panel

from conftest import NOW
from golden_util import check
from scenarios import HOUR, MIN, Project, midway, release_plan


def at(t: float) -> str:
    return dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).isoformat()


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def eta(line: str) -> str:
    """A row's last column (its finish time, or what stands in for one)."""
    return line.rstrip(" │").split("%")[-1].strip()


def release_text(p: Project, width: int = 120) -> str:
    return testing.render_text(release_panel(p.engine().build_frame(width), width)[0], width)[0]


def hold(p: Project, flags: dict | None = None, **until: float) -> None:
    """midway's plan with each named item (by key) held until NOW + the offset (negative: ended)."""
    plan = release_plan()
    for it in plan["items"]:
        if it["key"] in until:
            it["paused_until"] = at(NOW + until[it["key"]])
        if flags and it["key"] in flags:
            it["flags"] = flags[it["key"]]
    p.plan(plan)


def test_a_hold_ends_at_its_time():
    """The plan is read once; whether an item is held is asked of the clock at every frame."""
    plan = parse_plan({"release": "1", "items": [
        {"name": "A", "key": "a", "paused": True},
        {"name": "B", "key": "b", "paused_until": at(NOW + HOUR)},
        {"name": "C", "key": "c", "paused_until": at(NOW - HOUR)},
        {"name": "D", "key": "d", "paused": True, "paused_until": at(NOW - HOUR)},  # the time ends it too
        {"name": "E", "key": "e"}]})
    assert [it.held(NOW) for it in plan.items] == [True, True, False, False, False]
    assert [it.hold_ended(NOW) for it in plan.items] == [False, False, True, True, False]
    b = plan.items[1]
    assert b.held(NOW + HOUR - 1) and not b.held(NOW + HOUR) and b.hold_ended(NOW + HOUR)


def test_a_hold_that_ended_with_nothing_resumed_says_so(tmp_path):
    """a11y's build stopped 2 h ago and its hold ended 1 h ago: nothing has run since. It reads "hold
    ended" and "not resumed", never "paused" or failed, and the header counts it. It keeps the time
    left it had while held (build 120 - 60 min run, review 30, fix 45 x 0.7: 2h01), so what runs
    after it waits that long: Dark mode polish, 45 + 15 + 20 x 0.7 min more."""
    p = Project(tmp_path)
    midway(p)
    hold(p, flags={"dark": ["after:a11y"]}, a11y=-HOUR)
    plain = release_text(p)
    audit = row(plain, "Accessibility audit")
    assert "◉" in audit and "hold ended" in audit and eta(audit) == "not resumed"
    assert "paused" not in plain and "failed" not in plain and "needs rerun" not in plain
    assert "1 not resumed" in plain
    assert "after access" in row(plain, "Dark mode polish") and eta(row(plain, "Dark mode polish")) == "~3h15 · 17:28"


def test_a_hold_that_ended_reads_the_real_stage_once_work_resumed(tmp_path):
    """export's hold ended 30 min ago and its fix began 20 min ago: its stage and its time (30 min
    planned, 20 run), as if it had never been held."""
    p = Project(tmp_path)
    midway(p)
    hold(p, export=-30 * MIN)
    export = row(release_text(p), "Export to CSV")
    assert "fix" in export and "hold ended" not in export and eta(export) == "~10m · 14:23"


def test_an_agent_still_at_work_when_the_hold_ended_has_resumed_it(tmp_path):
    """startup's build was last heard from 4 min ago, 2 min before its hold ended: a long tool call
    writes nothing, so an agent that has not gone silent counts as at work (it reads its overrun)."""
    p = Project(tmp_path)
    midway(p)
    hold(p, startup=-2 * MIN)
    startup = row(release_text(p), "Faster startup")
    assert "build +" in startup and "hold ended" not in startup and "·" in eta(startup)


def test_a_hold_that_ended_on_an_item_that_waits_reads_the_wait(tmp_path):
    """Nothing to resume while it waits: Dark mode polish waits on Faster startup, and Security
    review on the owner's go-ahead."""
    p = Project(tmp_path)
    midway(p)
    hold(p, dark=-10 * MIN, security=-10 * MIN)
    plain = release_text(p)
    dark, security = row(plain, "Dark mode polish"), row(plain, "Security review")
    assert "after faster" in dark and "~" in eta(dark)
    assert "your go-ahead" in security
    assert "hold ended" not in plain and "not resumed" not in plain


def test_the_hold_ends_without_the_plan_being_read_again(tmp_path):
    """One dashboard, one reading of the plan: the row turns at the hold's end on its own."""
    p = Project(tmp_path)
    midway(p)
    hold(p, a11y=HOUR)
    engine = p.engine()

    def audit() -> str:
        return row(testing.render_text(release_panel(engine.build_frame(120), 120)[0], 120)[0], "Accessibility audit")

    assert "paused" in audit() and eta(audit()) == "from 15:13"
    testing.freeze(NOW + HOUR + MIN)
    assert "hold ended" in audit() and eta(audit()) == "not resumed" and "paused" not in audit()


def test_agents_of_an_item_whose_hold_ended_are_at_work_again(tmp_path):
    """While held, an item's agents leave Agents at work (its run was stopped); once the hold has
    ended, one that is at work shows again."""
    p = Project(tmp_path)
    midway(p)

    def agents_text() -> str:
        return testing.render_text(agents_panel(p.engine().build_frame(120), 120, []), 120)[0]

    hold(p, export=HOUR)
    assert "fix:export" not in agents_text()
    hold(p, export=-30 * MIN)
    assert "fix:export" in agents_text()


@pytest.mark.parametrize("width", (80, 120))
def test_holds_golden(tmp_path, update_golden, width):
    """Every kind of hold in one release panel: a11y's ended with nothing resumed, export's ended
    with its fix running since, startup's holding until 16:13."""
    p = Project(tmp_path)
    midway(p)
    hold(p, a11y=-HOUR, export=-30 * MIN, startup=2 * HOUR)
    plain, styled = testing.render_text(release_panel(p.engine().build_frame(width), width)[0], width)
    check(f"hold-ended/{width}.txt", plain, update_golden)
    check(f"hold-ended/{width}.ansi", styled, update_golden)

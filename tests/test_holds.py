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


def test_a_hold_ends_at_its_very_second(tmp_path):
    """At paused_until itself the hold is over: a11y (its build stopped 2 h ago) reads "hold ended",
    export (fix active 40 s ago) and startup (build active 4 min ago, not yet silent) their real
    stages. One second before it, all three still read "paused"."""
    p = Project(tmp_path)
    midway(p)
    hold(p, a11y=0, export=0, startup=0)
    plain = release_text(p)
    assert "hold ended" in row(plain, "Accessibility audit") and eta(row(plain, "Accessibility audit")) == "not resumed"
    assert "fix" in row(plain, "Export to CSV") and "build +" in row(plain, "Faster startup")
    assert "1 not resumed" in plain and "paused" not in plain
    hold(p, a11y=1, export=1, startup=1)
    plain = release_text(p)
    assert all("paused" in row(plain, n) for n in ("Accessibility audit", "Export to CSV", "Faster startup"))


@pytest.mark.parametrize("silent, ended", ((10 * MIN, False), (10 * MIN + 1, True)))
def test_work_at_the_hold_end_itself_has_resumed_it(tmp_path, silent, ended):
    """a11y's hold ended 10 min ago; its build finished then: last active at paused_until itself, it
    has resumed the item (at or after the end counts). Last active a second before: not resumed."""
    p = Project(tmp_path)
    midway(p)
    hold(p, a11y=-10 * MIN)
    p.runs.clear()
    p.agent("build:a11y", status="done", start_ago=50 * MIN, quiet_s=silent, run="wf_run-z")
    assert ("hold ended" in row(release_text(p), "Accessibility audit")) is ended


@pytest.mark.parametrize("relaunched", ("build:export", "review:export", "fix:export"))
@pytest.mark.parametrize("died_ago, resumed", ((15 * MIN, True), (40 * MIN, False)))
def test_an_attempt_that_died_after_the_hold_ended_has_resumed_it(tmp_path, relaunched, died_ago, resumed):
    """export's fix was stopped 2 h ago and held until 30 min ago. A run then started one of its
    stages again, and that attempt died: after the hold ended, the item was resumed, so it reads its
    real stage (its fix, whose own attempt died with no result, needs a re-run), never "hold ended".
    Where that stage had returned before (build, review), its label reads the result and the attempt
    that died rides along (latest_by_label): it still counts as the run it was. An attempt that died
    before the hold ended resumed nothing."""
    p = Project(tmp_path)
    midway(p)
    hold(p, export=-30 * MIN)
    p.runs.clear()
    p.agent("design:export", status="done", start_ago=6 * HOUR, quiet_s=5.5 * HOUR)
    p.agent("build:export", status="done", start_ago=5 * HOUR, quiet_s=4 * HOUR)
    p.agent("review:export", status="done", start_ago=3.9 * HOUR, quiet_s=3.5 * HOUR)
    p.agent("fix:export", status="failed", start_ago=3 * HOUR, quiet_s=2 * HOUR)
    p.agent(relaunched, status="failed", start_ago=died_ago + 10 * MIN, quiet_s=died_ago, run="wf_run-z")
    plain = release_text(p)
    export = row(plain, "Export to CSV")
    if resumed:
        assert "●─●─●─●─✕" in export and "fix failed" in export and eta(export) == "needs rerun"
        assert "hold ended" not in plain and "not resumed" not in plain
    else:
        assert "hold ended" in export and eta(export) == "not resumed" and "1 not resumed" in plain


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


@pytest.mark.parametrize("held", ("a", "ab", "abc"))
def test_a_chain_of_rows_held_to_one_time_counts_the_hold_once(tmp_path, held):
    """b runs after a and c after b, each an hour of build; each named row is held until a day from
    now. The hold is a wait beside the one on the row before, so the chain ends a day and three hours
    from now however many of its rows are held. It was added again at each held link: holding all
    three moved the chain's end two days further on, and the release's finish with it."""
    p = Project(tmp_path)
    items = [{"name": "Alpha", "key": "a", "build": 60}, {"name": "Bravo", "key": "b", "build": 60, "flags": ["after:a"]},
             {"name": "Charlie", "key": "c", "build": 60, "flags": ["after:b"]},
             {"name": "Delta", "key": "d", "build": 10, "flags": ["after:c"]}]
    for it in items:
        if it["key"] in held:
            it["paused_until"] = at(NOW + 24 * HOUR)
    p.plan({"release": "1.4.0", "items": items})
    p.notes()
    engine = p.engine()
    f = engine.build_frame(120)
    panel, release_at = release_panel(f, 120)
    plain = testing.render_text(panel, 120)[0]
    assert eta(row(plain, "Delta")) == "~1d03h · Tue 17:23"  # c's end, then d's 10 min
    for name in ("Alpha", "Bravo", "Charlie"):
        assert ("paused" in row(plain, name)) is (name[0].lower() in held)
    # the release's finish: the chain, then the final merge (3h30) and the tag row (2h30)
    assert release_at - NOW == pytest.approx((24 * 60 + 3 * 60 + 10 + 210 + 150) * 60, abs=1)


def test_a_hold_that_ended_soon_after_its_run_was_stopped_reads_not_resumed(tmp_path):
    """a11y's run was stopped 10 min ago and its hold ended 5 min ago: nothing has run since. Its agent,
    silent for 10 min, read as still at work until 25 min of silence, so the row read its build running
    with a finish time. The runtime's record of the stop ends it at once: "hold ended", "not resumed"."""
    p = Project(tmp_path)
    midway(p)
    hold(p, a11y=-5 * MIN)
    p.runs.clear()
    p.agent("build:a11y", start_ago=HOUR, quiet_s=10 * MIN, run="wf_run-z")
    audit = row(release_text(p), "Accessibility audit")
    assert "build" in audit and "hold ended" not in audit and "·" in eta(audit)  # the silence rule alone
    p.run_end("wf_run-z", "killed", ago_s=10 * MIN)
    audit = row(release_text(p), "Accessibility audit")
    assert "hold ended" in audit and eta(audit) == "not resumed"


def test_a_hold_that_ended_before_the_owners_stage_reads_that_stage(tmp_path):
    """nod's hold ended an hour ago with nothing of it run, and its next stage is the owner's: it waits
    on the owner (the header counts it), never "not resumed"."""
    p = Project(tmp_path)
    p.plan({"release": "1.4.0", "items": [
        {"name": "Nod one", "key": "nod", "build": 30, "before": [["your nod", None, 0]],
         "paused_until": at(NOW - HOUR)}]})
    p.notes()
    plain = release_text(p)
    assert "your nod" in row(plain, "Nod one") and "1 wait on you" in plain and "not resumed" not in plain


def test_a_finished_item_held_to_a_time_adds_nothing(tmp_path):
    """d is done, its hold a day off: what runs after it waits on nothing, and the release's finish is
    n's 30 min, then the final merge (3h30) and the tag row (2h30)."""
    p = Project(tmp_path)
    p.plan({"release": "1.4.0", "items": [
        {"name": "Done one", "key": "d", "build": 30, "paused_until": at(NOW + 24 * HOUR)},
        {"name": "Next one", "key": "n", "build": 30, "flags": ["after:d"]}]})
    p.notes()
    p.agent("build:d", status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR)
    p.agent("review:d", status="done", start_ago=2 * HOUR, quiet_s=90 * MIN, findings=[])
    panel, release_at = release_panel(p.engine().build_frame(120), 120)
    assert eta(row(testing.render_text(panel, 120)[0], "Next one")).startswith("~30m")
    assert release_at - NOW == pytest.approx((30 + 210 + 150) * 60, abs=1)


def test_a_begun_held_row_after_a_longer_wait_counts_the_longer(tmp_path):
    """b, 20 of its 60 min of build run, is held until an hour from now and runs after a (9h50 of build
    left). Its hold and its 40 min run beside that wait: b's time left is a's, so the release is out
    9h50 from now, then the final merge and the tag row, not an hour and 40 min later (the hold and
    the begun work added on top of the wait)."""
    p = Project(tmp_path)
    p.plan({"release": "1.4.0", "items": [
        {"name": "Alpha", "key": "a", "build": 600},
        {"name": "Bravo", "key": "b", "build": 60, "flags": ["after:a"], "paused_until": at(NOW + HOUR)}]})
    p.notes()
    p.agent("build:a", start_ago=10 * MIN, quiet_s=20)
    p.agent("build:b", start_ago=40 * MIN, quiet_s=20 * MIN, run="wf_run-b")
    panel, release_at = release_panel(p.engine().build_frame(120), 120)
    plain = testing.render_text(panel, 120)[0]
    assert "~9h50" in row(plain, "Alpha") and "paused" in row(plain, "Bravo")
    assert release_at - NOW == pytest.approx((590 + 210 + 150) * 60, abs=1)

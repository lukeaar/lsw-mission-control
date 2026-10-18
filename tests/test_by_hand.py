"""A stage done by hand (`{"done": "<ISO time>"}`), drawn. Work done with no agent and no job could only
be marked done by taking a whole release item's key away (or the row out of the plan): a `null` stage
counts as done only once a later stage has begun, so the row read the hand-done stage as still to come."""

from __future__ import annotations

from lsw_mission_control import testing
from lsw_mission_control.render.next_release import next_panel
from lsw_mission_control.render.other import other_panel
from lsw_mission_control.render.release import release_panel

from conftest import NOW
from scenarios import HOUR, MIN, Project, midway, release_plan, ts


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def text(panel_of, p: Project, width: int = 120) -> str:
    out = panel_of(p.engine().build_frame(width), width)
    return testing.render_text(out[0] if isinstance(out, tuple) else out, width)[0]


def test_other_work_reads_a_stage_done_by_hand_as_done(tmp_path):
    """midway's docs draft is at work: an outline done by hand before it reads ●; a row done by hand
    from end to end is done, with no agent and no job behind it"""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["other"] = [
        {"name": "Docs site refresh", "stages": [["outline", {"done": ts(NOW - 2 * HOUR)}, 15],
                                                 ["draft", "docs:draft", 60], ["review", "docs:review", 20]]},
        {"name": "Hand-checked links", "stages": [["check", {"done": ts(NOW - HOUR)}, 30],
                                                  ["sign-off", {"done": ts(NOW - 30 * MIN)}, 5]]},
        {"name": "Changelog by hand", "stages": [["write", {"done": ts(NOW - HOUR)}, 30], ["proofread", None, 10]]}]
    p.plan(plan)
    plain = text(other_panel, p)
    docs = row(plain, "Docs site refresh")
    assert "●─◉─○" in docs and "draft" in docs and "~" in docs
    done = row(plain, "Hand-checked links")
    assert "●─●" in done and "done" in done and "100%" in done
    # the stage after one done by hand, with no agent, still waits to be done (or marked) itself
    changelog = row(plain, "Changelog by hand")
    assert "●─○" in changelog and "queued" in changelog and "~10m" in changelog
    assert "2 in progress" in plain


def test_a_release_items_stage_done_by_hand(tmp_path):
    """export's design marked done by hand where its agent never ran, and its sign-off too: the row
    reads its build next, queued, and counts the hand-done stages as work behind it"""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][1]["key"] = "export2"  # a key no agent of midway's has worked on
    plan["items"][1]["before"] = [["design", {"done": ts(NOW - 3 * HOUR)}, 30],
                                  ["your sign-off", {"done": ts(NOW - 2 * HOUR)}, 0]]
    p.plan(plan)
    export = row(text(release_panel, p), "Export to CSV")
    assert "●─●─○─○─○" in export and "queued" in export and "your sign-off" not in export


def test_an_owner_ok_item_with_a_stage_done_by_hand_has_begun(tmp_path):
    """a stage done by hand is work begun: the item no longer waits for the go-ahead"""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][5]["before"] = [["scope", {"done": ts(NOW - HOUR)}, 20]]
    p.plan(plan)
    security = row(text(release_panel, p), "Security review")
    assert "your go-ahead" not in security and "after all" in security


def test_a_hold_that_ended_is_resumed_by_a_stage_done_by_hand_since(tmp_path):
    """a11y's hold ended an hour ago: a stage of it done by hand since then is the work resumed (it
    reads its real stage); one done by hand before then is not (`hold ended` · `not resumed`)"""
    p = Project(tmp_path)
    midway(p)
    for done_ago, resumed in ((30 * MIN, True), (2 * HOUR, False)):
        plan = release_plan()
        plan["items"][4]["paused_until"] = ts(NOW - HOUR)
        plan["items"][4]["before"] = [["triage", {"done": ts(NOW - done_ago)}, 15]]
        p.plan(plan)
        audit = row(text(release_panel, p), "Accessibility audit")
        assert ("hold ended" not in audit) == resumed and ("not resumed" in audit) != resumed


def test_a_planned_items_pick_done_by_hand_starts_it(tmp_path):
    """the next release's item whose first stage is the owner's: done by hand, the item has begun and
    reads its next stage with a time, where it waited on the owner"""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["next"] = {"release": "1.5.0", "items": [
        {"key": "sync", "name": "Offline sync", "group": "first", "before": [["your pick", None, 0]], "build": 240}]}
    p.plan(plan)
    assert "your pick" in row(text(next_panel, p), "Offline sync")
    plan["next"]["items"][0]["before"] = [["your pick", {"done": ts(NOW - HOUR)}, 0]]
    p.plan(plan)
    plain = text(next_panel, p)
    sync = row(plain, "Offline sync")
    assert "●─○" in sync and "queued" in sync and "~4h00" in sync and "wait on you" not in plain


# ── an item's build, review or fix done by hand (`by_hand`) ──────────────────────────────────────────
def test_a_release_item_done_by_hand_from_end_to_end_reads_done_with_its_key(tmp_path):
    """a11y's build agent failed; the work was then done by hand, build, review and fix: the row reads
    done, counted as finished and ready, where only taking its key away (null) could say so"""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][4]["by_hand"] = {"build": ts(NOW - 3 * HOUR), "review": ts(NOW - 2 * HOUR), "fix": ts(NOW - HOUR)}
    p.plan(plan)
    plain = text(release_panel, p)
    assert "Accessibility audit" not in plain and "● 2 finished" in plain and "2/6 items ready" in plain
    assert "failed" not in plain.splitlines()[1]  # the header: a11y's failed build no longer counts
    # its build alone done by hand: the review is next, queued
    plan["items"][4]["by_hand"] = {"build": ts(NOW - 3 * HOUR)}
    p.plan(plan)
    audit = row(text(release_panel, p), "Accessibility audit")
    assert "●─○─○" in audit and "queued" in audit and "needs rerun" not in audit


def test_an_owner_ok_item_whose_build_was_done_by_hand_has_begun(tmp_path):
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][5]["by_hand"] = {"build": ts(NOW - HOUR)}
    p.plan(plan)
    security = row(text(release_panel, p), "Security review")
    assert "your go-ahead" not in security and "●─○─○" in security and "after all" in security


def test_a_hold_that_ended_is_resumed_by_a_build_done_by_hand_since(tmp_path):
    p = Project(tmp_path)
    midway(p)
    for done_ago, resumed in ((30 * MIN, True), (2 * HOUR, False)):
        plan = release_plan()
        plan["items"][4]["paused_until"] = ts(NOW - HOUR)
        plan["items"][4]["by_hand"] = {"build": ts(NOW - done_ago)}
        p.plan(plan)
        audit = row(text(release_panel, p), "Accessibility audit")
        assert ("hold ended" not in audit) == resumed and ("not resumed" in audit) != resumed


def test_a_planned_items_build_done_by_hand_starts_it(tmp_path):
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["next"] = {"release": "1.5.0", "items": [
        {"key": "sync", "name": "Offline sync", "build": 240, "review": 45, "by_hand": {"build": ts(NOW - HOUR)}}]}
    p.plan(plan)
    plain = text(next_panel, p)
    sync = row(plain, "Offline sync")
    assert "●─○" in sync and "queued" in sync and "~45m" in sync and "1 under way" in plain

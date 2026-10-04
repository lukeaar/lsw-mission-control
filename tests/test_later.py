"""The releases after the next one (the plan's `later`): each is its own panel, in plan order, right
after the next release's. An item not yet begun waits on the release before its own, never on this
one: the bug they pin had a release two out drawn as a group at the bottom of the next release's
panel, its first stage naming this release's install."""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from lsw_mission_control import testing
from lsw_mission_control.agents import FinishedStore, label_names
from lsw_mission_control.config import FinalMergeCfg
from lsw_mission_control.plan import parse_plan
from lsw_mission_control.render.next_release import later_panel, next_panel

from conftest import NOW
from scenarios import HOUR, MIN, Project, later_releases

LATER_17 = {"release": "1.7.0", "items": [
    {"key": "sso", "name": "Single sign-on", "group": "first", "build": 120, "review": 30},
    {"key": "audit", "name": "Audit log export", "group": "first", "before": [["your pick of fields", None, 0]],
     "build": 45}]}


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def titles(plain: str) -> list[str]:
    """Every panel's title, top to bottom."""
    return [m.group(1) for m in re.finditer(r"╭─\s+(.+?)\s+─", plain)]


def with_two_later(p: Project) -> None:
    later_releases(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    plan["later"].append(LATER_17)
    p.plan(plan)


def later_text(p: Project, i: int = 0, width: int = 150) -> str:
    return testing.render_text(later_panel(p.engine().build_frame(width), width, i), width)[0]


def next_text(p: Project, width: int = 150) -> str:
    return testing.render_text(next_panel(p.engine().build_frame(width), width), width)[0]


def load(p: Project) -> dict:
    return json.loads((p.dot / "status_plan.json").read_text())


LONG = {"key": "long", "name": "Long one", "before": [[f"step {i}", None, 0] for i in range(12)],
        "build": 10, "review": 10, "fix": 10}  # 15 stages: 29 cells of dots


def test_each_later_release_is_its_own_panel_right_after_the_next(tmp_path):
    p = Project(tmp_path)
    with_two_later(p)
    plain, _ = testing.render_engine(p.engine(), 150)
    shown = titles(plain)
    at = shown.index("Next release 1.5.0")
    assert shown[at:at + 4] == ["Next release 1.5.0", "Later release 1.6.0", "Later release 1.7.0",
                                "Other work in progress"]
    # the next release's panel holds only its own items
    nxt = testing.render_text(next_panel(p.engine().build_frame(150), 150), 150)[0]
    assert "Plugin API" not in nxt and "Single sign-on" not in nxt


def test_an_item_not_begun_waits_on_the_release_before_its_own(tmp_path):
    p = Project(tmp_path)
    with_two_later(p)
    r16, r17 = later_text(p, 0), later_text(p, 1)
    for name in ("First week of 1.5.0", "Plugin API"):
        r = row(r16, name)
        assert "after 1.5.0" in r and r.rstrip(" │").endswith("—")  # planned, not scheduled: no finish time
    assert "after 1.6.0" in row(r17, "Single sign-on")
    # never this release: 1.4.0 is not what a release two out waits for
    assert "1.4.0" not in r16 + r17


def test_an_owners_first_stage_waits_for_the_release_before_too(tmp_path):
    """"your scope" is the owner's, but not yet: the item waits on 1.5.0, and the header does not
    count it as waiting on the owner (the next release's "your pick" still does)."""
    p = Project(tmp_path)
    with_two_later(p)
    r16, r17 = later_text(p, 0), later_text(p, 1)
    assert "after 1.5.0" in row(r16, "Plugin API") and "your scope" not in r16
    assert "after 1.6.0" in row(r17, "Audit log export")
    assert "wait on you" not in r16 + r17
    nxt = testing.render_text(next_panel(p.engine().build_frame(150), 150), 150)[0]
    assert "1 wait on you" in nxt and "your pick" in row(nxt, "Pick the sync engine")


def test_an_item_after_another_of_its_release_reads_after_it(tmp_path):
    p = Project(tmp_path)
    later_releases(p)
    themes = row(later_text(p), "Themes as plugins")
    assert "after plugin" in themes and themes.rstrip(" │").endswith("—")


def test_an_item_begun_early_reads_its_stage_and_time(tmp_path):
    """search's design began 25 min ago (45 planned): 20 min left, then build 120 and review 30."""
    p = Project(tmp_path)
    later_releases(p)
    plain = later_text(p)
    search = row(plain, "Search across workspaces")
    assert "design" in search and "after" not in search and "~2h50 · 17:03" in search
    assert "4 items planned" in plain and "1 under way" in plain and "once 1.5.0 is out" in plain


def test_without_a_next_release_the_first_later_one_comes_after_this_one(tmp_path):
    p = Project(tmp_path)
    later_releases(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    del plan["next"]
    p.plan(plan)
    plain, _ = testing.render_engine(p.engine(), 150)
    assert "Next release" not in plain and "Later release 1.6.0" in titles(plain)
    assert "after 1.4.0" in row(later_text(p), "Plugin API")


def test_a_later_release_with_no_items_has_no_panel(tmp_path):
    p = Project(tmp_path)
    later_releases(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    plan["later"] = [{"release": "1.6.0", "items": []}, LATER_17]
    p.plan(plan)
    f = p.engine().build_frame(120)
    assert later_panel(f, 120, 0) is None
    plain, _ = testing.render_engine(p.engine(), 150)
    assert "Later release 1.6.0" not in plain and "after 1.6.0" in row(plain, "Single sign-on")


def test_a_release_of_one_item(tmp_path):
    p = Project(tmp_path)
    with_two_later(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    plan["later"][1]["items"] = plan["later"][1]["items"][:1]
    p.plan(plan)
    assert "1 item planned" in later_text(p, 1) and "4 items planned" in later_text(p, 0)


def test_the_title_and_the_wait_are_the_projects_words(tmp_path):
    p = Project(tmp_path)
    later_releases(p)
    p.write_config("""
        [release]
        later_title = "Then {release}"
        later_wait = "{release} installed"
        """)
    plain, _ = testing.render_engine(p.engine(), 150)
    assert "Then 1.6.0" in titles(plain) and "1.5.0 installed" in row(plain, "Plugin API")


def test_a_later_panel_that_fails_shows_its_error_in_its_own_place(tmp_path, monkeypatch):
    import lsw_mission_control.engine as eng

    p = Project(tmp_path)
    with_two_later(p)
    real = eng.later_panel

    def boom(f, width, i):
        if i == 0:
            raise KeyError("items")
        return real(f, width, i)

    monkeypatch.setattr(eng, "later_panel", boom)
    engine = p.engine()
    plain, _ = testing.render_engine(engine, 150)
    shown = titles(plain)
    at = shown.index("Next release 1.5.0")
    assert shown[at:at + 3] == ["Next release 1.5.0", "Mission control error", "Later release 1.7.0"]
    assert "KeyError: 'items'" in plain and "later[0] failed: Traceback" in engine.errors_for_once()


def test_a_later_panel_that_fails_to_draw_is_contained_and_named(tmp_path, monkeypatch):
    """A failure while DRAWING (markup in a cell) shows in the panel's place, names the panel, and
    fails --once and the reload gate."""
    from rich.table import Table

    import lsw_mission_control.engine as eng

    def markup(*_a, **_k):
        t = Table.grid()
        t.add_column()
        t.add_row("see [/docs] first")  # builds fine; fails only when drawn
        return t

    p = Project(tmp_path)
    with_two_later(p)
    monkeypatch.setattr(eng, "later_panel", markup)
    engine = p.engine()
    plain, _ = testing.render_engine(engine, 150)
    assert "drawing the later[1] panel" in plain and "Next release 1.5.0" in titles(plain)
    assert "later[0] failed: Traceback" in engine.errors_for_once() and "later[1] failed" in engine.errors_for_once()


def test_a_later_items_agents_are_named_and_kept(tmp_path):
    """Agents at work names a later item's agent after it, and the finished store keeps its stages
    when the release changes (work begun early for a release two out is not lost)."""
    plan = parse_plan({"release": "1", "items": [], "next": {"release": "2", "items": []},
                       "later": [{"release": "3", "items": [{"key": "s", "name": "Search", "before": [
                           ["design", "design:s", 45]], "build": 60}]}]})
    names = label_names(plan, FinalMergeCfg())
    assert names["design:s"] == "Search" and names["build:s"] == "Search"
    store = FinishedStore(tmp_path / "finished.json")
    rec = {"id": "d1", "label": "design:s", "phase": "", "status": "done", "run": "wf_1", "t0": 1.0, "t1": 2.0,
           "result": None, "action": "", "logs": frozenset()}
    store.merge([rec], plan, names)
    moved = parse_plan({"release": "2", "items": [], "next": {"release": "3", "items": [
        {"key": "s", "name": "Search", "before": [["design", "design:s", 45]], "build": 60}]}})
    assert [a["label"] for a in store.merge([], moved, label_names(moved, FinalMergeCfg()))] == ["design:s"]


def test_a_begun_later_items_agent_is_at_work(tmp_path):
    from lsw_mission_control.render.agents import agents_panel

    p = Project(tmp_path)
    later_releases(p)
    plain = testing.render_text(agents_panel(p.engine().build_frame(120), 120, []), 120)[0]
    assert "design:search" in plain and "Search across workspaces" in row(plain, "design:search")


@pytest.mark.parametrize("width", (80, 100))
def test_a_later_releases_long_item_leaves_the_next_panel_as_it_was(tmp_path, width):
    """Each planned panel sizes its stage column by its own items. With one width for every planned
    release, a 15-stage item added to 1.6.0 set the 1.5.0 panel's stage column too: at 100 columns
    "Pick the sync e…" read "Pick the syn…"; at 80 every name was gone and the finish time cut to
    "~25m ·"."""
    p = Project(tmp_path)
    later_releases(p)
    plain = next_text(p, width)
    assert row(plain, "Pick ") and "~25m · 14:38" in plain
    plan = load(p)
    plan["later"][0]["items"].append(LONG)
    p.plan(plan)
    assert next_text(p, width) == plain


@pytest.mark.parametrize("width", (80, 100))
@pytest.mark.parametrize("where", ("next", "later"))
def test_a_long_item_keeps_every_name_and_finish_time_in_its_own_panel(tmp_path, where, width):
    """A 15-stage item in its own panel: at 80 columns its dots are cut ("…") so that every name keeps
    6 cells and every finish time is whole (its 29 cells of dots left the names a column of -7, which
    ran each row past the panel's edge); at 100 they fit whole."""
    p = Project(tmp_path)
    later_releases(p)
    plan = load(p)
    rel = plan["next"] if where == "next" else plan["later"][0]
    rel["items"].append(LONG)
    p.plan(plan)
    plain = next_text(p, width) if where == "next" else later_text(p, 0, width)
    for it in rel["items"]:
        assert row(plain, it["name"][:5]), it["name"]
    assert ("~25m · 14:38" if where == "next" else "~2h50 · 17:03") in plain
    dots = "─".join("○" * 15)
    assert (dots in row(plain, "Long")) is (width == 100)
    assert width == 100 or "○─○─○─○─○─○─○─○…" in row(plain, "Long")


def test_an_after_naming_another_releases_item_is_ignored(tmp_path):
    """`after:<key>` names an item of the same release; one of another release's is ignored
    (plan-schema.md), so search (1.6.0, begun early) reads its own stage and time with `after:sync`
    (1.5.0, not begun) as without it. A wait across releases would need every planned panel's waits
    worked out together, and a plan may already carry such keys: its own change, not this one."""
    p = Project(tmp_path)
    later_releases(p)
    before = row(later_text(p), "Search across workspaces")
    plan = load(p)
    for it in plan["later"][0]["items"]:
        if it["key"] == "search":
            it["flags"] = ["after:sync"]
    p.plan(plan)
    assert row(later_text(p), "Search across workspaces") == before and "~2h50 · 17:03" in before


def test_a_planned_item_has_no_hold(tmp_path):
    """`paused` and `paused_until` are read on release items (and `paused` on other work) only: on a
    later item, as on a next one, they change nothing (plan-schema.md). search began early and its
    run stopped 2 h ago, so it reads as any stopped planned item does, held or not. A hold on a
    planned item (paused, its agents out of Agents at work, its end) would be its own change."""
    p = Project(tmp_path)
    later_releases(p)
    p.runs.clear()
    p.agent("build:icons", start_ago=15 * MIN, quiet_s=20)
    p.agent("design:search", start_ago=3 * HOUR, quiet_s=2 * HOUR, run="wf_run-d")  # stopped: silent 2 h
    before = row(later_text(p), "Search across workspaces")
    plan = load(p)
    for it in plan["later"][0]["items"]:
        if it["key"] == "search":
            it["paused"] = True
            it["paused_until"] = dt.datetime.fromtimestamp(NOW + HOUR, tz=dt.timezone.utc).isoformat()
    p.plan(plan)
    assert row(later_text(p), "Search across workspaces") == before


def test_a_label_a_later_item_shares_keeps_naming_the_nearer_one():
    """A key typed again in a later release: Agents at work keeps naming the nearer release's item, or
    the other work, the label belongs to (the later release was named last, and took it over)."""
    plan = parse_plan({
        "release": "1", "items": [{"key": "a", "name": "This release's A", "build": 10}],
        "other": [{"name": "Other O", "stages": [["run", "run:o", 5]]}],
        "next": {"release": "2", "items": [{"key": "n", "name": "Next N", "build": 5}]},
        "later": [{"release": "3", "items": [{"key": "a", "name": "Later A", "build": 5},
                                             {"key": "n", "name": "Later N", "build": 5},
                                             {"key": "l", "name": "Later L", "before": [["run", "run:o", 5]],
                                              "build": 5}]},
                  {"release": "4", "items": [{"key": "l", "name": "Even later L", "build": 5}]}]})
    names = label_names(plan, FinalMergeCfg())
    assert (names["build:a"], names["build:n"], names["run:o"], names["build:l"]) == (
        "This release's A", "Next N", "Other O", "Later L")

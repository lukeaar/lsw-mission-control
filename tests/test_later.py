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
    r = row(r16, "First week of 1.5.0")
    assert "after 1.5.0" in r and r.rstrip(" │").endswith("—")  # planned, not scheduled: no finish time
    assert "after 1.6.0" in row(r17, "Single sign-on")
    # never this release: 1.4.0 is not what a release two out waits for
    assert "1.4.0" not in r16 + r17


def test_an_owners_first_stage_in_a_later_release_waits_on_the_owner(tmp_path):
    """"your scope" is the owner's, a decision the owner can make now: a later item whose first stage is
    the owner's reads that stage and counts as waiting on the owner, as a next item does. It read the
    release before (`after 1.5.0`) and was not counted, so the decision stayed out of sight until that
    release shipped. It has no finish time, and neither has what runs after it."""
    p = Project(tmp_path)
    with_two_later(p)
    r16, r17 = later_text(p, 0), later_text(p, 1)
    plugin = row(r16, "Plugin API")
    assert "your scope" in plugin and plugin.rstrip(" │").endswith("—") and "1 wait on you" in r16
    assert "after plugin" in row(r16, "Themes as plugins")
    assert "your pick of" in row(r17, "Audit log export") and "1 wait on you" in r17
    assert "after 1.6.0" in row(r17, "Single sign-on")
    nxt = testing.render_text(next_panel(p.engine().build_frame(150), 150), 150)[0]
    assert "1 wait on you" in nxt and "your pick" in row(nxt, "Pick the sync engine")


@pytest.mark.parametrize("where", ("next", "later"))
def test_a_stage_that_failed_before_the_owners_reads_the_failure(tmp_path, where):
    """A begun item whose measure failed, with the owner's go-ahead after it: the failure needs a re-run
    before the owner has anything to say, so the row reads the failure, never "your go-ahead" (the
    planned panels read the first stage not done, never the first not begun; a ✕ comes first)."""
    p = Project(tmp_path)
    later_releases(p)
    plan = load(p)
    rel = plan["next"] if where == "next" else plan["later"][0]
    rel["items"].append({"key": "gauge", "name": "Gauge the cache", "before": [
        ["measure", "measure:gauge", 30], ["your go-ahead", None, 0]], "build": 30})
    p.plan(plan)
    p.agent("measure:gauge", status="failed", start_ago=2 * HOUR, quiet_s=90 * MIN, run="wf_run-g")
    plain = next_text(p) if where == "next" else later_text(p)
    gauge = row(plain, "Gauge")
    assert "measure failed" in gauge and "needs rerun" in gauge and "your go-ahead" not in gauge


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
    assert "after 1.4.0" in row(later_text(p), "First week")


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
    assert "Then 1.6.0" in titles(plain) and "1.5.0 installed" in row(plain, "First week")


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


def test_an_after_naming_another_releases_item_waits_for_it(tmp_path):
    """`after:<key>` may name an item of another release: every planned release's rows are worked out
    together. search (1.6.0, begun early, its design running) runs after sync (1.5.0, not begun: no
    finish time), so its design runs on beside the wait and it has none either; the rollout, not
    begun, reads the wait on sync instead of the one on 1.5.0. The key was ignored: search read its own
    time (~2h50 · 17:03) and the rollout "after 1.5.0"."""
    p = Project(tmp_path)
    later_releases(p)
    plan = load(p)
    for it in plan["later"][0]["items"]:
        if it["key"] in ("search", "rollout"):
            it["flags"] = ["after:sync"]
    p.plan(plan)
    plain = later_text(p)
    search, rollout = row(plain, "Search across workspaces"), row(plain, "First week")
    assert "design" in search and search.rstrip(" │").endswith("—")
    assert "after offline" in rollout and rollout.rstrip(" │").endswith("—")


def cross(tmp_path) -> Project:
    """This release's export (90 of its 120 min of build left), 1.5.0's sync after it (begun: 10 min into
    an hour of build) and 1.6.0's themes after sync (begun: 5 min into half an hour), and two not begun:
    late after export, and lone after a key no release holds."""
    p = Project(tmp_path)
    p.plan({"release": "1.4.0", "items": [{"name": "Export to CSV", "key": "export", "build": 120}],
            "next": {"release": "1.5.0", "items": [
                {"key": "sync", "name": "Offline sync", "build": 60, "flags": ["after:export"]}]},
            "later": [{"release": "1.6.0", "items": [
                {"key": "themes", "name": "Themes as plugins", "build": 30, "flags": ["after:sync"]},
                {"key": "late", "name": "Late one", "build": 30, "flags": ["after:export"]},
                {"key": "lone", "name": "Lone one", "build": 30, "flags": ["after:nowhere"]}]}]})
    p.notes()
    p.agent("build:export", start_ago=30 * MIN, quiet_s=20)
    p.agent("build:sync", start_ago=10 * MIN, quiet_s=20, run="wf_run-s")
    p.agent("build:themes", start_ago=5 * MIN, quiet_s=20, run="wf_run-t")
    return p


def test_a_planned_item_after_one_of_this_releases_waits_for_its_time(tmp_path):
    """sync never finishes before export, this release's item it runs after: ~1h30, not its own ~50m;
    themes, after sync in the release after, waits on through it (~1h30, not ~25m). late, not begun,
    reads the wait on export; lone's key names nothing, so it reads the wait on 1.5.0."""
    p = cross(tmp_path)
    nxt, later = next_text(p), later_text(p)
    assert "~1h30 · 15:43" in row(nxt, "Offline sync") and "build" in row(nxt, "Offline sync")
    assert "~1h30 · 15:43" in row(later, "Themes as plugins")
    assert "after export" in row(later, "Late one") and "after 1.5.0" in row(later, "Lone one")
    # this release's own panel reads as it did: its items wait on nothing of a release after it
    from lsw_mission_control.render.release import release_panel
    release = testing.render_text(release_panel(p.engine().build_frame(150), 150)[0], 150)[0]
    assert "~1h30 · 15:43" in row(release, "Export to CSV")


def test_a_key_names_its_own_release_first_then_the_nearest_before(tmp_path):
    """"cache" is an item of this release and of 1.5.0, one agent label for both (build:cache, 10 min in):
    this release's has 50 of its hour left, 1.5.0's 110 of its two hours. 1.6.0's mirror (25 min left)
    runs after 1.5.0's, the nearer release before it: ~1h50, not ~50m. 1.5.0's warm runs after its own
    release's, and a key only a release after a row's holds still counts: 1.5.0's early runs after
    1.6.0's mirror."""
    p = Project(tmp_path)
    p.plan({"release": "1.4.0", "items": [{"name": "Old cache", "key": "cache", "build": 60}],
            "next": {"release": "1.5.0", "items": [
                {"key": "cache", "name": "New cache", "build": 120},
                {"key": "warm", "name": "Warm it", "build": 30, "flags": ["after:cache"]},
                {"key": "early", "name": "Early one", "build": 30, "flags": ["after:mirror"]}]},
            "later": [{"release": "1.6.0", "items": [
                {"key": "mirror", "name": "Mirror", "build": 30, "flags": ["after:cache"]}]}]})
    p.notes()
    p.agent("build:cache", start_ago=10 * MIN, quiet_s=20)
    p.agent("build:mirror", start_ago=5 * MIN, quiet_s=20, run="wf_run-m")
    nxt, later = next_text(p), later_text(p)
    assert "~1h50 · 16:03" in row(nxt, "New cache") and "after new" in row(nxt, "Warm it")
    assert "~1h50 · 16:03" in row(later, "Mirror") and "build" in row(later, "Mirror")
    assert "after mirror" in row(nxt, "Early one")


def test_a_cycle_across_releases_is_broken_and_every_row_draws(tmp_path):
    p = Project(tmp_path)
    p.plan({"release": "1.4.0", "items": [],
            "next": {"release": "1.5.0", "items": [{"key": "a", "name": "Alpha", "build": 30, "flags": ["after:b"]}]},
            "later": [{"release": "1.6.0", "items": [{"key": "b", "name": "Bravo", "build": 30, "flags": ["after:a"]}]}]})
    p.notes()
    plain, _ = testing.render_engine(p.engine(), 120)
    assert row(plain, "Alpha") and row(plain, "Bravo") and "Mission control error" not in plain


def at(t: float) -> str:
    return dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).isoformat()


def hold_planned(p: Project, key: str, **hold) -> None:
    """The plan with the planned item `key` given `hold` (paused, paused_until)."""
    plan = load(p)
    for rel in [plan["next"]] + plan["later"]:
        for it in rel["items"]:
            if it["key"] == key:
                it.update(hold)
    p.plan(plan)


def test_a_planned_item_can_be_held(tmp_path):
    """A hold on a planned item reads as a release item's. search (1.6.0) began early, its design at
    work: held, it reads "paused" with no finish of its own and is not under way, the header counts it,
    and its agent leaves Agents at work. A planned item had no hold: `paused` changed nothing."""
    from lsw_mission_control.render.agents import agents_panel

    p = Project(tmp_path)
    later_releases(p)
    hold_planned(p, "search", paused=True)
    plain = later_text(p)
    search = row(plain, "Search across workspaces")
    assert "paused" in search and search.rstrip(" │").endswith("—")
    assert "1 paused" in plain and "under way" not in plain
    agents = testing.render_text(agents_panel(p.engine().build_frame(120), 120, []), 120)[0]
    assert "design:search" not in agents and "build:icons" in agents


def test_a_planned_item_held_until_a_time_is_waited_for_with_its_hold(tmp_path):
    """icons (1.5.0, 15 min into its 30 min of build) is held until a day from now: it reads when it
    resumes, and polish, begun early after it, never finishes before that hold and icons' work are
    over: ~1d00h, the hold counted once."""
    p = Project(tmp_path)
    later_releases(p)
    plan = load(p)
    plan["next"]["items"].append({"key": "polish", "name": "Polish the icons", "group": "then", "build": 30,
                                  "flags": ["after:icons"]})
    p.plan(plan)
    hold_planned(p, "icons", paused_until=at(NOW + 24 * HOUR))
    p.agent("build:polish", start_ago=5 * MIN, quiet_s=20, run="wf_run-p")
    plain = next_text(p)
    icons, polish = row(plain, "New icon set"), row(plain, "Polish")
    assert "paused" in icons and icons.rstrip(" │").endswith("from Tue 14:13") and "1 paused" in plain
    assert "build" in polish and "~1d00h · Tue 14:38" in polish


def test_a_planned_items_hold_that_ended_with_nothing_resumed_says_so(tmp_path):
    """search's design stopped 2 h ago and its hold ended an hour ago, with nothing of it run since: it
    reads "hold ended" and "not resumed", and the header counts it. At work again, it reads its stage.
    rollout, not begun, reads the wait on the release before, as a held release item that waits reads
    its wait."""
    p = Project(tmp_path)
    later_releases(p)
    p.runs.clear()
    p.agent("build:icons", start_ago=15 * MIN, quiet_s=20)
    p.agent("design:search", start_ago=3 * HOUR, quiet_s=2 * HOUR, run="wf_run-d")
    hold_planned(p, "search", paused_until=at(NOW - HOUR))
    hold_planned(p, "rollout", paused_until=at(NOW - HOUR))
    plain = later_text(p)
    search = row(plain, "Search across workspaces")
    assert "hold ended" in search and search.rstrip(" │").endswith("not resumed") and "1 not resumed" in plain
    assert "after 1.5.0" in row(plain, "First week")
    p.agent("design:search", start_ago=10 * MIN, quiet_s=30, run="wf_run-e")
    search = row(later_text(p), "Search across workspaces")
    assert "design" in search and "hold ended" not in search


def test_a_planned_items_hold_is_read_from_the_plan():
    plan = parse_plan({"release": "1", "items": [], "next": {"release": "2", "items": [
        {"key": "a", "name": "A", "paused": True}, {"key": "b", "name": "B", "paused_until": "2026-10-04T13:00:00+10:00"},
        {"key": "c", "name": "C"}]}})
    a, b, c = plan.next.items
    assert (a.paused, a.paused_until, b.paused, b.paused_until, c.paused) == (True, None, True, 1791082800.0, False)
    assert a.held(NOW) and b.hold_ended(NOW + 400 * 86400) and not c.held(NOW)
    with pytest.raises(ValueError, match="paused_until of 'X'"):
        parse_plan({"release": "1", "items": [], "later": [{"release": "3", "items": [
            {"key": "x", "name": "X", "paused_until": "Sunday"}]}]})


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

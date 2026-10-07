"""The finished items of the releases after this one (the next release's and each later release's)
are one quiet row, `● N finished ✓`, below the rest, as the release panel draws its own; where the
panel draws group headings, a blank row sets it apart from the last group's rows. Each used to be a
row of its own ("done", a full bar, ✓) under its group: a release with much of its work begun early
listed every finished item, while its header already counted them."""

from __future__ import annotations

import json
import re

import pytest

from lsw_mission_control import testing
from lsw_mission_control.render.next_release import later_panel, next_panel

from conftest import NOW
from scenarios import HOUR, MIN, Project, later_releases, next_release

# A release with four items finished: one of "first", both of "spikes" (a group whose items have all
# finished), and one of "then"; the rest wait on the owner, wait on another item, or run.
ITEMS = [
    {"key": "decide", "name": "Pick the sync engine", "group": "first", "before": [["your pick", None, 0]], "build": 0},
    {"key": "proto", "name": "Sync prototype", "group": "first", "build": 60},
    {"key": "spike", "name": "Storage spike", "group": "spikes", "build": 30},
    {"key": "bench", "name": "Storage benchmark", "group": "spikes", "build": 30},
    {"key": "sync", "name": "Offline sync", "group": "first", "build": 240, "flags": ["after:decide"]},
    {"key": "icons", "name": "New icon set", "group": "then", "build": 30, "review": 10},
    {"key": "crash", "name": "Crash reporter opt-in", "group": "then", "build": 20},
]
FINISHED = ("proto", "spike", "bench", "crash")
DONE = tuple(f"build:{key}" for key in FINISHED)
# The widest item: 10 stages, 19 cells of dots (the stages column is never under 16).
BIG = {"key": "big", "name": "Many-staged item", "group": "then", "before": [[f"step {i}", f"step:{i}", 5] for i in range(9)],
       "build": 10}
STEPS = tuple(f"step:{i}" for i in range(9))


def next_text(p: Project, width: int = 150) -> str:
    return testing.render_text(next_panel(p.engine().build_frame(width), width), width)[0]


def later_text(p: Project, i: int = 0, width: int = 150) -> str:
    return testing.render_text(later_panel(p.engine().build_frame(width), width, i), width)[0]


def table(plain: str) -> list[str]:
    """The table's lines below its header row (`… stages … eta`: a narrow window squeezes the item
    column, and its `item`, away), top to bottom."""
    lines = plain.splitlines()
    at = next(i for i, line in enumerate(lines) if " stages " in line and line.rstrip(" │").endswith("eta"))
    rows = []
    for line in lines[at + 1:]:
        if not line.startswith("│"):
            break
        rows.append(line)
    return rows


def first_cells(plain: str) -> list[str]:
    """What each table line shows in its first column: an item's name, a group, `● N finished`, or
    nothing (a blank row)."""
    return [re.split(r"\s{2,}", line[2:].rstrip(" │"))[0] for line in table(plain)]


def head(plain: str) -> str:
    """The panel's header line (its counts)."""
    return plain.splitlines()[1]


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def planned(p: Project, where: str, done=DONE, running=("build:icons",), extra=()) -> None:
    """ITEMS (and `extra`) in the next release, or in the release after it (`later`): the labels
    `done` returned (four items finished) and those `running` are at work."""
    later_releases(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    if where == "next":
        plan["next"]["items"] = ITEMS + list(extra)
    else:
        plan["later"][0]["items"] = ITEMS + list(extra)
    p.plan(plan)
    p.runs.clear()
    for label in done:
        p.agent(label, status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR, run=f"wf_{label.replace(':', '-')}")
    for label in running:
        p.agent(label, start_ago=15 * MIN, quiet_s=20)


def with_items(p: Project, items: list[dict]) -> None:
    """`items` are the next release's, with no agent at work."""
    next_release(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    plan["next"]["items"] = items
    p.plan(plan)
    p.runs.clear()


def test_the_next_releases_finished_item_is_one_row_below_the_rest(tmp_path):
    p = Project(tmp_path)
    next_release(p)
    plain = next_text(p)
    assert first_cells(plain) == ["first", "Pick the sync engine", "Offline sync", "then",
                                  "Prefetch the next page of results", "New icon set", "", "● 1 finished"]
    assert table(plain)[-1].rstrip(" │").endswith("✓")
    assert "Crash reporter" not in plain and "1 done" in head(plain)


@pytest.mark.parametrize("where", ("next", "later"))
def test_finished_items_are_counted_once_and_drawn_as_one_row(tmp_path, where):
    """The four finished items go, wherever they stood: "spikes" had nothing else, so its heading goes
    too, and "first" is one run of rows again ("Offline sync" stood after the spikes). The header
    counts as it did."""
    p = Project(tmp_path)
    planned(p, where)
    plain = next_text(p) if where == "next" else later_text(p)
    assert first_cells(plain) == ["first", "Pick the sync engine", "Offline sync", "then", "New icon set", "",
                                  "● 4 finished"]
    assert table(plain)[-1].rstrip(" │").endswith("✓")
    assert "done" not in "\n".join(table(plain)) and "100%" not in plain
    assert head(plain).startswith("│ 7 items planned  ·  1 under way  ·  1 wait on you  ·  4 done  ·  ")
    # what is left reads as it did: the owner's pick (in a later release too), the wait on it, the one at work
    rows = dict(zip(first_cells(plain), table(plain)))
    pick, sync, icons = rows["Pick the sync engine"], rows["Offline sync"], rows["New icon set"]
    assert "your pick" in pick and "after pick" in sync
    assert "build" in icons and "~25m · 14:38" in icons


@pytest.mark.parametrize("where", ("next", "later"))
def test_a_release_whose_items_have_all_finished(tmp_path, where):
    """Nothing left in motion: the one row, with no group heading over nothing (and so no blank row)."""
    p = Project(tmp_path)
    later_releases(p)
    plan = json.loads((p.dot / "status_plan.json").read_text())
    rel = plan["next"] if where == "next" else plan["later"][0]
    rel["about"], rel["items"] = "all in", [it for it in ITEMS if it["key"] in FINISHED]
    p.plan(plan)
    p.runs.clear()
    for label in DONE:
        p.agent(label, status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR, run=f"wf_{label.replace(':', '-')}")
    plain = next_text(p) if where == "next" else later_text(p)
    assert first_cells(plain) == ["● 4 finished"]
    assert head(plain).startswith("│ 4 items planned  ·  4 done  ·  all in ")


def test_a_release_with_nothing_finished_has_no_finished_row(tmp_path):
    p = Project(tmp_path)
    later_releases(p)
    assert "finished" not in next_text(p) + later_text(p)


def test_the_finished_row_is_the_release_panels(tmp_path):
    """The same row as the release's own, cell for cell and colour for colour: the release panel and
    the next release's each have one finished item here."""
    p = Project(tmp_path)
    next_release(p)
    plain, styled = testing.render_engine(p.engine(), 150)
    rows = [line for line in plain.splitlines() if "● 1 finished" in line]
    styled_rows = [line for line in styled.splitlines() if "1 finished" in line]
    assert len(rows) == 2 and rows[0] == rows[1]
    assert len(styled_rows) == 2 and styled_rows[0] == styled_rows[1]


@pytest.mark.parametrize("width", (80, 100, 150))
def test_an_item_that_finishes_moves_no_other_row(tmp_path, width):
    """The widest item finishes: its row goes and the finished row says one more, and nothing else
    moves. The stages column is sized by every item the release holds, finished or not, as the release
    panel's is, so the other rows keep their dots and their names where they were."""
    p, q = Project(tmp_path / "before"), Project(tmp_path / "after")
    planned(p, "next", done=DONE + STEPS, running=("build:icons", "build:big"), extra=[BIG])
    planned(q, "next", done=DONE + STEPS + ("build:big",), extra=[BIG])
    before, after = next_text(p, width), next_text(q, width)
    # first, pick, sync, then, icons, big, a blank row, finished
    assert len(table(before)) == 8 and "4 done" in head(before)
    big = table(before)[5]  # at work, its dots whole from 100 columns (cut, ending in "…", at 80)
    assert "build" in big and ("●─" * 9 + "◉" in big) is (width > 80)
    assert table(after)[:5] == table(before)[:5]
    assert len(table(after)) == 7 and "5 done" in head(after)


def test_only_finished_items_roll_up_whatever_the_rest_are_doing(tmp_path):
    """Beside two finished items (a review that found nothing, so its fix is skipped, and a detached
    job with every unit in, then its build), each other state keeps its row and its count: a failed
    build (needs rerun), the owner's stage partway through an item, an item after the failed one,
    and an item after a finished one (no wait left: planned)."""
    units = tmp_path / "units.jsonl"
    p = Project(tmp_path / "p")
    with_items(p, [
        {"key": "flaky", "name": "Flaky build", "group": "a", "build": 30},
        {"key": "clean", "name": "Clean review", "group": "a", "build": 30, "review": 10, "fix": 20},
        {"key": "job", "name": "Thumbnail job", "group": "b",
         "before": [["rebuild", {"progress": str(units), "total": 3}, 60]], "build": 20},
        {"key": "signoff", "name": "Needs a sign-off", "group": "b",
         "before": [["measure", "measure:signoff", 30], ["your sign-off", None, 0]], "build": 30},
        {"key": "blocked", "name": "After the flaky one", "group": "b", "build": 30, "flags": ["after:flaky"]},
        {"key": "free", "name": "After the clean one", "group": "b", "build": 30, "flags": ["after:clean"]},
    ])
    p.agent("build:flaky", status="failed", start_ago=2 * HOUR, quiet_s=90 * MIN, run="wf_f")
    p.agent("build:clean", status="done", start_ago=5 * HOUR, quiet_s=4 * HOUR, run="wf_c")
    p.agent("review:clean", status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR, run="wf_c2")
    units.write_text("".join(json.dumps({"t": NOW - (4 - i) * HOUR}) + "\n" for i in range(3)))
    p.agent("build:job", status="done", start_ago=50 * MIN, quiet_s=40 * MIN, run="wf_j")
    p.agent("measure:signoff", status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR, run="wf_s")
    plain = next_text(p)
    assert first_cells(plain) == ["a", "Flaky build", "b", "Needs a sign-off", "After the flaky one",
                                  "After the clean one", "", "● 2 finished"]
    assert head(plain).startswith("│ 6 items planned  ·  1 under way  ·  1 wait on you  ·  2 done  ·  ")
    assert "build failed" in row(plain, "Flaky build") and "needs rerun" in row(plain, "Flaky build")
    assert "your sign-off" in row(plain, "Needs a sign-off")
    blocked, free = row(plain, "After the flaky one"), row(plain, "After the clean one")
    assert "after flaky" in blocked and blocked.rstrip(" │").endswith("—")
    assert "planned" in free and "after" not in free
    assert "Clean review" not in plain and "Thumbnail job" not in plain


@pytest.mark.parametrize("width", (40, 60, 75, 80))
def test_a_release_all_finished_draws_in_the_narrowest_windows(tmp_path, width):
    """Every item finished, at widths where the names column is cut to a cell or squeezed away: the
    panel draws, its one row keeps its ✓, and the header still counts them."""
    p = Project(tmp_path)
    with_items(p, [{"key": f"k{i}", "name": f"Finished item {i}", "group": "g" if i % 2 else "", "build": 20}
                   for i in range(12)])
    for i in range(12):
        p.agent(f"build:k{i}", status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR, run=f"wf_k{i}")
    plain = next_text(p, width)
    rows = table(plain)
    assert len(rows) == 1 and rows[0].rstrip(" │").endswith("✓")
    assert "12 done" in plain and "Finished item" not in plain


def test_without_groups_the_finished_row_follows_the_rows_as_the_release_panels_does(tmp_path):
    p = Project(tmp_path)
    with_items(p, [{"key": "a", "name": "Running item", "build": 30},
                   {"key": "b", "name": "Finished item", "build": 30}])
    p.agent("build:a", start_ago=10 * MIN, quiet_s=20, run="wf_a")
    p.agent("build:b", status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR, run="wf_b")
    assert first_cells(next_text(p)) == ["Running item", "● 1 finished"]


def test_the_finished_row_never_reads_as_the_last_groups_own(tmp_path):
    """Under group headings, a row right under the last group's rows reads as that group's own:
    `then` / `New icon set` / `● 3 finished` says three of `then` finished, when all three are of
    `first`. A blank row before it (only where the panel draws headings) sets it apart, as the blank
    row under the release panel's sets that one apart from the final merge."""
    p = Project(tmp_path)
    with_items(p, [
        {"key": "a1", "name": "Sync prototype", "group": "first", "build": 30},
        {"key": "a2", "name": "Storage spike", "group": "first", "build": 30},
        {"key": "a3", "name": "Storage benchmark", "group": "first", "build": 30},
        {"key": "a4", "name": "Offline sync", "group": "first", "build": 240},
        {"key": "icons", "name": "New icon set", "group": "then", "build": 30},
    ])
    for key in ("a1", "a2", "a3"):
        p.agent(f"build:{key}", status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR, run=f"wf_{key}")
    p.agent("build:a4", start_ago=20 * MIN, quiet_s=20, run="wf_a4")
    p.agent("build:icons", start_ago=15 * MIN, quiet_s=20, run="wf_icons")
    plain = next_text(p)
    assert first_cells(plain) == ["first", "Offline sync", "then", "New icon set", "", "● 3 finished"]
    assert table(plain)[-2].strip(" │") == ""

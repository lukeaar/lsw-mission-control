"""The owner's two rules for a plan's items, kept by the engine so that no plan can break them.

1. An item holds at most MAX_STAGES (5) stages: its `before` (or an other row's `stages`), and each of
   build, review and fix it draws. A plan with an item over it is refused at load, naming each, with
   no config to raise or disable the cap.
2. A release item whose build, review and fix are all 0 draws none of them, and is done when its
   `before` stages are. It used to draw three empty stages and read "queued" after its last one, so a
   plan marked them done by hand to finish it."""

from __future__ import annotations

import json
import os

import pytest

from lsw_mission_control import plan as plan_module
from lsw_mission_control import testing
from lsw_mission_control.plan import MAX_STAGES, PlanLoader, TooManyStages, parse_plan
from lsw_mission_control.progress import Calibration, calibrate, item_progress, item_stages, item_started
from lsw_mission_control.render.release import release_panel

from conftest import NOW
from scenarios import HOUR, Project, midway, release_plan, ts

SECTIONS = ("items", "items 0/0/0", "other", "next", "later")


def steps(n: int) -> list:
    return [[f"step {i}", None, 0] for i in range(n)]


def item_of(section: str, n: int, name: str = "Big") -> dict:
    """An item of `section` that draws `n` stages."""
    if section == "items":  # its before stages, then build, review and fix: a 0 among them is drawn too
        return {"name": name, "key": name, "before": steps(n - 3), "build": 10, "review": 5, "fix": 0}
    if section == "items 0/0/0":  # its before stages only
        return {"name": name, "key": name, "before": steps(n), "build": 0, "review": 0, "fix": 0}
    if section == "other":
        return {"name": name, "stages": steps(n)}
    # a planned item: build, review and fix only above 0 minutes (fix 0, and a by_hand fix, draw none)
    return {"name": name, "key": name, "before": steps(n - 2), "build": 10, "review": 5, "fix": 0,
            "by_hand": {"fix": "2026-09-21T14:13:20Z"}}


def plan_with(section: str, item: dict) -> dict:
    raw: dict = {"release": "1", "items": []}
    if section.startswith("items"):
        raw["items"] = [item]
    elif section == "other":
        raw["other"] = [item]
    elif section == "next":
        raw["next"] = {"release": "2", "items": [item]}
    else:  # the second of the later releases
        raw["next"] = {"release": "2", "items": []}
        raw["later"] = [{"release": "3", "items": []}, {"release": "4", "items": [item]}]
    return raw


def where(section: str) -> str:
    return {"items 0/0/0": "items", "later": "later[1]"}.get(section, section)


def test_the_cap_is_five():
    assert MAX_STAGES == 5


@pytest.mark.parametrize("section", SECTIONS)
def test_five_stages_load(section):
    plan = parse_plan(plan_with(section, item_of(section, 5)))
    if section.startswith("items"):
        assert plan.stages_of(plan.items[0]) == 5 and len(item_stages(plan, plan.items[0])) == 5
    elif section == "other":
        assert len(plan.other[0].stages) == 5
    else:
        assert len(plan.planned()[-1].items[0].stages) == 5


@pytest.mark.parametrize("section", SECTIONS)
def test_six_stages_are_refused_naming_the_item_and_its_count(section):
    with pytest.raises(TooManyStages) as e:
        parse_plan(plan_with(section, item_of(section, 6)))
    assert str(e.value) == f"an item holds at most 5 stages: {where(section)} 'Big' has 6"


def test_every_item_over_is_named_in_plan_order_with_its_count():
    long_name = "A very long item name that goes on well past forty characters"
    raw = {"release": "1",
           "items": [item_of("items", 7, "I1"), item_of("items", 5, "fine"), item_of("items 0/0/0", 6, long_name)],
           "other": [item_of("other", 9, "O1")],
           "next": {"release": "2", "items": [item_of("next", 6, "N1")]},
           "later": [{"release": "3", "items": [item_of("later", 8, "L1"), item_of("later", 2, "small")]}]}
    with pytest.raises(TooManyStages) as e:
        parse_plan(raw)
    assert str(e.value) == ("an item holds at most 5 stages: items 'I1' has 7, "
                            "items 'A very long item name that goes on well…' has 6, other 'O1' has 9, "
                            "next 'N1' has 6, later[0] 'L1' has 8")


def test_an_item_is_counted_by_the_stages_it_draws():
    """two items of one key (a typo) both draw the last one's `before` stages: the first, which builds,
    draws 4 + 3 of them, and is refused, though it lists none of its own"""
    raw = {"release": "1", "items": [{"name": "A", "key": "k", "build": 10},
                                     {"name": "B", "key": "k", "before": steps(4)}]}
    with pytest.raises(TooManyStages) as e:
        parse_plan(raw)
    assert str(e.value) == "an item holds at most 5 stages: items 'A' has 7"


def test_an_item_with_no_key_draws_no_stage_and_is_not_counted():
    """an item with no key is done: its `before` is not read, so it holds no stage"""
    plan = parse_plan({"release": "1", "items": [{"name": "Done", "key": None, "before": steps(9), "build": 60}]})
    assert plan.stages_of(plan.items[0]) == 0 and item_stages(plan, plan.items[0]) == []


def test_the_loader_refuses_the_plan_keeps_the_last_good_one_and_names_every_item_over(tmp_path):
    """the title's `status_plan.json not loaded (...)`, never cut at a title's line: it names each"""
    path = tmp_path / "status_plan.json"
    good = plan_with("other", item_of("other", 5))
    path.write_text(json.dumps(good))
    loader = PlanLoader(path)
    kept = loader.refresh()
    assert loader.loaded and loader.note == ""
    bad = {"release": "1", "items": [item_of("items", 6, f"Item number {i} of a long plan") for i in range(6)]}
    path.write_text(json.dumps(bad))
    os.utime(path, (NOW + 10, NOW + 10))
    assert loader.refresh() is kept
    named = ", ".join(f"items 'Item number {i} of a long plan' has 6" for i in range(6))
    assert loader.note == f"status_plan.json not loaded (TooManyStages: an item holds at most 5 stages: {named})"
    assert len(loader.note) > 160


def test_no_config_lifts_the_cap(tmp_path):
    """the cap is the engine's constant: a project's config has no key for it, and a plan's own key
    (`max_stages`, say) is ignored like any unknown one"""
    raw = plan_with("other", item_of("other", 6))
    raw["max_stages"] = 10
    with pytest.raises(TooManyStages):
        parse_plan(raw)
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["other"].append(item_of("other", 6, "Six stages"))
    p.plan(plan)
    engine = p.engine()
    plain = testing.render_engine(engine, 160)[0]
    assert ("status_plan.json not loaded (TooManyStages: an item holds at most 5 stages: other 'Six stages' "
            "has 6)") in " ".join(plain.split())
    assert plan_module.MAX_STAGES == 5


# ── rule 2: an item whose build, review and fix are all 0 ────────────────────────────────────────────
def rows_of(p: Project, width: int = 120) -> str:
    out = release_panel(p.engine().build_frame(width), width)  # (the panel, the release's finish)
    return testing.render_text(out[0], width)[0]


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def with_export(p: Project, **export) -> None:
    """midway's release, its second item (export) replaced: a key no agent of midway's has worked on"""
    midway(p)
    plan = release_plan()
    plan["items"][1] = {"name": "Export to CSV", "key": "export2", **export}
    p.plan(plan)


def test_an_item_of_no_build_review_or_fix_is_done_when_its_before_stages_are(tmp_path):
    p = Project(tmp_path)
    with_export(p, before=[["design", {"done": ts(NOW - 2 * HOUR)}, 30], ["your sign-off", {"done": ts(NOW - HOUR)}, 0]],
                build=0, review=0, fix=0)
    plain = rows_of(p)
    assert "Export to CSV" not in plain and "● 2 finished" in plain and "2/6 items ready" in plain


def test_an_item_of_no_build_review_or_fix_draws_its_before_stages_only(tmp_path):
    """its design done, its sign-off the owner's: two dots, waiting on you (it drew three more, empty)"""
    p = Project(tmp_path)
    with_export(p, before=[["design", {"done": ts(NOW - 2 * HOUR)}, 30], ["your sign-off", None, 0]])
    export = row(rows_of(p), "Export to CSV")
    assert "●─○" in export and "●─○─" not in export and "your sign-off" in export


def test_an_item_of_no_build_review_or_fix_and_no_before_stage_is_done(tmp_path):
    p = Project(tmp_path)
    with_export(p, build=0)
    plain = rows_of(p)
    assert "Export to CSV" not in plain and "● 2 finished" in plain


def test_a_by_hand_on_an_item_of_no_build_review_or_fix_marks_nothing(tmp_path):
    """build, review and fix are not drawn, so marking them done by hand neither adds a stage nor finishes it"""
    p = Project(tmp_path)
    with_export(p, before=[["design", "design:export2", 30]], by_hand={"build": ts(NOW - HOUR)})
    export = row(rows_of(p), "Export to CSV")
    assert "○" in export and "○─" not in export and "─○" not in export and "queued" in export


def test_an_item_with_any_build_review_or_fix_keeps_all_three(tmp_path):
    """today's behaviour exactly: a 0 among them is still drawn (and waited for)"""
    p = Project(tmp_path)
    with_export(p, before=[["design", {"done": ts(NOW - 2 * HOUR)}, 30]], build=60)
    export = row(rows_of(p), "Export to CSV")
    assert "●─○─○─○" in export and "queued" in export


def test_an_item_of_no_build_review_or_fix_begins_and_calibrates_by_its_before_stages_only():
    """an agent labelled build:<key> of such an item is no stage of it: it neither starts the item (an
    `owner_ok` item's go-ahead) nor counts towards the release's calibration"""
    raw = {"release": "1", "items": [{"name": n, "key": n, "before": [["design", f"design:{n}", 30]]} for n in "abc"]}
    plan = parse_plan(raw)
    labels = {f"build:{k}": {"id": k, "label": f"build:{k}", "status": "done", "run": "wf", "t0": NOW - 7200,
                             "t1": NOW, "phase": "", "result": {"findings": []}} for k in "abc"}
    assert not item_started(plan, "a", labels)
    cal = Calibration(0.7)
    calibrate(cal, plan.items, labels)
    assert cal.n == 0 and cal.factors["build"] == 1.0
    labels["design:a"] = {**labels["build:a"], "id": "d", "label": "design:a"}
    assert item_started(plan, "a", labels)
    p = item_progress(plan, plan.items[0], labels, now=NOW, cal=cal, default_fix_share=0.7)
    assert p.current == "done" and len(p.marks) == 1

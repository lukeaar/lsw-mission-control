from __future__ import annotations

import json
import os

import pytest

from lsw_mission_control.notes import NotesLoader, read_notes
from lsw_mission_control.plan import EMPTY_PLAN, PlanLoader, parse_plan, parse_stage


@pytest.mark.parametrize("stage, want", [
    (["build", "build:x", 30], ("build", "build:x", 30.0)),
    (["both", ["a:x", "b:x"], "15"], ("both", ["a:x", "b:x"], 15.0)),
    (["yours", None, 0], ("yours", None, 0.0)),
    (["job", {"progress": "/p.jsonl", "total": 12}, 60], ("job", {"progress": "/p.jsonl", "total": 12}, 60.0)),
    (["job", {"progress": "/p.jsonl", "total": 3, "eta_json": "/e.json"}, 1],
     ("job", {"progress": "/p.jsonl", "total": 3, "eta_json": "/e.json"}, 1.0)),
])
def test_parse_stage_accepts(stage, want):
    assert parse_stage(stage, "Item") == want


@pytest.mark.parametrize("stage", [
    ["bad", 3, 10], ["bad", ["a", 1], 10], ["bad", {"progress": "/p", "total": 0}, 1],
    ["bad", {"total": 3}, 1], ["bad", {"progress": "/p", "total": 3, "eta_json": 5}, 1],
])
def test_parse_stage_rejects_with_its_text(stage):
    with pytest.raises(ValueError) as e:
        parse_stage(stage, "Item")
    assert str(e.value) == ("stage 'bad' of 'Item': label must be text, a list of text, null, or {progress, total}")


def test_parse_plan_shapes():
    plan = parse_plan({
        "release": 2, "note": "ignored", "items": [
            {"name": "A", "key": None},
            {"name": "B", "key": 7, "build": "10", "review": 5, "flags": ["after:a", 3],
             "before": [["design", "design:7", 20]]},
        ],
        "other": [{"name": "O", "stages": [["s", None, 1]], "paused": 1, "after": "A", "after_server": "yes", "note": "x"}],
        "next": {"release": "3", "about": "later", "items": [
            {"key": "n1", "name": "N", "group": "g", "before": [["your pick", None, 0]], "build": 30, "fix": 0},
            {"key": None, "name": "M"}]},
    })
    assert plan.release == "2"
    a, b = plan.items
    assert a.key is None and b.key == "7" and b.build == 10 and b.flags == ("after:a", "3")
    assert plan.before("7") == [("design", "design:7", 20.0)] and plan.before(None) == []
    o = plan.other[0]
    assert o.paused is True and o.after == "A" and o.after_server is True
    n1, m = plan.next.items
    # a next item gets build:/review:/fix: stages only for kinds with minutes > 0
    assert n1.stages == (("your pick", None, 0.0), ("build", "build:n1", 30.0))
    assert m.stages == () and plan.next.about == "later"


def test_after_live_is_another_name_for_after_server():
    plan = parse_plan({"release": "1", "items": [], "other": [
        {"name": "A", "after_live": True, "stages": [["job", None, 0]]},
        {"name": "B", "stages": [["s", None, 1]]}]})
    assert [o.after_server for o in plan.other] == [True, False]


BASE = {"release": "1", "items": [{"name": "I", "key": "i", "build": 10}],
        "other": [{"name": "O", "stages": [["s", "s:o", 5]]},
                  {"name": "J", "after_server": True, "stages": [["job", None, 0], ["check", "check:j", 5]]}],
        "next": {"release": "2", "items": [{"key": "n", "name": "N", "build": 5}]}}


def with_change(path: str, value):
    """BASE with the value at a dotted path ('other.0.stages.0.2') replaced."""
    d = json.loads(json.dumps(BASE))
    *head, last = path.split(".")
    node = d
    for part in head:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return d


@pytest.mark.parametrize("path, value, message", [
    # shapes that used to load and then fail every frame
    ("other.0.stages", [], None),  # an ordinary item may have no stages yet
    ("other.1.stages", [], "'J' waits on a live job: its first stage is the job's, so it needs one"),
    ("other.0.stages.0.2", "nan", "must be from 0 to"),
    ("other.0.stages.0.2", "inf", "must be from 0 to"),
    ("other.0.stages.0.2", 1e308, "must be from 0 to"),
    ("other.0.stages.0.2", -1, "must be from 0 to"),
    ("other.0.stages.0.2", True, "must be a number"),
    ("other.0.stages.0.2", [1], "must be a number"),
    ("other.0.after", ["O"], "after of 'O' must be the name of another other item"),
    ("other.0.after", {"a": 1}, "after of 'O' must be the name of another other item"),
    ("other.0.stages.0", ["s", "s:o"], "must be [name, label, minutes]"),
    ("other.0.stages.0", "abc", "must be [name, label, minutes]"),
    ("other.0.stages.0.1", {"progress": "/p", "total": 10**9}, "total must be at most"),
    ("other.0.stages.0.1", {"progress": "/p", "total": 3, "eta_key": 5}, "eta_key must be text"),
    ("other.0.stages", "s", "stages of 'O' must be a list"),
    ("other", {"a": 1}, "other must be a list"),
    ("items.0.build", 1e308, "build of 'I' must be from 0 to"),
    ("items.0.review", -5, "review of 'I' must be from 0 to"),
    ("items.0.fix", "inf", "fix of 'I' must be a number"),
    ("items.0.flags", "after:x", "flags of 'I' must be a list"),
    ("items.0", [], "each of items must be an object"),
    ("next.items.0.key", ["n"], "key of 'N' must be text"),
    ("next.items.0.build", float("nan"), "build of 'N' must be a number"),
    ("next", [1], "next must be an object"),
])
def test_shapes_that_would_fail_every_frame_are_refused(path, value, message):
    d = with_change(path, value)
    if message is None:
        parse_plan(d)
        return
    with pytest.raises(ValueError) as e:
        parse_plan(d)
    assert message in str(e.value)


def test_numbers_as_text_still_load():
    plan = parse_plan(with_change("other.0.stages.0.2", "15"))
    assert plan.other[0].stages[0][2] == 15.0
    assert parse_plan(with_change("next.items.0.key", 7)).next.items[0].stages[0][1] == "build:7"


def test_missing_keys_raise():
    with pytest.raises(KeyError):
        parse_plan({"items": []})
    with pytest.raises(KeyError):
        parse_plan({"release": "1"})


class _Owns:
    name = "owner"

    def parse_plan(self, raw):
        fb = raw["went_live"]
        return (str(fb[0]), int(fb[1]))


def test_a_plugin_owns_plan_keys():
    plan = parse_plan({"release": "1", "items": [], "went_live": ["2026-01-01T00:00:00Z", 5]}, [_Owns()])
    assert plan.plugin_data == {"owner": ("2026-01-01T00:00:00Z", 5)}
    with pytest.raises(KeyError, match="went_live"):
        parse_plan({"release": "1", "items": []}, [_Owns()])


def test_loader_keeps_the_last_good_plan(tmp_path):
    p = tmp_path / "status_plan.json"
    loader = PlanLoader(p, [_Owns()])
    assert loader.refresh() is EMPTY_PLAN and loader.note == "status_plan.json is missing"
    p.write_text(json.dumps({"release": "1", "items": [], "went_live": ["x", 1]}))
    os.utime(p, (100, 100))
    assert loader.refresh().release == "1" and loader.note == ""
    good = loader.plan
    p.write_text(json.dumps({"release": "2", "items": []}))  # the plugin's key is missing
    os.utime(p, (200, 200))
    assert loader.refresh() is good
    assert loader.note == "status_plan.json not loaded (KeyError: 'went_live')"
    p.write_text('{"release": ')
    os.utime(p, (300, 300))
    loader.refresh()
    assert loader.note.startswith("status_plan.json not loaded (JSONDecodeError: ") and len(loader.note) <= 160
    # the mtime gate: an unchanged file is not read again
    p.write_text(json.dumps({"release": "3", "items": [], "went_live": ["x", 1]}))
    os.utime(p, (300, 300))
    assert loader.refresh() is good
    os.utime(p, (400, 400))
    assert loader.refresh().release == "3" and loader.note == ""


def test_a_plan_moved_away_and_back_is_read_again(tmp_path):
    p = tmp_path / "status_plan.json"
    p.write_text(json.dumps({"release": "1", "items": []}))
    os.utime(p, (100, 100))
    loader = PlanLoader(p)
    assert loader.refresh().release == "1"
    p.rename(tmp_path / "moved.json")
    loader.refresh()
    assert loader.note == "status_plan.json is missing"
    (tmp_path / "moved.json").rename(p)  # back, with its old mtime
    assert loader.refresh().release == "1" and loader.note == ""


def test_notes(tmp_path):
    p = tmp_path / "n.json"
    assert read_notes(p).mtime is None
    p.write_text("[1, 2]")  # not an object: not notes
    assert read_notes(p).mtime is None
    p.write_text(json.dumps({"waiting_on_owner": "one thing", "in_progress_elsewhere": [1, "b"]}))
    n = read_notes(p)
    assert n.waiting_on_owner == ("one thing",) and n.in_progress_elsewhere == ("1", "b") and n.mtime
    p.write_text("not json")
    assert read_notes(p).mtime is None


def test_notes_loader_keeps_the_last_good_notes(tmp_path):
    p = tmp_path / "status_notes.json"
    loader = NotesLoader(p)
    assert loader.refresh().mtime is None and loader.note == ""  # no file: no notes, no complaint
    p.write_text(json.dumps({"waiting_on_owner": ["decide"]}))
    os.utime(p, (100, 100))
    good = loader.refresh()
    assert good.waiting_on_owner == ("decide",) and loader.note == ""
    p.write_text('{"waiting_on_owner": ["decide", "sign"],}')  # a trailing comma
    os.utime(p, (200, 200))
    assert loader.refresh() is good
    assert loader.note.startswith("status_notes.json not loaded (JSONDecodeError: ") and len(loader.note) <= 160
    p.write_text("[]")
    os.utime(p, (300, 300))
    assert loader.refresh() is good and loader.note == "status_notes.json not loaded (ValueError: it must hold a JSON object)"
    p.write_text(json.dumps({"waiting_on_owner": []}))
    os.utime(p, (400, 400))
    assert loader.refresh().waiting_on_owner == () and loader.note == ""

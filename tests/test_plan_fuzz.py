"""A plan that loads must draw: every value of the documented example plan is replaced, one at a
time, by an odd one (a wrong type, NaN, a huge number, markup, an empty list, ...). Each result is
either refused by the loader (the last good plan stays, the title says why) or drawn with no error
anywhere. LSW_MC_FUZZ=full tries every value at every place; the default is a fixed sample."""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from lsw_mission_control import testing
from lsw_mission_control.plan import parse_plan

from scenarios import Project, midway

DOCS = Path(__file__).resolve().parents[1] / "docs"
ODD = [None, "", " ", "x", "[/x] y", "15", 0, -1, 1.5, 1e308, "nan", "inf", True, [], [1], ["a", 1], {}, {"a": 1},
       [["s", None, 1]], [["s", "a:b", 1e308]]]


def example_plan() -> dict:
    md = (DOCS / "plan-schema.md").read_text()
    block = re.search(r"## `status_plan.json`.*?```json\n(.*?)```", md, re.S).group(1)
    return json.loads(block)


def places(node, path=()):
    yield path
    if isinstance(node, dict):
        for k, v in node.items():
            yield from places(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from places(v, path + (i,))


def replaced(plan: dict, path: tuple, value):
    d = copy.deepcopy(plan)
    node = d
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = copy.deepcopy(value)
    return d


def test_every_plan_that_loads_draws(tmp_path):
    base = example_plan()
    cases = [(path, v) for path in places(base) if path for v in ODD]
    if os.environ.get("LSW_MC_FUZZ") != "full":
        cases = cases[::7]  # a fixed sample of every place and every kind of value
    p = Project(tmp_path)
    midway(p)
    engine = p.engine()
    loaded = refused = 0
    failures = []
    for path, value in cases:
        try:
            plan = parse_plan(replaced(base, path, value), engine.plugins)
        except Exception:  # noqa: BLE001 — refused: the loader keeps the last good plan
            refused += 1
            continue
        loaded += 1
        engine.plans.plan = plan
        engine.plans._mtime = engine.plans.path.stat().st_mtime  # refresh() keeps this plan
        for width in (80, 150):
            testing.render_engine(engine, width)
            errors = engine.errors_for_once()
            if errors:
                failures.append(f"{'.'.join(map(str, path))} = {value!r} at {width}: {errors.strip().splitlines()[-1]}")
    assert loaded and refused
    assert not failures, "\n".join(failures[:20])

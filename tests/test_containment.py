"""One failure stays in its own place, and stays loud: a panel that fails to build or to DRAW, a
plugin that returns the wrong thing, outside text that looks like markup. The reload gate
(--check-net --self-check) and --once fail on every one of them."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from rich.table import Table

import lsw_mission_control.engine as eng
from lsw_mission_control import cli, testing
from lsw_mission_control.app import Scroll, run_once
from lsw_mission_control.config import load_config
from lsw_mission_control.engine import Engine
from lsw_mission_control.plugin import Flags, load_plugins

from scenarios import STUB_PLUGIN, Project, midway, release_plan

SRC = Path(__file__).resolve().parents[1] / "src"


def lswmc(*args):
    return subprocess.run([sys.executable, "-m", "lsw_mission_control", *args], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(SRC)}, timeout=60)


def markup_table(*_a, **_k):
    t = Table.grid()
    t.add_column()
    t.add_row("see [/docs] first")  # builds fine; fails only when drawn
    return t


def self_check(p: Project) -> int:
    cfg = load_config(p.dot / "mission-control.toml")
    plugins, errors = load_plugins(cfg, Flags())
    return cli.self_check(cfg, Flags(), plugins, errors)


def test_a_drawing_error_is_contained_and_fails_once(tmp_path, monkeypatch, capsys):
    p = Project(tmp_path)
    midway(p)
    monkeypatch.setattr(eng, "notes_panel", markup_table)
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert "MarkupError" in plain and "Release 1.4.0" in plain and "Model usage" in plain
    assert "drawing the notes panel" in plain  # not the guard's own file
    assert "notes failed: Traceback" in e.errors_for_once() and not e.last_error
    assert run_once(p.engine(), Console(file=io.StringIO(), width=150), wait_s=0) == 1
    assert "MarkupError" in capsys.readouterr().err


def test_the_gate_refuses_a_drawing_error_in_a_panel(tmp_path, monkeypatch, capsys):
    p = Project(tmp_path)
    midway(p)
    assert self_check(p) == 0
    monkeypatch.setattr(eng, "notes_panel", markup_table)
    assert self_check(p) == 1
    assert "notes failed" in capsys.readouterr().err


def test_the_gate_refuses_a_drawing_error_outside_every_panel(tmp_path, monkeypatch, capsys):
    p = Project(tmp_path)
    midway(p)
    monkeypatch.setattr(eng, "title_line", markup_table)  # the title is not a contained panel
    assert self_check(p) == 1
    assert "MarkupError" in capsys.readouterr().err


def test_the_gate_refuses_a_frame_that_cannot_be_built(tmp_path, monkeypatch, capsys):
    p = Project(tmp_path)
    midway(p)

    def boom(self, width):
        raise RuntimeError("no frame")

    monkeypatch.setattr(Engine, "build_frame", boom)
    assert self_check(p) == 1
    assert "RuntimeError: no frame" in capsys.readouterr().err


def test_scroll_records_what_it_caught():
    class Boom:
        def __rich_console__(self, console, options):
            raise ValueError("bad body")
    s = Scroll()
    s.body = Boom()
    Console(file=io.StringIO(), width=80, height=10).print(s)
    assert "ValueError: bad body" in s.error
    s.body = "fine"
    Console(file=io.StringIO(), width=80, height=10).print(s)
    assert s.error == ""


def stub_config(p: Project, fail: str) -> None:
    midway(p)
    p.write_config(f"""
        [github]
        repo = "example/demo"

        [plan]
        after_server = "stub"

        [[plugins]]
        name = "stub"
        file = "{STUB_PLUGIN}"
        class = "StubPlugin"

        [plugins.options]
        host = "stubhost"
        fail = "{fail}"
        """)


@pytest.mark.parametrize("fail, shown", [
    ("side_card", "RuntimeError: stub side_card broke"),
    ("markup", "MarkupError: closing tag '[/docs]'"),
    ("dict-card", "TypeError: side_card() must return None or"),
    ("no-grid", "TypeError: side_card() must return None or"),
])
def test_a_plugin_card_failure_stays_in_its_card_and_fails_the_gate(tmp_path, fail, shown):
    p = Project(tmp_path)
    stub_config(p, fail)
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert shown in plain
    assert "Repository" in plain and "Release 1.4.0" in plain and not e.last_error
    assert e.errors_for_once()
    r = lswmc("--project", str(p.root), "--check-net", "--self-check")
    assert r.returncode == 1 and "28/28 network cases correct" in r.stdout, r.stderr


def test_a_live_job_that_is_not_a_number_is_a_plugin_error(tmp_path):
    p = Project(tmp_path)
    stub_config(p, "nan-job")
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert "plugin error" in plain and "frac must be a finite number" in plain
    assert "Other work in progress" in plain and not e.last_error


def test_a_plugin_panel_that_is_not_renderable(tmp_path):
    p = Project(tmp_path)
    midway(p)
    (tmp_path / "paneller.py").write_text(
        "from lsw_mission_control.plugin import Plugin\n"
        "class P(Plugin):\n"
        "    def panel(self, width, frame):\n"
        "        return 42\n")
    p.write_config(f"""
        [layout]
        panels = ["notes", "plugin:paneller", "usage"]

        [[plugins]]
        name = "paneller"
        file = "{tmp_path / 'paneller.py'}"
        class = "P"
        """)
    e = p.engine()
    plain, _ = testing.render_engine(e, 120)
    assert "panel() must return None or a rich renderable, not a int" in plain and "Model usage" in plain


def test_outside_text_is_never_markup(tmp_path):
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][2]["name"] = "Fix [/api] routes"
    plan["other"][1]["name"] = "Docs [b]refresh[/b] :smile:"
    p.plan(plan)
    p.agent("docs:draft", start_ago=10 * 60, quiet_s=5, run="wf_run-z", action="Bash · [n for n in dir(x)] [/x] :smile:")
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert not e.errors_for_once(), e.errors_for_once()
    assert "Fix [/api] routes" in plain and "Docs [b]refresh[/b] :smile:" in plain
    assert "[n for n in dir(x)] [/x] :smile:" in plain

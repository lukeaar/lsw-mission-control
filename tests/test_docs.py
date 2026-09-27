"""The docs' examples must work: the worked plugin loads and draws, the example config loads."""

from __future__ import annotations

import re
import time
from pathlib import Path

from lsw_mission_control import testing
from lsw_mission_control.config import load_config

from scenarios import Project, midway

DOCS = Path(__file__).resolve().parents[1] / "docs"


def blocks(md: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", md, re.S)


def test_the_worked_plugin(tmp_path):
    code = [b for b in blocks((DOCS / "plugins.md").read_text(), "python") if "class QueuePlugin" in b][0]
    p = Project(tmp_path)
    midway(p)
    (p.dot / "mc_queue.py").write_text(code)
    p.write_config("""
        [plan]
        after_server = "queue"

        [[plugins]]
        name = "queue"
        file = "mc_queue.py"
        class = "QueuePlugin"

        [plugins.options]
        host = "build-box"
        """)
    e = p.engine()
    assert not e.load_errors and e.own_labels == frozenset({"gh", "ssh → build-box"})
    plain, _ = testing.render_engine(e, 150)
    assert "connecting to build-box…" in plain and "Queue · build-box" in plain
    e.plugins[0].ctx.state.update(done=300.0, left=100.0, rate=50.0, at=time.time(), err=None)
    plain, _ = testing.render_engine(e, 150)
    assert "75%" in plain and "in 2h00" in plain and not e.errors_for_once()


def test_the_example_config(tmp_path):
    toml = [b for b in blocks((DOCS / "config.md").read_text(), "toml") if "schema = 1" in b][0]
    d = tmp_path / "acme" / ".claude"
    d.mkdir(parents=True)
    (d / "mission-control.toml").write_text(toml)
    cfg = load_config(d / "mission-control.toml")
    assert cfg.github.repo == "example/acme" and cfg.release.final_merge.minutes == 165
    assert cfg.plugins[0].name == "queue" and cfg.after_server == "queue"

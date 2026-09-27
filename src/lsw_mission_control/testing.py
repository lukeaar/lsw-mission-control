"""Helpers for tests and parity runs: a recording console and a frozen clock."""

from __future__ import annotations

import io
import os
import time

from rich.console import Console

from lsw_mission_control import util


def use_utc() -> None:
    os.environ["TZ"] = "UTC"
    time.tzset()


def freeze(at: float | None) -> None:
    """Freeze the dashboard's clock (None: the real time again)."""
    util.set_clock(None if at is None else (lambda: at))


def record_console(width: int, height: int = 60, style: str = "#fdfbf9 on #252226") -> Console:
    return Console(width=width, height=height, record=True, force_terminal=True, color_system="truecolor",
                   legacy_windows=False, file=io.StringIO(), style=style, _environ={})


def render_text(renderable, width: int, height: int = 60, style: str = "#fdfbf9 on #252226") -> tuple[str, str]:
    """(plain text, text with ANSI styles) of one renderable at a width."""
    console = record_console(width, height, style)
    console.print(renderable)
    return console.export_text(styles=False, clear=False), console.export_text(styles=True)


def render_engine(engine, width: int, height: int = 60) -> tuple[str, str]:
    console = record_console(width, height, f"{engine.cfg.theme.text} on {engine.cfg.theme.bg}")
    console.print(engine.render(console))
    return console.export_text(styles=False, clear=False), console.export_text(styles=True)

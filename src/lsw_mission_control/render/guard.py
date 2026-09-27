"""Containment while drawing: a panel whose drawing fails (a markup error in a cell, a plugin's
renderable that raises) is replaced by an error panel in its own place, and the error is
reported, so the rest of the dashboard draws and --once and the reload gate still see it.

Guarded(x) draws exactly what x draws when nothing fails: x's own lines, one by one.
"""

from __future__ import annotations

from typing import Callable

from rich.console import RenderableType
from rich.measure import Measurement
from rich.segment import Segment


class Guarded:
    def __init__(self, inner: RenderableType, on_error: Callable[[BaseException], RenderableType]) -> None:
        self.inner = inner
        self.on_error = on_error  # called inside the `except`: it records the traceback, returns the stand-in

    def __rich_console__(self, console, options):
        try:
            lines = console.render_lines(self.inner, options, pad=False)
        except Exception as e:  # noqa: BLE001 — one panel's fault must not blank the others
            lines = console.render_lines(self.on_error(e), options, pad=False)
        for line in lines:
            yield from line
            yield Segment.line()

    def __rich_measure__(self, console, options) -> Measurement:
        try:
            return Measurement.get(console, options, self.inner)
        except Exception:  # noqa: BLE001 — drawing it will fail too, and report it then
            return Measurement(1, options.max_width)

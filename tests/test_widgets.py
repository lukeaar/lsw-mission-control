"""The work table's rows, drawn: what each bar fills with, and in which colour."""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.table import Table

from lsw_mission_control.progress import Prog
from lsw_mission_control.render.widgets import work_row
from lsw_mission_control.theme import C, contrast


def bar_runs(t: Table) -> list[tuple[int, str]]:
    """The row's runs of bar glyphs: (how many, their colour), left to right."""
    console = Console(file=io.StringIO(), width=120, force_terminal=True, color_system="truecolor", legacy_windows=False)
    return [(len(seg.text), seg.style.color.get_truecolor().hex) for seg in console.render(t)
            if seg.text and set(seg.text) == {"━"} and seg.style is not None and seg.style.color is not None]


def one_row(name: str, p: Prog, bar_w: int = 20) -> Table:
    t = Table(box=None, show_header=False, pad_edge=False)
    for _ in range(5):
        t.add_column(no_wrap=True)
    work_row(t, name, p, bar_w)
    return t


@pytest.mark.parametrize("fraction, done", [(0.46, 9), (0.71, 14), (0.0, 0), (1.0, 20)])
def test_a_paused_rows_bar_shows_the_work_done(fraction, done):
    """A held row's bar fills with its work done in a muted grey. Its fill was the panel's own colour,
    the empty part's, so a paused row at 71% looked empty while its percentage said 71%."""
    p = Prog(None, "paused", fraction=fraction)
    p.paused = True
    runs = bar_runs(one_row("Split maps", p))
    assert runs == [(n, colour) for n, colour in ((done, C.MUTED), (20 - done, C.SURFACE)) if n]
    assert contrast(C.MUTED, C.SURFACE) >= 3  # the fill reads against the empty part


def test_a_running_rows_bar_keeps_its_time_colour():
    p = Prog(30 * 60, "build", fraction=0.5)
    assert bar_runs(one_row("Export", p)) == [(10, C.ACCENT), (10, C.SURFACE)]

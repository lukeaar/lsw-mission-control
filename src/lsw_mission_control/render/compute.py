"""The heavy jobs, at the foot of Other work in progress: where each runs, what it is, how long it
has run, its CPU (in cores) and memory, and its progress with a finish from the rate it shows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

from lsw_mission_control.render.widgets import bar, eta_colour
from lsw_mission_control.theme import C
from lsw_mission_control.util import clock, human

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame
    from lsw_mission_control.sources.compute import ComputeJob

STALE_S = 5 * 60  # a remote's rows read longer ago than this say how long


def _gb(n: float) -> str:
    return f"{n / 2**30:.1f} GB" if n >= 2**30 / 10 else f"{n / 2**20:.0f} MB"


def _count(x: float) -> str:
    return f"{x:,.0f}" if x == int(x) else f"{x:,.1f}"


def compute_section(f: Frame, width: int) -> list:
    """[] with nothing heavy running; otherwise a blank line, a heading with each machine's cores,
    and one row per job."""
    rows: list[ComputeJob] = f.compute
    if not rows and not f.compute_errors:
        return []
    here = f.cfg.compute.local_name
    t_now = f.now

    def where(j: ComputeJob) -> str:
        return here if j.machine == "local" else j.machine

    totals: dict[str, float] = {}
    for j in rows:
        if j.cores is not None and not j.ended:
            totals[where(j)] = totals.get(where(j), 0.0) + j.cores
    head = Text("compute jobs", style=C.FAINT)
    if totals:
        head.append("   " + " · ".join(f"{m} {c:.1f} cores" for m, c in totals.items()), style=C.FAINT)
    out: list = [Text(""), head]
    # By the panel's inner width: bars and finish times from 94 columns, cores and memory in words from 120.
    wide, words = width >= 90, width >= 116
    bar_w = 12 if width >= 130 else 8
    g = Table.grid(padding=(0, 2), expand=True)
    g.add_column(no_wrap=True, style=C.FAINT)
    g.add_column(no_wrap=True, overflow="ellipsis", ratio=1, min_width=10)
    g.add_column(no_wrap=True, justify="right", style=C.MUTED)
    g.add_column(no_wrap=True, justify="right", style=C.MUTED)
    g.add_column(no_wrap=True)
    g.add_column(no_wrap=True, justify="right")
    for j in rows:
        name = Text(j.label, style=C.MUTED if j.ended or j.waiting else C.TEXT)
        detail = j.detail
        if j.as_of is not None and t_now - j.as_of > STALE_S:
            detail = " · ".join(x for x in (detail, f"read {human(t_now - j.as_of)} ago") if x)
        if detail:
            name.append(f" · {detail}", style=C.FAINT)
        ran = Text(human(t_now - j.started) if j.started and not j.ended else "")
        if j.ended:
            load = Text(f"ended {clock(j.ended)}", style=C.FAINT)
        elif j.cores is None and j.mem is None:
            load = Text("—", style=C.FAINT)
        elif words:
            load = Text(" · ".join(x for x in (f"{j.cores:.1f} cores" if j.cores is not None else "",
                                              _gb(j.mem) if j.mem is not None else "") if x))
        else:
            load = Text(" ".join(x for x in (f"{j.cores:.1f}c" if j.cores is not None else "",
                                             _gb(j.mem).replace(" ", "")[:-1] if j.mem is not None else "") if x))
        prog, when = Text(""), Text("")
        if j.done is not None:
            frac = j.done / j.total if j.total else None
            eta = None if j.ended else j.eta
            colour = eta_colour(eta) if eta is not None else C.MUTED
            n = f"{_count(j.done)}/{_count(j.total)}" if j.total else _count(j.done)
            prog = (bar(frac, bar_w, colour) + Text(f" {n}", style=C.MUTED)) if wide and frac is not None \
                else Text(n, style=C.MUTED)
            if eta is not None:
                when = Text(f"~{human(eta)}" + (f" · {clock(t_now + eta)}" if wide else ""), style=colour)
            elif not j.ended:
                when = Text("—", style=C.FAINT)
        g.add_row(Text(where(j)), name, ran, load, prog, when)
    out.append(g)
    for e in f.compute_errors:
        out.append(Text(f"not read: {e}", style=C.AMBER))
    return out

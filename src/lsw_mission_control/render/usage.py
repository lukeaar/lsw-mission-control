"""Model usage: the plan limits (from the status line or the probe) and the tokens counted."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text

from lsw_mission_control.render.logo import LOGO_MIN_COLS, LOGO_MIN_ROWS, LOGO_PANEL_PAD, LogoAnimator, logo_panel
from lsw_mission_control.render.widgets import LABEL_W, METER_W, meter, panel
from lsw_mission_control.theme import C
from lsw_mission_control.util import clock, count, human, now, num

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame

TABLES_GAP = 4  # the least room between the token table and the by-model table beside it


def share(window: dict) -> float | None:
    """A plan window's used share in percent, or None when it carries none: a plan without that
    window may send it with `used_percentage` null or missing, which num() read as 0%."""
    x = window.get("used_percentage")
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class Level:
    """A plan limit's row: its meter as long as the row it is drawn in leaves it, then its share and
    reset (`tail`). It measures as drawn with a METER_W meter, so Model usage keeps the width
    usage_row finds for it and the meter takes only room to spare; narrower, the row is cut short
    with "…", as a Text row is. Each meter leaves room for `tail_w`, the widest tail of the rows, so
    the meters stay one length and the shares line up."""

    def __init__(self, fraction: float, colour: str, tail: Text):
        self.fraction, self.colour, self.tail, self.tail_w = fraction, colour, tail, tail.cell_len

    def line(self, meter_w: int) -> Text:
        t = meter(self.fraction, meter_w, self.colour) + self.tail
        t.no_wrap, t.overflow = True, "ellipsis"
        return t

    def __rich_measure__(self, console: Console, options) -> Measurement:
        return Measurement.get(console, options, self.line(METER_W))

    def __rich_console__(self, console: Console, options):
        yield self.line(max(METER_W, options.max_width - self.tail_w))


class Tables:
    """The token table, and the by-model table beside it when the width it is drawn in holds both
    (TABLES_GAP apart at least, by model at the right end, under the meters' tails), else under it
    after a blank line. It measures as drawn stacked, the narrowest it is drawn whole at, so Model
    usage keeps the width usage_row finds for it; how many rows it draws depends on the width."""

    def __init__(self, tokens: Table, by_model: Table | None):
        self.tokens, self.by_model = tokens, by_model

    def stacked(self) -> Group:
        return Group(self.tokens, Text(""), self.by_model) if self.by_model else Group(self.tokens)

    def __rich_measure__(self, console: Console, options) -> Measurement:
        return Measurement.get(console, options, self.stacked())

    def __rich_console__(self, console: Console, options):
        if self.by_model:
            right = Measurement.get(console, options, self.by_model).maximum
            if Measurement.get(console, options, self.tokens).maximum + TABLES_GAP + right <= options.max_width:
                g = Table.grid(expand=True)
                g.add_column(ratio=1)
                g.add_column(width=right)
                g.add_row(self.tokens, self.by_model)
                yield g
                return
        yield self.stacked()


def usage_panel(f: Frame, width: int):
    """Plan limits (5-hour, weekly) with their resets, and tokens used, under Agents at work."""
    g = Table.grid(padding=(0, 1), expand=True)
    g.add_column(no_wrap=True, style=C.MUTED, width=LABEL_W)
    g.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
    try:
        usage = json.loads((f.cfg.usage_dir / "usage.json").read_text())
    except (OSError, ValueError):
        usage = None
    if not isinstance(usage, dict):
        usage = None
    t_now = now()
    stale_after = (f.cfg.usage.probe_every_min + 5) * 60
    if usage:
        at = num(usage.get("at"))
        stale = t_now - at > stale_after
        limits = usage.get("rate_limits") if isinstance(usage.get("rate_limits"), dict) else {}
        levels = []
        for key, label in (("five_hour", "5-hour"), ("seven_day", "weekly")):
            w = limits.get(key)
            pct = share(w) if isinstance(w, dict) else None
            if pct is None:
                continue  # no window, or one with no share (a plan without it may send it empty): no row, not 0%
            resets = num(w.get("resets_at"))
            if resets and resets <= t_now:
                # The window reset after the last plan data: its use since then is unknown.
                levels.append(Level(0.0, C.FAINT, Text("    ?%", style=C.FAINT)
                                    + Text(f"  reset {clock(resets)} · no data since", style=C.FAINT)))
                g.add_row(label, levels[-1])
                continue
            col = C.GREEN if pct < 50 else C.AMBER if pct < 80 else C.RED
            # Stale: a faint meter and a muted share; how old the data is, the subtitle says in amber
            # (a note on each row widened Model usage, and pushed the logo out of a 99-column window).
            tail = Text()  # no style of its own: drawn after the meter, it would span the reset too
            tail.append(f"  {pct:3.0f}%", style=C.MUTED if stale else f"bold {col}")
            if resets:
                tail.append(f"   resets {clock(resets)} · in {human(resets - t_now)}", style=C.MUTED)
            levels.append(Level(pct / 100, C.FAINT if stale else col, tail))
            g.add_row(label, levels[-1])
        widest = max((lv.tail_w for lv in levels), default=0)
        for lv in levels:
            lv.tail_w = widest
        status = str(usage.get("status") or "")
        if status and status != "allowed" or usage.get("overage"):
            note = Text()
            if status == "allowed_warning":
                note.append("warning: close to a limit", style=C.AMBER)
            elif status.startswith("rejected"):
                note.append("limit reached", style=f"bold {C.RED}")
            elif status and status != "allowed":
                note.append(status, style=C.AMBER)
            if usage.get("overage"):
                note.append(("  ·  " if note.plain else "") + "using usage credits", style=C.AMBER)
            g.add_row("status", note)
    else:
        g.add_row("plan", Text("limits: no data yet", style=C.FAINT))
    tokens = f.store.get("tokens")
    by_model = f.store.get("tokens_by_model")
    t = Table(box=None, show_header=True, header_style=f"bold {C.FAINT}", pad_edge=False, padding=(0, 1))
    t.add_column("tokens", style=C.MUTED, no_wrap=True, width=LABEL_W)
    for head in ("output", "input", "cache writes", "cache reads"):
        t.add_column(head, justify="right", no_wrap=True, style=C.TEXT if head == "output" else C.MUTED)
    if tokens:
        for name, label in (("5h", "last 5 h"), ("today", "today"), ("7d", "7 days")):
            inp, out, cw, cr = tokens[name]
            t.add_row(label, count(out), count(inp), count(cw), count(cr))
    else:
        t.add_row("", Text("counting the session logs…", style=C.FAINT), "", "", "")
    m = None
    if by_model:
        m = Table(box=None, show_header=True, header_style=f"bold {C.FAINT}", pad_edge=False, padding=(0, 1))
        m.add_column("by model", style=C.MUTED, no_wrap=True, width=LABEL_W)
        for head in ("last 5 h", "today", "7 days", "share · 7 days"):
            m.add_column(head, justify="right", no_wrap=True, style=C.MUTED)
        week = sum(v["7d"] for v in by_model.values()) or 1
        for fam, v in sorted(by_model.items(), key=lambda kv: -kv[1]["7d"]):
            if not (v["5h"] or v["today"] or v["7d"]):
                continue
            m.add_row(fam, count(v["5h"]), count(v["today"]), count(v["7d"]), f"{100 * v['7d'] / week:.0f}%")
    # A grid with no row (a plan-data record with no window this panel reads, and no status) draws
    # nothing, but rich measures its flexible column as wide as it is offered: usage_row would read
    # Model usage as the whole row wide and leave the logo out. Left out, it draws the same.
    parts = ([g] if g.row_count else []) + [Text(""), Tables(t, m)]
    src = {"probe": "probe", None: "terminal"}.get(usage.get("source") if usage else None, "terminal")
    sub = "" if not usage else (f"plan data as of {clock(at)} · {human(t_now - at)} ago · {src}" if at
                                else f"plan data of unknown age · {src}")
    return panel(Group(*parts), "Model usage", sub, C.AMBER if usage and stale else None)


def usage_row(console: Console, f: Frame, width: int, logo: LogoAnimator | None):
    """Model usage with the logo panel to its right, both the same height, when there is room.

    Model usage keeps its own width, MEASURED from what it draws now (rich's Measurement of the
    panel: its widest row, border and padding included; its subtitle too; usage_panel leaves out a
    grid with no row, which rich would measure the whole width), so the logo never clips it: the
    width it has with METER_W meters and the by-model table under the token table. Given more, its
    meters take the room (Level) and the by-model table goes beside the token table once both fit
    (Tables), in fewer rows. The logo takes the rest of the row, up to square (rows high, rows * 2 + 2
    wide), its rows those Model usage draws beside it: counted at the width the logo leaves it, and
    again if there its tables no longer fit side by side (stacked it is taller, so the logo is wider
    and Model usage only narrower: they stay stacked). Given less than square, the logo's drawing
    shrinks to the width (logo_cells fits the drawing to the grid) inside a panel still exactly as
    tall as Model usage, which is what the live view's locator relies on (app.Scroll: the logo's panel
    closes the dashboard). It is left out only when Model usage is under LOGO_MIN_ROWS rows (counted
    beside the smallest logo when side by side it is shorter), or when not even the smallest logo
    (LOGO_MIN_ROWS square: LOGO_MIN_COLS columns of drawing, LOGO_PANEL_PAD more of panel) fits beside it.

    Measured on the owner's window (2026-10-04, plan limits, tokens and by-model data present): the
    plan-limit rows are the widest, 72 cells ("5-hour … resets Mon 01:00 · in 4h41") and 73 ("weekly …
    resets Sun 13:00 · in 6d16h"), the token and by-model tables 54 each, so Model usage needs 77
    columns. At 99 the logo gets 21 (17 of drawing beside 10 rows), the smallest fits from 94, the
    square from 104. The fixed cut-off this replaces (the logo from 100 columns, whatever Model usage
    held) left a 99-column window with no logo, and clipped the weekly row at 100 ("in …"). A row
    that grows takes its room from the logo, never the reverse: a window that reset on an earlier day
    with no plan data since is the widest, 80 ("reset Sun 12:13 · no data since": the logo from 97; it
    read "· no plan data since", 85, the logo from 102). Plan data gone stale no longer grows a row (it
    added " · as of HH:MM": Model usage 91, the logo from 108); the subtitle says it, so at 99 the logo
    shows in every state.

    Side by side (owner, 2026-10-25: a wide window left Model usage's right half empty), MEASURED on
    the tests' common case (tests/scenarios.py midway): the token table is 54 columns and the by-model
    table 53, so with TABLES_GAP they fit in a Model usage 115 wide; beside its 7 rows the logo's
    square is 16 columns, so they sit side by side from 136 columns, and at 135 Model usage is drawn
    stacked, 12 rows, beside a square logo of 26."""
    usage = usage_panel(f, width)
    if logo is None:
        return usage

    def rows_at(w: int) -> int:  # Model usage's rows drawn w wide: fewer once its tables fit side by side
        return len(console.render_lines(usage, console.options.update(width=w, height=None))) - 2

    opts = console.options.update(width=width, height=None)
    need = Measurement.get(console, opts, usage).maximum
    sub = usage.subtitle
    if sub:  # in the bottom border: a space either side of it, then two cells of border
        need = max(need, (Text.from_markup(sub) if isinstance(sub, str) else sub).cell_len + 6)
    rows = rows_at(width)
    if rows < LOGO_MIN_ROWS:  # side by side too short for the logo, which beside it they may stack for
        rows = rows_at(width - (LOGO_MIN_COLS + LOGO_PANEL_PAD) - 1)
    cols = min(rows * 2 + 2, width - need - 1 - LOGO_PANEL_PAD)  # the row: Model usage, a space, the logo's panel
    if rows < LOGO_MIN_ROWS or cols < LOGO_MIN_COLS:
        return usage
    beside = rows_at(width - (cols + LOGO_PANEL_PAD) - 1)
    if beside != rows:  # beside the logo the tables stack: a taller Model usage, a wider logo, so they stay stacked
        rows, cols = beside, min(beside * 2 + 2, width - need - 1 - LOGO_PANEL_PAD)
    g = Table.grid(padding=(0, 1))
    g.add_column(width=width - (cols + LOGO_PANEL_PAD) - 1)
    g.add_column(width=cols + LOGO_PANEL_PAD)
    g.add_row(usage, logo_panel(logo, rows, cols))
    return g

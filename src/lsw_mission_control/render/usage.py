"""Model usage: the plan limits (from the status line or the probe) and the tokens counted."""

from __future__ import annotations

import json
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
        for key, label in (("five_hour", "5-hour"), ("seven_day", "weekly")):
            w = limits.get(key)
            if not isinstance(w, dict):
                continue
            pct = num(w.get("used_percentage"))
            resets = num(w.get("resets_at"))
            if resets and resets <= t_now:
                # The window reset after the last plan data: its use since then is unknown.
                g.add_row(label, meter(0.0, METER_W, C.FAINT) + Text("    ?%", style=C.FAINT)
                          + Text(f"  reset {clock(resets)} · no plan data since", style=C.FAINT))
                continue
            col = C.GREEN if pct < 50 else C.AMBER if pct < 80 else C.RED
            # Stale: a faint meter and a muted share; how old the data is, the subtitle says in amber
            # (a note on each row widened Model usage, and pushed the logo out of a 99-column window).
            line = meter(pct / 100, METER_W, C.FAINT if stale else col)
            line.append(f"  {pct:3.0f}%", style=C.MUTED if stale else f"bold {col}")
            if resets:
                line.append(f"   resets {clock(resets)} · in {human(resets - t_now)}", style=C.MUTED)
            g.add_row(label, line)
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
    # A grid with no row (a plan-data record with no window this panel reads, and no status) draws
    # nothing, but rich measures its flexible column as wide as it is offered: usage_row would read
    # Model usage as the whole row wide and leave the logo out. Left out, it draws the same.
    parts = ([g] if g.row_count else []) + [Text(""), t]
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
        parts += [Text(""), m]
    src = {"probe": "probe", None: "terminal"}.get(usage.get("source") if usage else None, "terminal")
    sub = "" if not usage else (f"plan data as of {clock(at)} · {human(t_now - at)} ago · {src}" if at
                                else f"plan data of unknown age · {src}")
    return panel(Group(*parts), "Model usage", sub, C.AMBER if usage and stale else None)


def usage_row(console: Console, f: Frame, width: int, logo: LogoAnimator | None):
    """Model usage with the logo panel to its right, both the same height, when there is room.

    Model usage keeps its own width, MEASURED from what it draws now (rich's Measurement of the
    panel: its widest row, border and padding included; its subtitle too; usage_panel leaves out a
    grid with no row, which rich would measure the whole width), so the logo never clips it. The
    logo takes the rest of the row, up to square (rows high, rows * 2 + 2 wide); given less,
    its drawing shrinks to the width (logo_cells fits the drawing to the grid) inside a panel still
    exactly as tall as Model usage, which is what the live view's locator relies on (app.Scroll: the
    logo's panel closes the dashboard). It is left out only when Model usage is under LOGO_MIN_ROWS
    rows, or when not even the smallest logo (LOGO_MIN_ROWS square: LOGO_MIN_COLS columns of
    drawing, LOGO_PANEL_PAD more of panel) fits beside it.

    Measured on the owner's window (2026-10-04, plan limits, tokens and by-model data present): the
    plan-limit rows are the widest, 72 cells ("5-hour … resets Mon 01:00 · in 4h41") and 73 ("weekly …
    resets Sun 13:00 · in 6d16h"), the token and by-model tables 54 each, so Model usage needs 77
    columns. At 99 the logo gets 21 (17 of drawing beside 10 rows), the smallest fits from 94, the
    square from 104. The fixed cut-off this replaces (the logo from 100 columns, whatever Model usage
    held) left a 99-column window with no logo, and clipped the weekly row at 100 ("in …"). A row
    that grows takes its room from the logo, never the reverse: a reset passed with no plan data since
    81 (the logo from 98). Plan data gone stale no longer grows a row (it added " · as of HH:MM":
    Model usage 91, the logo from 108); the subtitle says it, so at 99 the logo shows in every state."""
    usage = usage_panel(f, width)
    if logo is None:
        return usage
    opts = console.options.update(width=width, height=None)
    rows = len(console.render_lines(usage, opts)) - 2
    need = Measurement.get(console, opts, usage).maximum
    sub = usage.subtitle
    if sub:  # in the bottom border: a space either side of it, then two cells of border
        need = max(need, (Text.from_markup(sub) if isinstance(sub, str) else sub).cell_len + 6)
    cols = min(rows * 2 + 2, width - need - 1 - LOGO_PANEL_PAD)  # the row: Model usage, a space, the logo's panel
    if rows < LOGO_MIN_ROWS or cols < LOGO_MIN_COLS:
        return usage
    g = Table.grid(padding=(0, 1))
    g.add_column(width=width - (cols + LOGO_PANEL_PAD) - 1)
    g.add_column(width=cols + LOGO_PANEL_PAD)
    g.add_row(usage, logo_panel(logo, rows, cols))
    return g

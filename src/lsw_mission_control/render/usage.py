"""Model usage: the plan limits (from the status line or the probe) and the tokens counted."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from lsw_mission_control.render.logo import LogoAnimator, logo_panel
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
            line = meter(pct / 100, METER_W, C.FAINT if stale else col)
            line.append(f"  {pct:3.0f}%", style=C.MUTED if stale else f"bold {col}")
            if stale:
                line.append(f" · as of {clock(at)}" if at else " · age unknown", style=C.AMBER)
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
    parts = [g, Text(""), t]
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
    sub = f"plan data as of {clock(at)} · {human(t_now - at)} ago · {src}" if usage and at else ""
    return panel(Group(*parts), "Model usage", sub)


def usage_row(console: Console, f: Frame, width: int, logo: LogoAnimator | None):
    """Model usage with the logo panel to its right, both the same height (when there is room)."""
    logo_w = None
    if logo is not None and width >= 100:
        probe = usage_panel(f, width - 40)
        rows = len(console.render_lines(probe, console.options.update(width=width - 40, height=None))) - 2
        if rows >= 5:
            logo_w = (rows * 2 + 2) + 4
    if not logo_w:
        return usage_panel(f, width)
    use_w = width - logo_w - 1
    usage = usage_panel(f, use_w)
    rows = len(console.render_lines(usage, console.options.update(width=use_w, height=None))) - 2
    g = Table.grid(padding=(0, 1))
    g.add_column(width=use_w)
    g.add_column(width=(rows * 2 + 2) + 4)
    g.add_row(usage, logo_panel(logo, rows))
    return g

"""Waiting on you: ONLY what the owner can act on now. Work in motion lives in Other work."""

from __future__ import annotations

import re

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lsw_mission_control.notes import Notes
from lsw_mission_control.theme import C
from lsw_mission_control.util import clock, human, now

_URL = re.compile(r"https?://\S+")


def waiting_text(item: str) -> Text:
    """One waiting item: its subject (up to the first colon) in bold, links muted."""
    t = Text()
    head, sep, rest = item.partition(": ")  # a subject ends at ': ' (never a time or a URL)
    if sep and len(head) <= 80 and "://" not in head:
        t.append(head + sep, style=f"bold {C.TEXT}")
        body = rest
    else:
        body = item
    pos = 0
    for m in _URL.finditer(body):
        t.append(body[pos:m.start()], style=C.TEXT)
        t.append(m.group(), style=C.MUTED)
        pos = m.end()
    t.append(body[pos:], style=C.TEXT)
    return t


def notes_panel(notes: Notes, problem: str = "") -> Panel:
    """`problem`: why the notes file did not load (the title says it in full). Then the last good
    notes stay, and the panel is never the green "nothing is waiting on you"."""
    waiting = list(notes.waiting_on_owner)
    mtime = notes.mtime
    g = Table.grid(padding=(0, 1))
    g.add_column(no_wrap=True)
    g.add_column()
    if waiting:
        for i, w in enumerate(waiting, 1):
            g.add_row(Text(f"{i}.", style=f"bold {C.AMBER}"), waiting_text(w))
    elif problem:
        g.add_row(Text("?", style=f"bold {C.AMBER}"), Text("the notes file did not load (see the title)", style=C.AMBER))
    else:
        g.add_row(Text("✓", style=C.GREEN), Text("nothing is waiting on you", style=C.MUTED))
    age = now() - mtime if mtime else None
    if problem:
        sub = Text("not loaded" + (f" · showing {clock(mtime)}" if mtime else ""), style=C.AMBER)
    else:
        sub = Text(f"updated {clock(mtime)} · {human(age)} ago" if mtime else "no notes file",
                   style=C.AMBER if age and age > 3600 else C.FAINT)
    title = f"Waiting on you · {len(waiting)}" if waiting else "Waiting on you"
    return Panel(g, title=Text(f" {title} ", style=f"bold {C.BG} on {C.AMBER}" if waiting else f"bold {C.TEXT}"),
                 title_align="left", subtitle=sub, subtitle_align="right",
                 border_style=C.AMBER if waiting or problem else C.BORDER, box=box.ROUNDED, padding=(0, 1),
                 style=f"on {C.BG}")

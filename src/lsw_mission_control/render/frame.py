"""The first row (the project chip, the clock, any load problem) and the last (the Key, the network flag)."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

from lsw_mission_control.net import network_flag
from lsw_mission_control.render.widgets import legend_line
from lsw_mission_control.theme import C
from lsw_mission_control.util import local_now


def title_line(title: str, subtitle: str, reload_note: str = "", plan_note: str = "", notes_note: str = "") -> Text:
    t = Text()
    t.append(f" {title} ", style=f"bold {C.BG} on {C.ACCENT}")
    t.append(f"  {subtitle}", style=f"bold {C.TEXT}")
    t.append(f"   {local_now().strftime('%a %d %b · %H:%M:%S')}", style=C.FAINT)
    if reload_note:
        t.append(f"   {reload_note}", style=f"bold {C.RED_SOFT}")
    if plan_note:
        t.append(f"   {plan_note}", style=f"bold {C.RED_SOFT}")
    if notes_note:
        t.append(f"   {notes_note}", style=f"bold {C.RED_SOFT}")
    return t


def bottom_line(width: int, crit: list[str] | None, pos: str = "") -> Table:
    """The last row: the scroll position and the Key on the left (the Key never truncates); at the
    very bottom right, whether switching networks is safe now."""
    left = Text()
    if pos:
        left.append(pos + "  ", style=C.MUTED)
    left.append_text(legend_line())
    g = Table.grid(padding=(0, 0))
    g.add_column(no_wrap=True, width=left.cell_len)
    g.add_column(no_wrap=True, justify="right", width=max(1, width - left.cell_len), overflow="crop")
    g.add_row(left, network_flag(crit, width - left.cell_len - 2))
    return g

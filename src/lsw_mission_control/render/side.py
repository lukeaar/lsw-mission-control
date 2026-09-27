"""The side row: each plugin's card, then Repository, side by side at equal width, one height."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

from lsw_mission_control.render.widgets import LABEL_W, panel
from lsw_mission_control.theme import C
from lsw_mission_control.util import ago, iso

if TYPE_CHECKING:
    from lsw_mission_control.plugin import SideCard


def card_grid() -> Table:
    """The 2-column grid every side card uses: a faint label column, then the value."""
    g = Table.grid(padding=(0, 1), expand=True)
    g.add_column(style=C.FAINT, no_wrap=True, width=LABEL_W)
    g.add_column(style=C.TEXT, no_wrap=True, overflow="ellipsis", ratio=1)
    return g


def error_card(title: str, message: str) -> SideCard:
    from lsw_mission_control.plugin import SideCard

    g = Table.grid(padding=(0, 1), expand=True)
    g.add_column(style=C.FAINT, no_wrap=True, width=LABEL_W)
    g.add_column(style=C.TEXT, ratio=1)  # an error wraps: all of it must be readable
    g.add_row("error", Text(message[:300], style=C.RED_SOFT))
    return SideCard(title, g)


def side_columns(n: int, width: int) -> int:
    """Each card's width when n cards share the row (one space between them)."""
    return (width - (n - 1)) // n


def repo_grid(store: dict, main_branch: str = "main", remote: str = "origin") -> Table:
    g = dict(store.get("git") or {})
    gh = dict(store.get("release_gh") or {})
    t = card_grid()
    t.add_row(Text(main_branch), Text(g.get("head", "…"), style=C.TEXT))
    unpushed = g.get("unpushed", "?")
    t.add_row("unpushed", Text(f"{unpushed} commits", style=C.AMBER if unpushed not in ("0", "?") else C.GREEN))
    br = g.get("branches", 0)
    wt = g.get("worktrees", 0)
    t.add_row("branches", Text(f"{br}", style=C.GREEN if br == 0 else C.MUTED) +
              Text(f"  ·  {wt} worktree{'' if wt == 1 else 's'}", style=C.MUTED))
    pr = gh.get("prs")
    if pr is None:
        t.add_row("pull reqs", Text("…", style=C.FAINT))
    else:
        line = Text(f"{pr['open']} open", style=C.AMBER if pr["open"] else C.GREEN)
        extra = [x for x in (f"{pr['bots']} from bots" if pr["bots"] else "",
                             f"{pr['drafts']} draft{'' if pr['drafts'] == 1 else 's'}" if pr["drafts"] else "") if x]
        if extra:
            line.append("  ·  " + " · ".join(extra), style=C.MUTED)
        t.add_row("pull reqs", line)
    r = gh.get("last_ci")
    if r:
        concl = r.get("conclusion") or r.get("status") or "?"
        if concl == "success":
            mark = Text("✓", style=C.GREEN)
        elif concl in ("failure", "cancelled", "timed_out", "startup_failure"):
            mark = Text(f"✕ {concl}", style=C.RED)
        else:
            mark = Text(concl.replace("_", " "), style=C.AMBER)
        line = Text("CI ", style=C.MUTED) + mark + Text(f" {r.get('headSha', '')[:8]}", style=C.FAINT)
        line.append(f" · {ago(iso(r.get('updatedAt') or r['createdAt']))}", style=C.FAINT)
        if gh.get("main_sha") and r.get("headSha") != gh["main_sha"]:
            line.append(f"  not {remote}/{main_branch} HEAD", style=C.AMBER)
        t.add_row("last CI", line)
    rel = gh.get("last_release")
    if rel:
        tag = rel.get("tagName", "?")
        line = Text(tag, style=C.TEXT)
        if rel.get("publishedAt"):
            line.append(f" · {ago(iso(rel['publishedAt']))}", style=C.FAINT)
        t.add_row("last release", line)
    return t


def side_panels(cards: list[SideCard], wrap=None):
    """The cards side by side, every grid padded with empty rows to the tallest. `wrap(i, card,
    its panel)` may put each card's panel in a renderable of its own (the engine's containment)."""
    n = max(c.grid.row_count for c in cards)
    for c in cards:
        while c.grid.row_count < n:
            c.grid.add_row("", "")
    side = Table.grid(expand=True, padding=(0, 1))
    for _c in cards:
        side.add_column(ratio=1)
    panels = [panel(c.grid, c.title, c.subtitle, c.subtitle_style) for c in cards]
    if wrap is not None:
        panels = [wrap(i, c, pnl) for i, (c, pnl) in enumerate(zip(cards, panels))]
    side.add_row(*panels)
    return side

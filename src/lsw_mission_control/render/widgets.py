"""Shared pieces of every panel: bars, meters, the work table and its rows, phrase packing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lsw_mission_control.theme import C
from lsw_mission_control.util import clock, fit, human, now

if TYPE_CHECKING:
    from lsw_mission_control.config import FinalMergeCfg
    from lsw_mission_control.plan import Plan
    from lsw_mission_control.progress import Prog

MAX_W = 150  # the default cap: the whole dashboard is at most this wide, so every panel shares one right edge
LABEL_W = 12  # the label column of the side and usage panels ("last release")
METER_W = 24  # every single meter: the release, a plugin's job, the plan limits
STAGE_W, WHEN_W = 15, 18  # fixed columns: stage name, "~6h41 · Sat 00:46"
CHIP_JOIN = "─"  # one-character lines between stage dots, in every panel
STAGES_MIN = 16  # the least stages column fit_stages() cuts to: the tag row's "CI, tag, release" sits in it
ITEM_MIN = 6  # the least the names get beside a stages column cut to make room: what 80 columns leave them ("Expo…")


def bar(fraction: float, width: int, colour: str) -> Text:
    """A progress bar: it fills as the work gets done, and its colour is the time left."""
    fraction = min(max(fraction, 0.0), 1.0) if fraction == fraction else 0.0  # NaN: empty, never a crash
    full = int(round(fraction * width))
    t = Text("━" * full, style=colour)
    t.append("━" * (width - full), style=C.SURFACE)
    return t


def meter(fraction: float, width: int, colour: str) -> Text:
    """A level, not progress (the plan limits): a different glyph, so it never reads as a bar."""
    fraction = min(max(fraction, 0.0), 1.0) if fraction == fraction else 0.0
    full = int(round(fraction * width))
    t = Text("▰" * full, style=colour)
    t.append("▱" * (width - full), style=C.BORDER)
    return t


def eta_colour(remaining: float | None, failed: bool = False) -> str:
    if failed:
        return C.RED_SOFT
    if remaining is None:
        return C.FAINT
    if remaining <= 0:
        return C.GREEN
    if remaining <= 60 * 60:
        return C.ACCENT
    if remaining <= 4 * 3600:
        return C.AMBER
    return C.RED


def panel(body, title: str, subtitle: str = "", sub_style: str | None = None) -> Panel:
    return Panel(body, title=Text(f" {title} ", style=f"bold {C.TEXT}"), title_align="left",
                 subtitle=Text(subtitle, style=sub_style or C.FAINT) if subtitle else None, subtitle_align="right",
                 border_style=C.BORDER, box=box.ROUNDED, padding=(0, 1), style=f"on {C.BG}")


def pack(phrases: list[Text], width: int, indent: int = 0) -> Text:
    """Phrases joined by ' · ', wrapping only BETWEEN phrases, never inside one."""
    out = Text()
    used = 0
    for i, ph in enumerate(phrases):
        if i and used + 5 + ph.cell_len <= width:
            out.append("  ·  ", style=C.FAINT)
            used += 5
        elif i:
            out.append("\n" + " " * indent)
            used = indent
        out.append_text(ph)
        used += ph.cell_len
    return out


def chips_of(marks, release: bool = False, join: str = CHIP_JOIN) -> Text:
    """One dot per stage joined by a line, the same in every table and centred in its column;
    the stage column beside it names the one that is running."""
    chips = Text()
    for i, (sym, col) in enumerate(marks):
        if i:
            chips.append(join, style=C.SURFACE)
        chips.append(sym, style=col)
    return chips


def stages_width(plan: Plan, fm: FinalMergeCfg) -> int:
    """Wide enough for the row with the most stages (at least 16), so every row's dots and
    lines fit whole and centre on one axis."""
    most = max([3 + len(plan.before(it.key)) for it in plan.items if it.key] + [len(o.stages) for o in plan.other]
               + [len(fm.stages)], default=3)
    return max(STAGES_MIN, most + len(CHIP_JOIN) * (most - 1))


def fit_stages(width: int, stages_w: int) -> int:
    """The stages column a work table draws: `stages_w`, cut where it would leave the names less
    than ITEM_MIN, never below STAGES_MIN. A row of many stages then ends its dots in "…" (the stage
    column beside them still names the running one) instead of pushing the names, and with them the
    finish times, out of the table."""
    # what is left beside the border and gaps (4 + 8), the fixed columns, the least bar and its share
    spare = width - 4 - 8 - STAGE_W - WHEN_W - (8 + 5) - ITEM_MIN
    return min(stages_w, max(STAGES_MIN, spare))


def table_widths(width: int, plan: Plan, fm: FinalMergeCfg, stages_w: int | None = None) -> tuple[int, int]:
    """(item column, bar) widths that fit the dashboard exactly, so nothing gets truncated:
    the panel's border and padding take 4, the four gaps between five columns take 8. Names
    get the room first; the bar keeps at least 8. Beside the stages column work_table draws
    (fit_stages()), names keep at least ITEM_MIN from 80 columns up; narrower, what is left, but
    never less than one cell (a column of no width, or less, ran its rows past the panel's edge and
    cut their finish times)."""
    room = width - 4 - ((stages_w or stages_width(plan, fm)) + STAGE_W + WHEN_W) - 8
    longest = max(len(n) for n in [it.name for it in plan.items] + [o.name for o in plan.other] + [fm.name])
    item_w = max(16, min(longest, room - 5 - 8))
    bar_w = max(8, min(40, room - 5 - item_w))
    return max(1, room - 5 - bar_w), bar_w


def work_table(header: str, width: int, plan: Plan, fm: FinalMergeCfg, stages_w: int | None = None) -> tuple[Table, int]:
    stages_w = fit_stages(width, stages_w or stages_width(plan, fm))
    item_w, bar_w = table_widths(width, plan, fm, stages_w)
    t = Table(box=None, show_header=True, header_style=f"bold {C.FAINT}", pad_edge=False, expand=False)
    t.add_column("item", style=C.TEXT, no_wrap=True, width=item_w, overflow="ellipsis")
    t.add_column(header, no_wrap=True, width=stages_w, justify="center", overflow="ellipsis")
    t.add_column("stage", no_wrap=True, style=C.MUTED, width=STAGE_W, overflow="ellipsis")
    t.add_column("progress", no_wrap=True, width=bar_w + 5)
    t.add_column("eta", no_wrap=True, justify="right", style=C.MUTED, width=WHEN_W)
    return t, bar_w


def work_row(t: Table, name: str, p: Prog, bar_w: int, release: bool = False, join: str = CHIP_JOIN) -> None:
    """The bar fills as the work gets done (full = finished); its colour is the time left."""
    t_now = now()
    if p.current == "done":
        t.add_row(Text(name, style=C.MUTED), chips_of(p.marks, release, join), Text("done", style=C.GREEN),
                  bar(1.0, bar_w, C.GREEN) + Text(" 100%", style=C.GREEN), Text("✓", style=C.GREEN))
        return
    if getattr(p, "hold_ended", None) is not None:
        # The owner's hold is over and nothing has resumed the work: not held, not failed, and nothing
        # runs, so it has no finish of its own. Its work done fills the bar in muted grey.
        t.add_row(Text(name), chips_of(p.marks, release, join), Text("hold ended", style=C.AMBER),
                  bar(p.fraction, bar_w, C.MUTED) + Text(f" {p.fraction * 100:3.0f}%", style=C.MUTED),
                  Text("not resumed", style=C.AMBER))
        return
    if p.paused:
        resume = getattr(p, "resume", None)
        # A held row has no time left to colour its bar: the work done fills it in muted grey (the
        # empty part's own colour, as it was, drew a held row at 71% as an empty bar).
        t.add_row(Text(name, style=C.MUTED), chips_of(p.marks, release, join), Text("paused", style=C.AMBER),
                  bar(p.fraction, bar_w, C.MUTED) + Text(f" {p.fraction * 100:3.0f}%", style=C.MUTED),
                  Text(f"from {clock(resume)}", style=C.AMBER) if resume and resume > t_now else Text("—", style=C.MUTED))
        return
    colour = eta_colour(p.remaining, p.failed)
    if p.failed:
        stage = Text(p.current, style=f"bold {C.RED_SOFT}")
        when = Text("needs rerun", style=f"bold {C.RED_SOFT}")
    else:
        if p.alert:
            stage = Text(p.current, style=C.RED)
        elif p.over > 0:
            stage = Text(f"{p.current} +{human(p.over)}", style=C.AMBER)
        else:
            stage = Text(p.current, style=C.MUTED if p.waiting else C.ACCENT_SOFT)
        if p.remaining is None:
            when = Text("—", style=C.FAINT)
        else:
            when = Text(f"{'≥' if p.over > 0 else '~'}{human(p.remaining)} · {clock(t_now + p.remaining)}", style=colour)
    t.add_row(Text(name), chips_of(p.marks, release, join), stage,  # a name is text, never markup
              bar(p.fraction, bar_w, colour) + Text(f" {p.fraction * 100:3.0f}%", style=C.MUTED), when)


def finished_row(t: Table, n: int) -> None:
    """Every finished item as one quiet row: the live ones stay on top."""
    t.add_row(Text.assemble(("● ", C.GREEN), (f"{n} finished", C.MUTED)), "", "", "", Text("✓", style=C.GREEN))


def legend_line() -> Text:
    legend = Text("Key: ", style=C.FAINT)
    keys = (("done", C.GREEN), ("< 1 h", C.ACCENT), ("< 4 h", C.AMBER), ("> 4 h", C.RED), ("failed", C.RED_SOFT))
    for i, (label, col) in enumerate(keys):
        legend.append("━━ ", style=col)
        legend.append(label + ("" if i == len(keys) - 1 else "   "), style=C.FAINT)
    return legend


def milestone_row(t: Table, name: str, name_style: str, stages, m: dict, bar_w: int, finish_word: str,
                  finish_style: str) -> None:
    """The last two rows show when each starts, how long it takes and when it finishes (never a
    running total); once started, since when and the time left; once done, when it finished."""
    t_now = now()
    label = Text(name, style=name_style)
    if m["status"] == "done":
        # A finish on another day carries its weekday ('Thu 18:36'); 'took' goes, not the time.
        took, fin = human(m["end"] - m["start"]), clock(m["end"])
        t.add_row(label, stages, Text(f"from {clock(m['start'])}", style=C.MUTED),
                  bar(1.0, bar_w, C.GREEN) + Text(" 100%", style=C.GREEN),
                  Text(fit(WHEN_W, f"took {took} · {fin}", f"{took} · {fin}"), style=C.GREEN))
    elif m["status"] in ("running", "failed"):
        failed = m["status"] == "failed"
        col = eta_colour(m["end"] - t_now, failed)
        t.add_row(label, stages, Text(f"since {clock(m['start'])}", style=C.MUTED),
                  bar(m["fraction"], bar_w, col) + Text(f" {m['fraction'] * 100:3.0f}%", style=C.MUTED),
                  Text("needs rerun", style=f"bold {C.RED_SOFT}") if failed
                  else Text(f"~{human(m['end'] - t_now)} · {clock(m['end'])}", style=col))
    else:
        starts = "from now" if m["start"] <= t_now + 60 else f"from {clock(m['start'])}"
        t.add_row(label, stages, Text(starts, style=C.MUTED), Text(f"takes ~{human(m['dur'])}", style=C.MUTED),
                  Text(f"{finish_word} {clock(m['end'])}", style=finish_style))

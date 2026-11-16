"""The Compute panel: the heavy jobs here and on the plugins' remotes, one row each: where it runs,
what it is, its state, how long it has run, its CPU (in cores) and memory, its progress and its
finish. Its progress and eta columns are the work tables' (the same bar at the same width, in the
same place), so they line up with the release panel's above it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group
from rich.table import Table
from rich.text import Text

from lsw_mission_control.render.widgets import WHEN_W, bar, eta_colour, panel, work_bar_w
from lsw_mission_control.theme import C
from lsw_mission_control.util import clock, human

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame
    from lsw_mission_control.sources.compute import ComputeJob

STALE_S = 5 * 60  # a remote's rows read longer ago than this say how long, in their state column
JOB_MIN, JOB_WANT = 10, 28  # the job column: never narrower; columns go until a label this long fits whole
DETAIL_MIN = 8  # a detail cut shorter than this is not shown at all
# What goes, in this order, while the job column is short of its labels: then the unit word of each
# count (when the rows count different things), then the state's reason ("frozen (battery)": "frozen",
# "read 10m ago": "10m ago"); then the bar gives up cells, so a finish time is never cut.
DROP = ("memory", "running", "cores")
SEPS = " ·,;:–-"  # what a cut never ends on before its "…"


def _gb(n: float) -> str:
    return f"{n / 2**30:.1f} GB" if n >= 2**30 / 10 else f"{n / 2**20:.0f} MB"


def _count(x: float) -> str:
    return f"{x:,.0f}" if x == int(x) else f"{x:,.1f}"


def cut_words(text: str, width: int) -> str:
    """`text` whole when it fits, else as many whole words as fit with "…" after them (never a word
    cut in two, nor a cut that ends on a separator); "" when not even one word fits."""
    if cell_len(text) <= width:
        return text
    out = ""
    for word in text.split(" "):
        nxt = f"{out} {word}" if out else word
        if cell_len(nxt) + 1 > width:
            break
        out = nxt
    out = out.rstrip(SEPS)
    return f"{out}…" if out else ""


def job_cell(j: ComputeJob, width: int, muted: bool) -> Text:
    """The label, then its detail dimmed after it where there is room (cut at a word)."""
    label = cut_words(j.label, width) or j.label[: max(0, width - 1)] + "…"
    t = Text(label, style=C.MUTED if muted else C.TEXT)
    room = width - cell_len(label) - 3
    if j.detail and label == j.label and room >= DETAIL_MIN + 1:
        detail = cut_words(j.detail, room)
        if detail == j.detail or cell_len(detail) > DETAIL_MIN:
            t.append(f" · {detail}", style=C.FAINT)
    return t


def compute_panel(f: Frame, width: int):
    """None with nothing heavy running and nothing that failed to load; else the panel."""
    rows: list[ComputeJob] = f.compute
    if not rows and not f.compute_errors:
        return None
    here, t_now = f.cfg.compute.local_name, f.now
    inner = width - 4
    bar_w = work_bar_w(width, f.plan, f.cfg.release.final_merge)

    def where(j: ComputeJob) -> str:
        return here if j.machine == "local" else j.machine

    def stale(j: ComputeJob) -> float | None:
        return t_now - j.as_of if j.as_of is not None and t_now - j.as_of > STALE_S and not j.ended else None

    def state_of(j: ComputeJob, short: bool) -> tuple[str, str]:
        if j.ended:
            return f"ended {clock(j.ended)}", C.FAINT
        age = stale(j)
        if age is not None:
            return f"{'' if short else 'read '}{human(age)} ago", C.AMBER
        word = j.state or ("waiting" if j.waiting else "")
        if word:
            return (word.split(" (")[0] if short else word), C.AMBER
        return "running", C.ACCENT_SOFT

    def held(j: ComputeJob) -> bool:  # nothing of it runs now: no finish of its own
        return bool(j.ended or j.state or j.waiting)

    counted = [j for j in rows if j.done is not None]
    units = {j.unit for j in counted}
    shared_unit = units.pop() if len(units) == 1 and next(iter(units)) else ""

    def done_text(j: ComputeJob, with_unit: bool) -> str:
        if j.done is None:
            return ""
        n = f"{_count(j.done)}/{_count(j.total)}" if j.total else _count(j.done)
        return f"{n} {j.unit}" if with_unit and j.unit and not shared_unit else n

    # Each column's width from what it holds; the job column takes what is left.
    o = {"gone": set(), "short_state": False, "with_unit": True}
    show_progress = bool(counted)

    def widths() -> dict[str, int]:
        w = {"machine": max([cell_len("machine")] + [cell_len(where(j)) for j in rows]),
             "state": max([cell_len("state")] + [cell_len(state_of(j, o["short_state"])[0]) for j in rows]),
             "running": cell_len("running"), "cores": cell_len("cores"),
             "memory": max([cell_len("memory")] + [cell_len(_gb(j.mem)) for j in rows if j.mem is not None])}
        if show_progress:
            w["done"] = max([cell_len(shared_unit or "done")] + [cell_len(done_text(j, o["with_unit"])) for j in rows])
            w["progress"] = bar_w + 5
        w["eta"] = WHEN_W
        return {k: v for k, v in w.items() if k not in o["gone"]}

    def job_room(w: dict[str, int]) -> int:
        return inner - sum(w.values()) - 2 * len(w)  # a 2-cell gap before each column after the job's

    steps = [lambda k=k: o["gone"].add(k) for k in DROP]
    if any(j.unit for j in counted) and not shared_unit:
        steps.append(lambda: o.update(with_unit=False))
    if any(" (" in j.state for j in rows) or any(stale(j) is not None for j in rows):
        steps.append(lambda: o.update(short_state=True))
    want = min(JOB_WANT, max([cell_len(j.label) for j in rows] or [JOB_MIN]))
    w = widths()
    for step in steps:
        if job_room(w) >= want:
            break
        step()
        w = widths()
    if job_room(w) < JOB_MIN and show_progress:
        bar_w = max(4, bar_w - (JOB_MIN - job_room(w)))
        w = widths()
    with_unit, short_state = o["with_unit"], o["short_state"]
    job_w = max(JOB_MIN, job_room(w))

    t = Table(box=None, show_header=True, header_style=f"bold {C.FAINT}", pad_edge=False, expand=False)
    t.add_column("machine", width=w["machine"], no_wrap=True)
    t.add_column("job", width=job_w, no_wrap=True, overflow="ellipsis")
    t.add_column("state", width=w["state"], no_wrap=True, overflow="ellipsis")
    for k in ("running", "cores", "memory"):
        if k in w:
            t.add_column(k, width=w[k], no_wrap=True, justify="right")
    if show_progress:
        t.add_column(shared_unit or "done", width=w["done"], no_wrap=True, justify="right")
        t.add_column("progress", width=w["progress"], no_wrap=True)
    t.add_column("eta", width=WHEN_W, no_wrap=True, justify="right")

    last_machine = None
    counts: dict[str, int] = {}
    for j in rows:
        machine = where(j)
        state, state_style = state_of(j, short_state)
        key = "ended" if j.ended else state.split(" ")[0] if state_style == C.AMBER and not stale(j) else "running"
        counts[key] = counts.get(key, 0) + 1
        cells: list = [Text(machine if machine != last_machine else "", style=C.MUTED),
                       job_cell(j, job_w, muted=held(j)), Text(state, style=state_style)]
        last_machine = machine
        live = not j.ended
        if "running" in w:
            cells.append(Text(human(t_now - j.started) if j.started and live else "", style=C.MUTED))
        if "cores" in w:
            cells.append(Text(f"{j.cores:.1f}", style=C.MUTED) if j.cores is not None
                         else Text("—" if live else "", style=C.FAINT))
        if "memory" in w:
            cells.append(Text(_gb(j.mem), style=C.MUTED) if j.mem is not None
                         else Text("—" if live else "", style=C.FAINT))
        eta = None if held(j) else j.eta
        colour = C.MUTED if eta is None else eta_colour(eta)
        if show_progress:
            cells.append(Text(done_text(j, with_unit), style=C.MUTED))
            if j.done is not None and j.total:
                frac = j.done / j.total
                complete = frac >= 1
                cells.append(bar(frac, bar_w, C.GREEN if complete else colour)
                             + Text(f" {min(frac, 1.0) * 100:3.0f}%", style=C.GREEN if complete else C.MUTED))
            else:
                cells.append(Text(""))
        if j.ended:
            when = Text("")
        elif eta is None:
            when = Text("—", style=C.FAINT)
        elif eta <= 0:
            when = Text("✓", style=C.GREEN)
        else:
            when = Text(f"~{human(eta)} · {clock(t_now + eta)}", style=colour)
        cells.append(when)
        t.add_row(*cells)

    parts: list = [t] if rows else []
    if f.compute_errors:
        if rows:
            parts.append(Text(""))
        parts += [Text(f"not read: {e}", style=C.AMBER) for e in f.compute_errors]
    order = ["running", *sorted(k for k in counts if k not in ("running", "ended")), "ended"]
    subtitle = " · ".join(f"{counts[k]} {k}" for k in order if counts.get(k))
    return panel(Group(*parts), "Compute", subtitle)

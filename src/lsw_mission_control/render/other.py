"""Other work in progress: everything the release does not wait for."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from lsw_mission_control.progress import Prog, in_wait_order, live_job_progress, short_name, stages_progress
from lsw_mission_control.render.compute import compute_section
from lsw_mission_control.render.widgets import pack, panel, work_row, work_table
from lsw_mission_control.theme import C
from lsw_mission_control.util import clock, now

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame


def other_panel(f: Frame, width: int):
    plan, labels, rc = f.plan, f.labels, f.cfg.release
    t_now = now()
    share = rc.fix_share
    job = f.live_job
    index: dict = {}
    for i, item in enumerate(plan.other):
        index.setdefault(item.name, i)

    def targets_of(i: int) -> list[int]:
        # A name the plan no longer holds is behind it: a finished row leaves the plan by hand.
        after = plan.other[i].after
        return [index[after]] if after in index else []

    def compute(i: int, wait) -> Prog:
        item = plan.other[i]
        stages = item.stages
        if item.after_server:
            # Its later stages are held while the owner holds it, as any held row's are: a stopped
            # re-run is held, never undone by the result before it (stages_progress, `paused`). It
            # follows its live job alone: its `after` has no effect.
            p = live_job_progress(stages, labels, job, f.live_job_error, now=t_now, cal=None, default_fix_share=share,
                                  paused=item.paused)
        else:
            # Until that work is done: "after <name>" while nothing of its own runs, and a finish
            # never before that work's (begun or not); none while that work has none.
            p = stages_progress(stages, labels, now=t_now, cal=None, default_fix_share=share,
                                wait_before=0.0 if wait is None else wait[0],
                                after="" if wait is None else short_name(plan.other[wait[1]].name, plan.items),
                                paused=item.paused)
        if item.paused and p.current != "done":
            # Held by the owner: no finish time while it waits, and its idle agent (which reads as
            # failed once silent) is not a failure: its stage shows as held.
            p.current, p.failed, p.remaining, p.paused = "paused", False, None, True
        return p

    rows: list[tuple[str, Prog]] = [(item.name, p) for item, p in
                                    zip(plan.other, in_wait_order(len(plan.other), targets_of, compute))]
    live = [r for r in rows if r[1].current != "done"]
    t, bar_w = work_table("stages", width, plan, rc.final_merge)
    # Live rows first; the few finished ones keep their names (they leave the plan by hand).
    for name, p in live + [r for r in rows if r[1].current == "done"]:
        work_row(t, name, p, bar_w)
    npaused = sum(p.paused for _n, p in live)
    phrases = [Text(f"{len(live) - npaused} in progress", style=C.ACCENT_SOFT)]
    if npaused:
        phrases.append(Text(f"{npaused} paused", style=C.AMBER))
    nfailed = sum(p.failed for _n, p in live)
    if nfailed:
        phrases.append(Text(f"{nfailed} failed", style=C.RED_SOFT))
    timed = [r for r in live if r[1].remaining is not None and not r[1].failed]
    if timed:
        # A tie goes to the work finishing on its own: a row that waits can end with it, never first.
        soonest = min(timed, key=lambda r: (r[1].remaining, r[1].waits))
        phrases.append(Text.assemble(("next to finish: ", C.MUTED), (soonest[0], C.TEXT),
                                     (f"  {clock(t_now + soonest[1].remaining)}", f"bold {C.TEXT}")))
    parts = [pack(phrases, width - 4), Text(""), t, *compute_section(f, width - 4)]
    also = list(f.notes.in_progress_elsewhere)
    if also:
        parts.append(Text(""))
        parts.append(Text("also in motion", style=C.FAINT))
        for line in also:
            parts.append(Text.assemble(("◌ ", C.FAINT), (line, C.MUTED)))
    return panel(Group(*parts), "Other work in progress")

"""Other work in progress: everything the release does not wait for."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from lsw_mission_control.progress import Prog, in_wait_order, short_name, stages_progress
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
            first, rest = stages[0], stages[1:]
            if f.live_job_error:
                # The plugin behind this row failed: say so, never read it as "not reached yet".
                p = stages_progress(rest, labels, now=t_now, cal=None, default_fix_share=share)
                p.marks = [("✕", C.RED_SOFT)] + p.marks
                p.remaining, p.current, p.waiting, p.alert = None, "plugin error", False, True
            elif job is None:
                # Never reached (or switched off): the job's state and its time are unknown.
                p = stages_progress(rest, labels, now=t_now, cal=None, default_fix_share=share)
                p.marks = [("○", C.FAINT)] + p.marks
                p.remaining, p.current, p.waiting = None, f"{first[0]}?", True
            elif job.left == 0:
                p = stages_progress(rest, labels, now=t_now, cal=None, default_fix_share=share)
                p.marks = [("●", C.GREEN)] + p.marks
            else:
                # The job's own progress (its share of the queue) counts as work done; the time it
                # has left weights it. Stalled or out of touch: the time is unknown.
                eta = job.eta if job.eta is not None else job.eta_life
                frac = min(job.frac, 0.999)
                done_before = frac / (1 - frac) * (eta or 0.0)
                p = stages_progress(rest, labels, now=t_now, cal=None, default_fix_share=share, wait_before=eta or 0.0,
                                    done_before=done_before)
                if p.current == "done":
                    # Every later stage is behind it (they ran early): the job's own time is what is
                    # left. stages_progress() reads a row with nothing left as done, dropping the wait.
                    ran = p.end - p.start if p.start and p.end and p.end > p.start else 0.0
                    behind = done_before + ran
                    p.remaining = eta or 0.0
                    p.fraction = behind / (behind + p.remaining) if behind + p.remaining > 0 else 0.0
                    p.end = None
                p.marks = [("◉", C.ACCENT)] + p.marks
                p.current, p.waiting = first[0], False
                if not job.live:
                    p.remaining, p.current, p.waiting = None, f"{first[0]}?", True
                elif job.eta is None:
                    p.remaining, p.current, p.alert = None, job.stalled_label, True
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
    parts = [pack(phrases, width - 4), Text(""), t]
    also = list(f.notes.in_progress_elsewhere)
    if also:
        parts.append(Text(""))
        parts.append(Text("also in motion", style=C.FAINT))
        for line in also:
            parts.append(Text.assemble(("◌ ", C.FAINT), (line, C.MUTED)))
    return panel(Group(*parts), "Other work in progress")

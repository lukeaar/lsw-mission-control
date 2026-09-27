"""The release after this one: its planned items, grouped, with their stages and state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from lsw_mission_control.progress import Prog, in_wait_order, short_name, stages_progress
from lsw_mission_control.render.widgets import pack, panel, work_row, work_table
from lsw_mission_control.theme import C
from lsw_mission_control.util import now

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame


def next_panel(f: Frame, width: int):
    """None when the plan has no next release, or it has no items."""
    nxt, labels, rc = f.plan.next, f.labels, f.cfg.release
    if not nxt or not nxt.items:
        return None
    t_now = now()
    # Planned items can have many stages: a column of its own keeps them from widening every
    # other panel's stage column and squeezing the names out.
    most = max((len(i.stages) for i in nxt.items), default=3)
    t, bar_w = work_table("stages", width, f.plan, rc.final_merge, stages_w=max(16, 2 * most - 1))
    live = done = waiting_owner = 0
    group = None
    # Each item after the items it runs after (one listed before them still waits for them). An
    # item not started has no finish time (planned, not scheduled); one begun never finishes before
    # the work it runs after, and has no finish time while that work has none.
    index: dict = {}
    for i, item in enumerate(nxt.items):
        if item.key:
            index.setdefault(item.key, i)
    waits: dict = {}
    begun: dict = {}
    owner_at: dict = {}

    def targets_of(i: int) -> list[int]:
        return [index[fl[6:]] for fl in nxt.items[i].flags if fl.startswith("after:") and fl[6:] in index]

    def compute(i: int, wait) -> Prog:
        item = nxt.items[i]
        waits[i] = wait
        after = "" if wait is None else short_name(nxt.items[wait[1]].name, f.plan.items)
        p = stages_progress(item.stages, labels, now=t_now, cal=None, default_fix_share=rc.fix_share,
                            wait_before=0.0 if wait is None else wait[0], after=after)
        begun[i] = any(m[0] in ("●", "◉", "✕", "–") for m in p.marks)
        # The first stage not yet behind it: when it is the owner's ("your ...", no agent), nothing
        # is running and it waits on nothing else, the row waits on the owner, wherever that stage
        # sits in the row: no finish time (what runs after it has none either).
        nxt_i = next((k for k, m in enumerate(p.marks) if m[0] == "○"), None)
        running = any(m[0] == "◉" for m in p.marks)
        owner_at[i] = nxt_i if (begun[i] and not running and wait is None and nxt_i is not None
                                and item.stages[nxt_i][1] is None
                                and item.stages[nxt_i][0].lower().startswith("your")) else None
        if not begun[i] or owner_at[i] is not None:
            p.remaining = None
        return p

    progs = in_wait_order(len(nxt.items), targets_of, compute)
    for i, item in enumerate(nxt.items):
        if item.group != group:
            group = item.group
            if group:
                t.add_row(Text(group, style=C.FAINT), "", "", "", "")
        p, started, wait = progs[i], begun[i], waits[i]
        blocking = wait is not None
        if p.current == "done":
            done += 1
        elif owner_at[i] is not None:
            p.current, p.waiting = item.stages[owner_at[i]][0], True
            waiting_owner += 1
        elif not started:
            # Planned, not scheduled: no finish time. The first unfinished stage says what it waits
            # for: one named "your ..." (a decision, a go-ahead, a pick-list) is the owner's.
            first = item.stages[0] if item.stages else None
            owner = (first is not None and first[1] is None and first[0].lower().startswith("your")
                     and not blocking)
            if blocking:
                p.current = f"after {short_name(nxt.items[wait[1]].name, f.plan.items)}"
            elif owner:
                p.current = first[0]
            elif first is not None and first[1] is None:
                p.current = first[0]
            else:
                p.current = "planned"
            p.remaining, p.waiting = None, True
            waiting_owner += owner
        else:
            live += 1
        work_row(t, item.name, p, bar_w)
    n = len(nxt.items)
    phrases = [Text(f"{n} items planned", style=C.ACCENT_SOFT)]
    if live:
        phrases.append(Text(f"{live} under way", style=C.ACCENT_SOFT))
    if waiting_owner:
        phrases.append(Text(f"{waiting_owner} wait on you", style=C.AMBER))
    if done:
        phrases.append(Text(f"{done} done", style=C.GREEN))
    if nxt.about:
        phrases.append(Text(nxt.about, style=C.MUTED))
    return panel(Group(pack(phrases, width - 4), Text(""), t), rc.next_title.format(release=nxt.release))

"""The releases after this one, each its own panel: the next release, then each later one (the
plan's `later`), with their planned items, grouped, with their stages and state, and the finished
ones as one row below the rest, as the release panel draws its own."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from lsw_mission_control.progress import Prog, in_wait_order, short_name, stages_progress
from lsw_mission_control.render.widgets import STAGES_MIN, finished_row, pack, panel, work_row, work_table
from lsw_mission_control.theme import C
from lsw_mission_control.util import now

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame
    from lsw_mission_control.plan import NextRelease


def next_panel(f: Frame, width: int):
    """None when the plan has no next release, or it has no items."""
    nxt = f.plan.next
    if not nxt or not nxt.items:
        return None
    return planned_panel(f, width, nxt, f.cfg.release.next_title)


def later_panel(f: Frame, width: int, i: int):
    """The plan's `later[i]`, a release after the next one; None when it has no items. Its items not
    yet begun wait on the release before it (`[release] later_wait`), never on this one."""
    rel = f.plan.later[i]
    if not rel.items:
        return None
    return planned_panel(f, width, rel, f.cfg.release.later_title, f.plan.release_before(i))


def planned_panel(f: Frame, width: int, nxt: NextRelease, title: str, after_release: str | None = None):
    """A release after this one. `after_release`: the release it comes after, which its items not yet
    begun wait on (None for the next release: its items wait on nothing but each other)."""
    labels, rc = f.labels, f.cfg.release
    t_now = now()
    # Planned items can have many stages: each planned release's panel sizes a stage column of its
    # own by its own items, so they never widen another panel's and squeeze its names out (a later
    # release's long item must not change the next release's panel). The panels line up whenever
    # their items have at most 8 stages (STAGES_MIN).
    most = max((len(i.stages) for i in nxt.items), default=3)
    t, bar_w = work_table("stages", width, f.plan, rc.final_merge, stages_w=max(STAGES_MIN, 2 * most - 1))
    live = done = waiting_owner = 0
    group, headed = None, False
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
        p, started, wait = progs[i], begun[i], waits[i]
        if p.current == "done":
            # Every finished item is one row below the rest, as the release's are; a group's heading
            # shows only over rows still drawn.
            done += 1
            continue
        if item.group != group:
            group = item.group
            if group:
                t.add_row(Text(group, style=C.FAINT), "", "", "", "")
                headed = True
        blocking = wait is not None
        if owner_at[i] is not None:
            p.current, p.waiting = item.stages[owner_at[i]][0], True
            waiting_owner += 1
        elif not started:
            # Planned, not scheduled: no finish time. The first unfinished stage says what it waits
            # for: one named "your ..." (a decision, a go-ahead, a pick-list) is the owner's. A later
            # release's item waits on the release before it, the owner's first stage included.
            first = item.stages[0] if item.stages else None
            owner = (first is not None and first[1] is None and first[0].lower().startswith("your")
                     and not blocking and after_release is None)
            if blocking:
                p.current = f"after {short_name(nxt.items[wait[1]].name, f.plan.items)}"
            elif after_release is not None:
                p.current = rc.later_wait.format(release=after_release)
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
    if done:
        if headed:
            # Right under the last group's rows it would read as that group's own: a blank row sets it
            # apart (without headings it follows the rows, as the release panel's does).
            t.add_row("", "", "", "", "")
        finished_row(t, done)
    n = len(nxt.items)
    phrases = [Text(f"{n} item{'' if n == 1 else 's'} planned", style=C.ACCENT_SOFT)]
    if live:
        phrases.append(Text(f"{live} under way", style=C.ACCENT_SOFT))
    if waiting_owner:
        phrases.append(Text(f"{waiting_owner} wait on you", style=C.AMBER))
    if done:
        phrases.append(Text(f"{done} done", style=C.GREEN))
    if nxt.about:
        phrases.append(Text(nxt.about, style=C.MUTED))
    return panel(Group(pack(phrases, width - 4), Text(""), t), title.format(release=nxt.release))

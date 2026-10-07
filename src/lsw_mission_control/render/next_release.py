"""The releases after this one, each its own panel: the next release, then each later one (the
plan's `later`), with their planned items, grouped, with their stages and state, and the finished
ones as one row below the rest, as the release panel draws its own. Every planned release's rows
are worked out together (planned_rows()): an `after:<key>` may name an item of another release."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from rich.console import Group
from rich.text import Text

from lsw_mission_control.progress import (
    Prog,
    hold_left,
    in_wait_order,
    owner_stage,
    short_name,
    stages_active_since,
    stages_progress,
)
from lsw_mission_control.render.widgets import STAGES_MIN, finished_row, pack, panel, work_row, work_table
from lsw_mission_control.theme import C
from lsw_mission_control.util import now

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame
    from lsw_mission_control.plan import NextItem


class Planned(NamedTuple):
    """One planned item's row, worked out with every planned release's (planned_rows())."""

    prog: Prog
    wait: tuple | None  # wait_for() of what it runs after: (its time left or None, its name); None once done
    begun: bool  # a stage of it has begun
    owner_at: int | None  # the stage it waits on the owner for (owner_stage()), when it waits on nothing else
    held: bool  # the owner holds it now
    stopped: bool  # held, or its hold has ended with nothing of it run since


def planned_rows(f: Frame) -> list[list[Planned]]:
    """Every planned release's rows (in Plan.planned() order), worked out together once a frame:
    a row runs after each item its `after:<key>` flags name, whichever release that item is in. A key
    names the item of the row's own release, else of the nearest release before it that has one (this
    release's items last), else of the nearest after it; a key no release holds is ignored. Each row
    is worked out after the rows it runs after (in_wait_order: a cycle, a typo, is broken where it
    closes), and a target with no finish time (a planned item not begun, a failed one, one waiting on
    the owner) leaves the row none either.

    An item not yet begun has no finish time (planned, not scheduled); one begun never finishes
    before the work it runs after. An item the owner holds (`paused`, `paused_until`) is held as a
    release item is: its stages read held, its hold's end is a wait beside the one on that work, and
    once its `paused_until` has passed with nothing of it run since, it is still stopped."""
    memo = f.memo.get("planned")
    if memo is not None:
        return memo
    plan, labels, rc = f.plan, f.labels, f.cfg.release
    t_now = now()
    rels = plan.planned()
    where: list[tuple[int, int]] = [(r, j) for r, rel in enumerate(rels) for j in range(len(rel.items))]
    first: list[dict] = []  # per release: key -> its first item's index in `where`
    g = 0
    for rel in rels:
        keys: dict = {}
        for item in rel.items:
            if item.key:
                keys.setdefault(item.key, g)
            g += 1
        first.append(keys)
    # This release's items, worked out by its own panel's rule (release_rows), only when a planned row
    # runs after one of them.
    this_keys = {it.key for it in plan.items if it.key}
    wanted = [fl[6:] for rel in rels for item in rel.items for fl in item.flags if fl.startswith("after:")]
    this: list = []
    if any(k in this_keys for k in wanted):
        from lsw_mission_control.render.release import release_rows

        this = list(zip(*release_rows(f)))
    this_index = {}
    for i, (it, _p) in enumerate(this):
        if it.key is not None:
            this_index.setdefault(it.key, len(where) + i)
    n_planned = len(where)

    def resolve(r: int, key: str) -> int | None:
        if key in first[r]:
            return first[r][key]
        for r2 in range(r - 1, -1, -1):
            if key in first[r2]:
                return first[r2][key]
        if key in this_index:
            return this_index[key]
        for r2 in range(r + 1, len(rels)):
            if key in first[r2]:
                return first[r2][key]
        return None

    def item_of(i: int) -> NextItem:
        r, j = where[i]
        return rels[r].items[j]

    def name_of(i: int) -> str:
        return item_of(i).name if i < n_planned else this[i - n_planned][0].name

    def targets_of(i: int) -> list[int]:
        if i >= n_planned:
            return []  # this release's rows are worked out already
        r = where[i][0]
        found = [resolve(r, fl[6:]) for fl in item_of(i).flags if fl.startswith("after:")]
        return [t for t in found if t is not None and t != i]

    out: list[list] = [[None] * len(rel.items) for rel in rels]

    def compute(i: int, wait) -> Prog:
        if i >= n_planned:
            return this[i - n_planned][1]
        r, j = where[i]
        item = rels[r].items[j]
        held = item.held(t_now)
        stopped = held or (item.hold_ended(t_now) and not stages_active_since(item.stages, labels, item.paused_until))
        after = "" if wait is None else short_name(name_of(wait[1]), plan.items)
        p = stages_progress(item.stages, labels, now=t_now, cal=None, default_fix_share=rc.fix_share,
                            wait_before=0.0 if wait is None else wait[0], after=after, paused=stopped,
                            hold=hold_left(item, t_now))
        begun = any(m[0] in ("●", "◉", "✕", "–") for m in p.marks)
        # The first stage not yet behind it is the owner's ("your ...", no agent), nothing of it runs
        # and it waits on nothing else: it waits on the owner, with no finish time (what runs after it
        # has none either), begun or not, in the next release or a later one.
        owner_at = owner_stage(item.stages, p) if wait is None and not held else None
        if not begun or owner_at is not None:
            p.remaining = None
        out[r][j] = Planned(p, None if wait is None else (wait[0], name_of(wait[1])), begun, owner_at, held, stopped)
        return p

    in_wait_order(n_planned + len(this), targets_of, compute)
    f.memo["planned"] = out
    return out


def next_panel(f: Frame, width: int):
    """None when the plan has no next release, or it has no items."""
    nxt = f.plan.next
    if not nxt or not nxt.items:
        return None
    return planned_panel(f, width, 0, f.cfg.release.next_title)


def later_panel(f: Frame, width: int, i: int):
    """The plan's `later[i]`, a release after the next one; None when it has no items. Its items not
    yet begun wait on the release before it (`[release] later_wait`), never on this one."""
    rel = f.plan.later[i]
    if not rel.items:
        return None
    return planned_panel(f, width, i + (f.plan.next is not None), f.cfg.release.later_title, f.plan.release_before(i))


def planned_panel(f: Frame, width: int, r: int, title: str, after_release: str | None = None):
    """The planned release Plan.planned()[r]. `after_release`: the release it comes after, which its
    items not yet begun wait on (None for the next release: its items wait on nothing but the items
    they run after)."""
    nxt = f.plan.planned()[r]
    rows = planned_rows(f)[r]
    rc = f.cfg.release
    # Planned items can have many stages: each planned release's panel sizes a stage column of its
    # own by its own items, so they never widen another panel's and squeeze its names out (a later
    # release's long item must not change the next release's panel). The panels line up whenever
    # their items have at most 8 stages (STAGES_MIN).
    most = max((len(i.stages) for i in nxt.items), default=3)
    t, bar_w = work_table("stages", width, f.plan, rc.final_merge, stages_w=max(STAGES_MIN, 2 * most - 1))
    live = done = waiting_owner = npaused = nended = 0
    group, headed = None, False
    for item, row in zip(nxt.items, rows):
        p = row.prog
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
        if row.held:
            # Held by the owner, as a release item is: not failed, not queued, not under way. Begun, it
            # keeps its time left (what runs after it counts it); not begun, it has none.
            p.current, p.failed, p.paused, p.resume = "paused", False, True, item.paused_until
            npaused += 1
        elif row.owner_at is not None:
            p.current, p.waiting, p.owner = item.stages[row.owner_at][0], True, True
            waiting_owner += 1
        elif row.stopped and not (p.waiting and (p.waits or (after_release is not None and not row.begun))):
            # Its hold is over and nothing has resumed it, by the release panel's rule: a row with no
            # stage of it stopped part-way that waits on other work, or on the release before, reads
            # that wait instead; one with a stage stopped part-way is not under way, whatever it waits
            # on. It keeps the time it had while held, as if it resumed now.
            p.current, p.failed, p.hold_ended = "hold ended", False, item.paused_until
            nended += 1
        elif not row.begun:
            # Planned, not scheduled: no finish time. The first unfinished stage says what it waits
            # for. A later release's item waits on the release before it.
            first = item.stages[0] if item.stages else None
            if row.wait is not None:
                p.current = f"after {short_name(row.wait[1], f.plan.items)}"
            elif after_release is not None:
                p.current = rc.later_wait.format(release=after_release)
            elif first is not None and first[1] is None:
                p.current = first[0]
            else:
                p.current = "planned"
            p.remaining, p.waiting = None, True
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
    if npaused:
        phrases.append(Text(f"{npaused} paused", style=C.AMBER))
    if nended:
        phrases.append(Text(f"{nended} not resumed", style=C.AMBER))
    if done:
        phrases.append(Text(f"{done} done", style=C.GREEN))
    if nxt.about:
        phrases.append(Text(nxt.about, style=C.MUTED))
    return panel(Group(pack(phrases, width - 4), Text(""), t), title.format(release=nxt.release))

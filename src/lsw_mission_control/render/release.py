"""The release panel: its items, the final merge, and the tag + release run from GitHub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from lsw_mission_control.progress import (
    Prog,
    hold_left,
    in_wait_order,
    item_active_since,
    item_minutes,
    item_progress,
    item_stages,
    item_started,
    owner_stage,
    short_name,
    stages_progress,
)
from lsw_mission_control.render.widgets import (
    METER_W,
    bar,
    chips_of,
    eta_colour,
    finished_row,
    milestone_row,
    pack,
    panel,
    work_row,
    work_table,
)
from lsw_mission_control.theme import C
from lsw_mission_control.util import clock, human, iso, now

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame
    from lsw_mission_control.plan import Item


def run_left(t0: float, minutes: float, t_now: float) -> tuple[float, float]:
    """(time left, seconds past its median) of a GitHub run created at `t0`, whose workflow's past
    runs took `minutes` (their median). Within the median, what is left of it, at least a minute.
    Past it, as a stage past its estimate has (stages_progress), at least 10 min, or a quarter of its
    time so far: a lower bound, read with `≥`. (It was a minute from now for as long as the run went on.)"""
    elapsed = t_now - t0
    over = elapsed - minutes * 60
    if over > 0:
        return max(10 * 60, 0.25 * elapsed), over
    return max(60.0, -over), 0.0


def tag_milestone(g: dict, merge_end: float, merge_done: bool, ci_min: float, rel_min: float, hands_min: float) -> dict:
    """Tag + release run, from GitHub: CI on the remote main's HEAD (once the final merge is
    done), the release tag, and the release run for it. Not started: it starts when the merge
    is done. `over`: seconds the running CI or release run is past its median (its time left is
    then a lower bound)."""
    t_now = now()
    hands = hands_min * 60
    dur = (ci_min + rel_min) * 60 + hands
    rel, ci, tagged = g.get("rel"), g.get("ci"), g.get("tag")
    start = max(merge_end, t_now)
    todo = {"status": "todo", "start": start, "end": start + dur, "dur": dur, "fraction": 0.0, "phase": ""}
    r0 = iso(rel["createdAt"]) if rel else None
    ci_t0 = iso(ci["createdAt"]) if ci else None
    # Main's CI created after the release run is a later commit's (main moved on after the tag), never the release's.
    ci_counts = ci is not None and (r0 is None or ci_t0 <= r0) and bool(
        tagged or rel or (merge_done and ci_t0 >= merge_end - 300))
    if not (rel or tagged or ci_counts):
        return todo
    # Tagged with neither run listed yet: it began when the merge finished (not 'now', which slides).
    start = ci_t0 if ci_counts else r0 if rel else merge_end if merge_done else t_now
    queued = ("queued", "waiting", "pending", "requested")
    over = 0.0
    if rel:
        if rel.get("status") == "completed":
            if rel.get("conclusion") == "success":
                return {"status": "done", "start": start, "end": iso(rel["updatedAt"]), "fraction": 1.0,
                        "phase": "released"}
            return {"status": "failed", "start": start, "end": t_now + rel_min * 60, "phase": "release failed",
                    "fraction": (t_now - start) / max(1.0, t_now - start + rel_min * 60)}
        left, over = run_left(r0, rel_min, t_now)
        end = t_now + left
        phase = "release queued" if rel.get("status") in queued else "release running"
    elif tagged:
        end, phase = t_now + rel_min * 60, "tagged"
    elif ci.get("status") != "completed":
        left, over = run_left(ci_t0, ci_min, t_now)
        end = t_now + left + hands + rel_min * 60
        phase = "CI queued" if ci.get("status") in queued else "CI running"
    elif ci.get("conclusion") == "success":
        end, phase = t_now + hands + rel_min * 60, "CI ✓ · tag next"
    else:
        return {"status": "failed", "start": start, "end": t_now + dur, "phase": "CI failed",
                "fraction": (t_now - start) / max(1.0, t_now - start + dur)}
    return {"status": "running", "start": start, "end": end, "phase": phase, "over": over,
            "fraction": (t_now - start) / max(1.0, end - start)}


def release_rows(f: Frame) -> tuple[list[Item], list[Prog]]:
    """The release's items in the order they are worked out, with their progress: the rest first, then
    the after_all items (each waits for the worst of the rest), in plan order. Worked out once a frame
    and kept: a planned release's row that runs after one of them reads it too (render/next_release.py)."""
    memo = f.memo.get("release")
    if memo is not None:
        return memo
    plan, labels, cal, rc = f.plan, f.labels, f.cal, f.cfg.release
    t_now = now()
    items = [it for it in plan.items if "after_all" not in it.flags]
    last = len(items)
    items += [it for it in plan.items if "after_all" in it.flags]
    index: dict = {}
    for i, it in enumerate(items[:last]):
        if it.key is not None:
            index.setdefault(it.key, i)

    def targets_of(i: int) -> list[int]:
        if i >= last:
            return list(range(last))
        return [index[fl[6:]] for fl in items[i].flags if fl.startswith("after:") and fl[6:] in index]

    def compute(i: int, wait) -> Prog:
        it = items[i]
        # Until the work it runs after is done, begun or not, it never finishes before that work.
        after = "" if wait is None else "all" if i >= last else short_name(items[wait[1]].key, plan.items)
        # Held now (the plan is read once: a hold with an end ends on the clock), or held until a time
        # that has passed with nothing of it run since: its work is still stopped as it was held.
        held = it.held(t_now)
        stopped = held or (it.hold_ended(t_now) and not item_active_since(plan, it, labels, it.paused_until))
        # The hold itself takes time: nothing of it runs before the hold ends. It is a wait beside the
        # one on the work it runs after (counted once along a chain of rows held to one time).
        p = item_progress(plan, it, labels, now=t_now, cal=cal, default_fix_share=rc.fix_share,
                          wait_before=0.0 if wait is None else wait[0], after=after, paused=stopped,
                          hold=hold_left(it, t_now))
        stages = item_stages(plan, it)
        if (i >= last and "owner_ok" in it.flags and p.current != "done"
                and not item_started(plan, it.key, labels)):
            p.current, p.waiting, p.owner = "your go-ahead", True, True
        elif wait is None and (k := owner_stage(stages, p)) is not None:
            # Its next stage is the owner's ("your ...", no agent), nothing of it runs and it waits on
            # nothing else: it waits on the owner, as a planned release's row does (it read "queued").
            # It keeps its time left: the release's finish counts it.
            p.current, p.waiting, p.owner = stages[k][0], True, True
        if held and p.current != "done":
            # Held by the owner: its stopped agent is not a failure and it is not queued. It keeps its
            # time left, so the release's finish and what runs after it still count it.
            p.current, p.failed, p.paused, p.owner = "paused", False, True, False
            p.resume = it.paused_until
        elif stopped and p.current != "done" and not (p.waiting and (p.waits or p.owner)):
            # The hold is over and nothing has resumed the work (a row that waits on other work, or on
            # the owner, reads that wait instead): neither paused, nor failed, nor queued. It keeps the
            # time left it had while held, as if it resumed now, so what runs after it counts it.
            p.current, p.failed, p.hold_ended = "hold ended", False, it.paused_until
        return p

    # A failed item's time is its re-run's (the release's finish counts it), so what runs after it waits that long.
    progs = in_wait_order(len(items), targets_of, compute, rerun=True)
    f.memo["release"] = (items, progs)
    return items, progs


def release_panel(f: Frame, width: int):
    """(the panel, when the release is out)."""
    plan, labels, cal, rc = f.plan, f.labels, f.cal, f.cfg.release
    fm_cfg = rc.final_merge
    t_now = now()
    items, progs = release_rows(f)
    rows: list[tuple[str, Prog]] = [(it.name, p) for it, p in zip(items, progs)]
    sizes: list[float] = [item_minutes(plan, it, cal) for it in items]
    # A release item always has a finish time (it waits only on other release items, which have one,
    # a failed one's counting its re-run).
    worst = max((p.remaining or 0.0 for _n, p in rows), default=0.0)
    gh_timing = f.store.get("gh_timing")
    run_min = float(rc.release_run_minutes)
    ci_min, rel_min = gh_timing if gh_timing else (rc.fallback_minutes - rc.hands_minutes - run_min, run_min)
    # The final merge: tracked by its labels once a workflow runs it.
    fm = stages_progress([(s, f"{s}:{fm_cfg.key}", m) for s, m in fm_cfg.stages], labels, now=t_now, cal=None,
                         default_fix_share=rc.fix_share)
    by_hand = fm.start is None and plan.final_merge_by_hand is not None
    if by_hand:
        # Done by hand (the plan says when): a hotfix released without a final-merge workflow.
        at = plan.final_merge_by_hand
        merge = {"status": "done", "start": at, "end": at, "fraction": 1.0}
    elif fm.current == "done":
        merge = {"status": "done", "start": fm.start or t_now, "end": fm.end or t_now, "fraction": 1.0}
    elif fm.start is not None:
        merge = {"status": "failed" if fm.failed else "running", "start": fm.start,
                 "end": t_now + (fm.remaining or 0.0), "fraction": fm.fraction}
    else:
        start = t_now + worst
        merge = {"status": "todo", "start": start, "end": start + fm_cfg.minutes * 60,
                 "dur": fm_cfg.minutes * 60, "fraction": 0.0}
    tag = tag_milestone(dict(f.store.get("release_gh") or {}), merge["end"], merge["status"] == "done", ci_min, rel_min,
                        rc.hands_minutes)
    release_at = tag["end"]
    released = tag["status"] == "done"

    t, bar_w = work_table("stages", width, plan, fm_cfg)
    live = [(n, p) for n, p in rows if p.current != "done"]
    for name, p in live:
        work_row(t, name, p, bar_w, release=True)
    done = len(rows) - len(live)
    if done:
        finished_row(t, done)
    t.add_row("", "", "", "", "")
    milestone_row(t, fm_cfg.name, C.TEXT,
                  Text("after all above", style=C.FAINT) if merge["status"] == "todo"
                  else Text("done by hand", style=C.GREEN) if by_hand else chips_of(fm.marks, True),
                  merge, bar_w, "done", C.MUTED)
    tag_stages = Text(rc.tag_todo_text, style=C.FAINT) if tag["status"] == "todo" else Text(
        tag["phase"], style=C.GREEN if released else C.RED_SOFT if tag["status"] == "failed" else C.ACCENT_SOFT)
    milestone_row(t, rc.tag_row.format(release=plan.release), f"bold {C.TEXT}", tag_stages, tag, bar_w, "out",
                  f"bold {C.TEXT}")

    # Share of the whole release behind us: each item's done share weighted by its size.
    sizes += [fm_cfg.minutes * 60, tag.get("dur") or (ci_min + rel_min + rc.hands_minutes) * 60]
    fractions = [p.fraction for _n, p in rows] + [merge["fraction"], tag["fraction"]]
    overall = 1.0 if released else sum(sz * fr for sz, fr in zip(sizes, fractions)) / max(1.0, sum(sizes))
    nfailed = sum(p.failed for _n, p in rows) + (merge["status"] == "failed") + (tag["status"] == "failed")
    nover = sum(p.over > 0 and not p.failed and not p.paused for _n, p in rows)
    npaused = sum(p.paused for _n, p in rows)
    nended = sum(p.hold_ended is not None for _n, p in rows)
    nowner = sum(p.owner for _n, p in rows)
    first = bar(overall, METER_W, C.GREEN if released else eta_colour(release_at - t_now))
    pct = 100 if released else min(99, int(overall * 100))  # never "100%" before it is out
    words = rc.words
    first.append(f" {pct}% {words['done_share']}", style=C.TEXT)
    phrases = [first, Text(f"{done}/{len(rows)} {words['ready']}", style=C.GREEN)]
    if nfailed:
        phrases.append(Text(f"{nfailed} failed", style=C.RED_SOFT))
    if nover:
        phrases.append(Text(f"{nover} overrun", style=C.AMBER))
    if npaused:
        phrases.append(Text(f"{npaused} paused", style=C.AMBER))
    if nended:
        phrases.append(Text(f"{nended} not resumed", style=C.AMBER))
    if nowner:
        phrases.append(Text(f"{nowner} wait on you", style=C.AMBER))  # as the planned releases' heads say it
    if released:
        phrases.append(Text.assemble((f"{words['released']} ", C.GREEN), (clock(release_at), f"bold {C.GREEN}")))
    else:
        # A run past its median makes the release's finish a lower bound, as the tag row reads it.
        at_least = "≥" if tag.get("over", 0.0) > 0 else ""
        phrases.append(Text.assemble((f"{words['out']} ", C.MUTED), (clock(release_at), f"bold {C.TEXT}"),
                                     (f" (in {at_least}{human(release_at - t_now)})", C.FAINT)))
    head = pack(phrases, width - 4, indent=METER_W + 1)
    return panel(Group(head, Text(""), t), rc.title.format(release=plan.release)), release_at

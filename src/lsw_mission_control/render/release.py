"""The release panel: its items, the final merge, and the tag + release run from GitHub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text

from lsw_mission_control.progress import (
    Prog,
    in_wait_order,
    item_minutes,
    item_progress,
    item_started,
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


def tag_milestone(g: dict, merge_end: float, merge_done: bool, ci_min: float, rel_min: float, hands_min: float) -> dict:
    """Tag + release run, from GitHub: CI on the remote main's HEAD (once the final merge is
    done), the release tag, and the release run for it. Not started: it starts when the merge
    is done."""
    t_now = now()
    hands = hands_min * 60
    dur = (ci_min + rel_min) * 60 + hands
    rel, ci, tagged = g.get("rel"), g.get("ci"), g.get("tag")
    start = max(merge_end, t_now)
    todo = {"status": "todo", "start": start, "end": start + dur, "dur": dur, "fraction": 0.0, "phase": ""}
    ci_t0 = iso(ci["createdAt"]) if ci else None
    ci_counts = ci is not None and bool(tagged or rel or (merge_done and ci_t0 >= merge_end - 300))
    if not (rel or tagged or ci_counts):
        return todo
    # Tagged with neither run listed yet: it began when the merge finished (not 'now', which slides).
    start = ci_t0 if ci_counts else iso(rel["createdAt"]) if rel else merge_end if merge_done else t_now
    queued = ("queued", "waiting", "pending", "requested")
    if rel:
        r0 = iso(rel["createdAt"])
        if rel.get("status") == "completed":
            if rel.get("conclusion") == "success":
                return {"status": "done", "start": start, "end": iso(rel["updatedAt"]), "fraction": 1.0,
                        "phase": "released"}
            return {"status": "failed", "start": start, "end": t_now + rel_min * 60, "phase": "release failed",
                    "fraction": (t_now - start) / max(1.0, t_now - start + rel_min * 60)}
        end = max(t_now + 60, r0 + rel_min * 60)
        phase = "release queued" if rel.get("status") in queued else "release running"
    elif tagged:
        end, phase = t_now + rel_min * 60, "tagged"
    elif ci.get("status") != "completed":
        end = max(t_now + 60, ci_t0 + ci_min * 60) + hands + rel_min * 60
        phase = "CI queued" if ci.get("status") in queued else "CI running"
    elif ci.get("conclusion") == "success":
        end, phase = t_now + hands + rel_min * 60, "CI ✓ · tag next"
    else:
        return {"status": "failed", "start": start, "end": t_now + dur, "phase": "CI failed",
                "fraction": (t_now - start) / max(1.0, t_now - start + dur)}
    return {"status": "running", "start": start, "end": end, "phase": phase,
            "fraction": (t_now - start) / max(1.0, end - start)}


def release_panel(f: Frame, width: int):
    """(the panel, when the release is out)."""
    plan, labels, cal, rc = f.plan, f.labels, f.cal, f.cfg.release
    fm_cfg = rc.final_merge
    t_now = now()
    # The rest first, then the after_all items (each waits for the worst of the rest), in plan order.
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
        p = item_progress(plan, it, labels, now=t_now, cal=cal, default_fix_share=rc.fix_share,
                          wait_before=0.0 if wait is None else wait[0], after=after)
        if i >= last and "owner_ok" in it.flags and not item_started(plan, it.key, labels):
            p.current, p.waiting = "your go-ahead", True
        return p

    # A failed item's time is its re-run's (the release's finish counts it), so what runs after it waits that long.
    progs = in_wait_order(len(items), targets_of, compute, rerun=True)
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
    if fm.current == "done":
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
                  Text("after all above", style=C.FAINT) if merge["status"] == "todo" else chips_of(fm.marks, True),
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
    nover = sum(p.over > 0 and not p.failed for _n, p in rows)
    first = bar(overall, METER_W, C.GREEN if released else eta_colour(release_at - t_now))
    pct = 100 if released else min(99, int(overall * 100))  # never "100%" before it is out
    words = rc.words
    first.append(f" {pct}% {words['done_share']}", style=C.TEXT)
    phrases = [first, Text(f"{done}/{len(rows)} {words['ready']}", style=C.GREEN)]
    if nfailed:
        phrases.append(Text(f"{nfailed} failed", style=C.RED_SOFT))
    if nover:
        phrases.append(Text(f"{nover} overrun", style=C.AMBER))
    if released:
        phrases.append(Text.assemble((f"{words['released']} ", C.GREEN), (clock(release_at), f"bold {C.GREEN}")))
    else:
        phrases.append(Text.assemble((f"{words['out']} ", C.MUTED), (clock(release_at), f"bold {C.TEXT}"),
                                     (f" (in {human(release_at - t_now)})", C.FAINT)))
    head = pack(phrases, width - 4, indent=METER_W + 1)
    return panel(Group(head, Text(""), t), rc.title.format(release=plan.release)), release_at

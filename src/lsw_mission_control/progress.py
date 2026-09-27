"""How far each piece of work has got, and how long it has left.

ETAs are estimates: each stage's planned length, CALIBRATED (release items only) against how
long finished release stages actually took, minus the time it has already run.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Callable, Sequence

from lsw_mission_control.agents import review_needs_fix
from lsw_mission_control.theme import C
from lsw_mission_control.util import iso

if TYPE_CHECKING:
    from lsw_mission_control.plan import Item, Plan


class Prog:
    """How far one piece of work has got."""

    def __init__(self, remaining: float | None, current: str, marks: list | None = None, failed: bool = False,
                 fraction: float = 0.0, over: float = 0.0, waiting: bool = False, start: float | None = None,
                 end: float | None = None, alert: bool = False) -> None:
        self.remaining = remaining  # seconds left; None = not known (a live job is not answering, or
        # it waits on work that has no finish time)
        self.current = current  # the running stage, "done", "queued", "after merge", "your go-ahead", …
        self.marks = marks or []  # one (symbol, colour) per stage
        self.failed = failed
        self.fraction = fraction  # share of the work behind it: the bar fills with this
        self.over = over  # seconds the running stage is past its estimate
        self.waiting = waiting  # nothing is running on it right now
        self.start = start  # when its first stage started
        self.end = end  # when its last stage finished, once done
        self.alert = alert  # stuck: its stage shows in red
        self.paused = False  # held by the owner ("paused": true on an other item)
        self.waits = False  # it runs after work that is not done: a tie for "next to finish" goes to that work


class Calibration:
    """Planned minutes of RELEASE items scaled by how long finished release stages actually took.

    One lives as long as the dashboard process and is updated in place every frame: a factor
    changes only once its kind has 3 samples, the fix share only once 3 reviews are done, and
    otherwise the last value stays (so re-running a stage never snaps the ETAs back to 1.0)."""

    KINDS = ("build", "review", "fix")

    def __init__(self, fix_share: float = 0.7) -> None:
        self.factors = {k: 1.0 for k in self.KINDS}
        self.fix_share = fix_share
        self.n = 0

    def get(self, stage: str, default: float = 1.0) -> float:
        """The factor for a stage NAMED build, review or fix (a before-stage of that name too)."""
        return self.factors.get(stage, default)

    def __repr__(self) -> str:
        return f"Calibration({self.factors}, fix_share={self.fix_share}, n={self.n})"


def calibrate(cal: Calibration, items: Sequence[Item], labels: dict) -> None:
    """Scale planned stage minutes by what finished stages really took.

    For each stage kind (build, review, fix): the sum of the ACTUAL durations of the release
    items' finished stages over the sum of their PLANNED minutes, clamped to 0.2-2.0 and used
    once there are 3 samples. The fix share is how many finished reviews led to a fix.
    """
    actual = {"build": 0.0, "review": 0.0, "fix": 0.0}
    planned = {"build": 0.0, "review": 0.0, "fix": 0.0}
    count = {"build": 0, "review": 0, "fix": 0}
    reviews = fixes = 0
    for it in items:
        key = it.key
        if not key:
            continue
        for kind, mins in (("build", it.build), ("review", it.review), ("fix", it.fix)):
            a = labels.get(f"{kind}:{key}")
            if a and a["status"] == "done" and a["t0"] and a["t1"]:
                actual[kind] += a["t1"] - a["t0"]
                planned[kind] += mins * 60
                count[kind] += 1
        rv = labels.get(f"review:{key}")
        if rv and rv["status"] == "done":
            reviews += 1
            fixes += f"fix:{key}" in labels
    for kind in actual:
        if count[kind] >= 3 and planned[kind] > 0:
            cal.factors[kind] = min(2.0, max(0.2, actual[kind] / planned[kind]))
    if reviews >= 3:
        cal.fix_share = fixes / reviews
    cal.n = sum(count.values())


_progress_cache: dict = {}


def progress_of(path: str) -> tuple[int, float | None, float | None]:
    """(units finished, first unit's time, last unit's time) from a detached job's progress file:
    one JSON line per finished unit, with "t" (epoch seconds) and optionally "status"."""
    try:
        st = os.stat(path)
    except OSError:
        return 0, None, None
    hit = _progress_cache.get(path)
    if hit and hit[0] == st.st_mtime:
        return hit[1]
    n, first, last = 0, None, None
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue  # valid JSON but not a unit's record ("x", 3, []): not a finished unit
                if e.get("status") not in (None, "ok", "done"):
                    continue
                n += 1
                t = e.get("t")
                if isinstance(t, (int, float)):
                    first = t if first is None else min(first, t)
                    last = t if last is None else max(last, t)
    except OSError:
        return 0, None, None
    _progress_cache[path] = (st.st_mtime, (n, first, last))
    return n, first, last


def eta_from_json(path: str | None, key: str, now: float) -> float | None:
    """An epoch-seconds finish time a detached job writes about itself, if fresh (< 30 min old)."""
    if not path:
        return None
    try:
        if now - os.stat(path).st_mtime > 1800:
            return None
        return iso(str(json.loads(open(path).read())[key]))
    except Exception:  # noqa: BLE001 — a missing or odd file only loses the better estimate
        return None


def stages_progress(stages: Sequence, labels: dict, *, now: float, cal: Calibration | None, default_fix_share: float,
                    wait_before: float | None = 0.0, after: str = "", done_before: float = 0.0,
                    paused: bool = False) -> Prog:
    """Progress through a sequence of stages. `cal` scales planned minutes by the release's
    calibration (release items only; None elsewhere); `done_before` is work already behind the
    first stage. `paused` (held by the owner): an unfinished stage is held, not running or
    failed, and its fill stops at its last activity.

    `wait_before`/`after`: what the row still waits for (wait_for()): the time left of the work it
    runs after, and that work's short name. The wait sits before the stages that have not begun:
    what has begun runs on beside it, so the row's time left is max(its begun stages', the wait)
    plus its stages not begun, never less than the wait. While nothing of its own runs, its stage
    reads "after <name>"; a wait of None (that work has no finish time) leaves it none either."""
    wait_unknown = wait_before is None
    wait = 0.0 if wait_before is None else wait_before
    remaining = 0.0  # the time left of its stages that have begun (running, failed, held, a job part-done)
    queued = 0.0  # the time of its stages not begun: they come after the wait
    progressed = done_before  # seconds of the item's estimated work already behind it
    current = ""
    marks = []
    failed = False
    over = 0.0
    prev_review = None
    prev_start = 0.0  # the latest start of any earlier stage
    starts, ends = [], []
    label_sets = [[] if isinstance(sp, dict) else sp if isinstance(sp, list) else ([sp] if sp else [])
                  for _n, sp, _p in stages]
    for i, (name, spec, planned) in enumerate(stages):
        minutes = planned * (cal.get(name, 1.0) if cal is not None else 1.0)
        later_started = any(x in labels for ls in label_sets[i + 1:] for x in ls)
        if isinstance(spec, dict):
            total = int(spec["total"])
            n, first, last = progress_of(spec["progress"])
            if n >= total:  # a detached job is done only when every unit is (stages can overlap)
                marks.append(("●", C.GREEN))
                if first and last:
                    starts.append(first)
                    ends.append(last)
                progressed += (last - first) if first and last and last > first else minutes * 60
                continue
            if n == 0 or first is None:
                queued += minutes * 60
                marks.append(("○", C.FAINT))
                continue
            elapsed = max(1.0, now - first)
            starts.append(first)
            projected = eta_from_json(spec.get("eta_json"), spec.get("eta_key", "projected_finish_utc"), now)
            # The job's own projection (e.g. a cost model over pages) beats a unit count when units
            # differ in size, as they do in a largest-first queue.
            remaining += max(0.0, projected - now) if projected else elapsed * (total - n) / n
            progressed += elapsed
            current = f"{name} {n}/{total}"
            marks.append(("◉", C.ACCENT))
            continue
        specs = label_sets[i]
        if not specs and later_started:
            marks.append(("●", C.GREEN))  # no agent of its own, and the work after it has begun
            continue
        found = [labels[x] for x in specs if x in labels]
        latest = max(((a["t0"] or 0) for a in found), default=0.0)
        if found and prev_start and latest < prev_start:
            found = []  # it ran before an earlier stage was re-run, so it must run again
        needs_fix = review_needs_fix(prev_review) if name == "fix" else None
        if name == "review":
            prev_review = found[0] if found else None
        if not found:
            if name == "fix" and needs_fix is False:
                marks.append(("–", C.GREEN))
                continue
            share = (cal.fix_share if cal is not None else default_fix_share) if (name == "fix" and needs_fix is None) else 1.0
            queued += minutes * 60 * share
            marks.append(("○", C.FAINT))
            continue
        prev_start = max(prev_start, latest)
        t0s = [a["t0"] for a in found if a["t0"]]
        t1s = [a["t1"] for a in found if a["t1"]]
        if t0s:
            starts.append(min(t0s))
        if len(found) == len(specs) and all(a["status"] == "done" for a in found):
            marks.append(("●", C.GREEN))
            progressed += max(0.0, max(t1s) - min(t0s)) if t0s and t1s else minutes * 60
            if t1s:
                ends.append(max(t1s))
            continue
        if paused:
            ran = max(0.0, max(t1s) - min(t0s)) if t0s and t1s else 0.0
            progressed += ran
            remaining += max(minutes * 60 - ran, 10 * 60)
            current = name
            marks.append(("◉", C.AMBER))
            continue
        if any(a["status"] == "failed" for a in found):
            failed = True
            marks.append(("✕", C.RED_SOFT))
            progressed += max(0.0, max(t1s) - min(t0s)) if t0s and t1s else 0.0  # the fill stays
            remaining += minutes * 60  # a re-run
            current = f"{name} failed"
            continue
        started = min((a["t0"] or now) for a in found)
        elapsed = now - started
        left = minutes * 60 - elapsed
        # Overrun: past the plan AND the calibrated estimate, so a stage within its plan is no alarm.
        over = max(over, elapsed - max(minutes, planned) * 60)
        if left < 10 * 60:
            # Past its estimate: the longer a stage has overrun, the longer it tends to go on.
            left = max(10 * 60, 0.25 * elapsed)
        remaining += left
        progressed += elapsed
        current = name
        marks.append(("◉", C.ACCENT))
    if marks and all(m[0] in ("●", "–") for m in marks):
        return Prog(0.0, "done", marks, fraction=1.0, start=min(starts) if starts else None,
                    end=max(ends) if ends else None)
    waiting = not current
    if waiting:
        current = f"after {after}" if after else "queued"
    remaining = max(remaining, wait) + queued
    fraction = progressed / (progressed + remaining) if (progressed + remaining) > 0 else 0.0
    p = Prog(None if wait_unknown else remaining, current, marks, failed, fraction, max(0.0, over), waiting,
             start=min(starts) if starts else None)
    p.waits = bool(after)
    return p


def stages_started(stages: Sequence, labels: dict) -> bool:
    """Any of the stages has begun: one of its agents is known, or its detached job has finished
    a unit."""
    for _n, spec, _m in stages:
        if isinstance(spec, dict):
            if progress_of(spec["progress"])[0]:
                return True
        elif any(x in labels for x in (spec if isinstance(spec, list) else [spec] if spec else [])):
            return True
    return False


def wait_for(targets: Sequence[tuple[object, Prog]], *, rerun: bool = False) -> tuple[float | None, object] | None:
    """What a row that runs after `targets` ((ref, progress) pairs) still waits for: None once
    every one is done; otherwise (time left, ref) of the unfinished one that finishes last. The
    time is None when an unfinished one has no finish time (stalled, paused, unknown) or has
    failed (its row reads "needs rerun", with no time): the row then has none either, and never
    reads as finishing before it. `rerun`: a failed one's time is its re-run's instead (the
    release panel, whose finish time counts the re-run)."""
    pending = [(ref, p) for ref, p in targets if p.current != "done"]
    if not pending:
        return None
    unknown = next((ref for ref, p in pending if p.remaining is None or (p.failed and not rerun)), None)
    if unknown is not None:
        return None, unknown
    ref, p = max(pending, key=lambda rp: rp[1].remaining)
    return p.remaining, ref


def in_wait_order(n: int, targets_of: Callable[[int], Sequence[int]],
                  compute: Callable[[int, tuple[float | None, int] | None], Prog], *,
                  rerun: bool = False) -> list[Prog]:
    """Rows 0..n-1, each computed after the rows it runs after (`targets_of(i)`: their indexes),
    so a row listed before its target still waits for it. `compute(i, wait)` gets wait_for() of
    its targets (`rerun` as there). A cycle (a typo in the plan) is broken where it closes; the
    rows still draw."""
    out: list = [None] * n
    path: set = set()

    def visit(i: int) -> Prog:
        if out[i] is None:
            path.add(i)
            targets = [(t, visit(t)) for t in targets_of(i) if 0 <= t < n and t not in path]
            path.discard(i)
            out[i] = compute(i, wait_for(targets, rerun=rerun))
        return out[i]

    for i in range(n):
        visit(i)
    return out


def item_progress(plan: Plan, item: Item, labels: dict, *, now: float, cal: Calibration, default_fix_share: float,
                  wait_before: float | None = 0.0, after: str = "") -> Prog:
    """A release item: its "before" stages (measure, design), then build → review → fix,
    labelled build:<key> etc."""
    key = item.key
    if key is None:
        return Prog(0.0, "done", [("●", C.GREEN)] * 3, fraction=1.0)
    return stages_progress(plan.before(key) + [
        ("build", f"build:{key}", item.build), ("review", f"review:{key}", item.review),
        ("fix", f"fix:{key}", item.fix)], labels, now=now, cal=cal, default_fix_share=default_fix_share,
        wait_before=wait_before, after=after)


def item_started(plan: Plan, key: str | None, labels: dict) -> bool:
    """Any of the item's stages has begun (a before-stage's agent, or its job's progress file)."""
    if not key:
        return False
    return stages_started(plan.before(key) + [(k, f"{k}:{key}", 0) for k in ("build", "review", "fix")], labels)


def item_minutes(plan: Plan, item: Item, cal: Calibration) -> float:
    key = item.key
    pre = sum(m for _n, _s, m in plan.before(key)) if key else 0.0
    f = cal.factors
    return (pre + item.build * f["build"] + item.review * f["review"] + item.fix * f["fix"]) * 60 if key else 0.0


def short_name(ref: str | None, items: Sequence[Item]) -> str:
    """A dependency as two words fit: 'integrate' (a key) or an item name → 'merge'."""
    if not ref:
        return ""
    name = next((it.name for it in items if it.key == ref), ref)
    words = name.split()
    return words[0].lower() if words else ""

"""How far each piece of work has got, and how long it has left.

ETAs are estimates: each stage's planned length, CALIBRATED (release items only) against how
long finished release stages actually took, minus the time it has already run.
"""

from __future__ import annotations

import json
import math
import os
from typing import TYPE_CHECKING, Callable, NamedTuple, Sequence

from lsw_mission_control.agents import died_after, review_needs_fix
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
        self.paused = False  # held by the owner ("paused": true on an other item or a release item)
        self.resume = None  # when a held release item resumes ("paused_until"), if the plan says
        self.hold_ended = None  # when a release item's hold ended, while nothing has resumed it
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
# The earliest start seen for each progress file while it is the same file (device, inode) and has
# not shrunk: path -> (st_dev, st_ino, size, start). Where the filesystem keeps no birth time a
# file's creation is its last write, which an appended unit moves forward; this keeps the start.
_job_starts: dict = {}

# A unit's "t" outside these years is not epoch seconds: a placeholder (0) or milliseconds.
_T_MIN, _T_MAX = 946_684_800.0, 4_102_444_800.0  # 2000-01-01, 2100-01-01 (UTC)

# Before its units give a pace, a job's time left is its planned minutes; the units' pace then
# takes over as they come in, weighed against the plan as if the plan were this share of the job's
# units (at least one). One unit of a long job, or the first of several run at once, is not a pace.
PACE_PRIOR = 0.1


class JobFile(NamedTuple):
    """A detached job's progress file, read: the job has begun once the file exists."""

    units: int  # finished units (lines whose status is none, "ok" or "done")
    first: float | None  # the first finished unit's time
    last: float | None  # the last finished unit's time
    start: float  # when the job began: the file's creation, or its earliest record if that is earlier


def _unit_time(v) -> float | None:
    """A record's "t" as epoch seconds; None when it is not a finite number (true, "x", NaN) or
    not a time in 2000-2100 (a placeholder 0, a negative number, milliseconds)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        t = float(v)
    except OverflowError:  # an integer too large for a float
        return None
    return t if math.isfinite(t) and _T_MIN <= t < _T_MAX else None


def created_at(st: os.stat_result) -> float:
    """When a file was made: its birth time where the filesystem keeps one (macOS), else its last
    modification (for a file nothing has been written to yet, that is when it was made)."""
    birth = getattr(st, "st_birthtime", None)
    return float(birth) if isinstance(birth, (int, float)) and birth > 0 else st.st_mtime


def job_file(path: str) -> JobFile | None:
    """A detached job's progress file (one JSON line per finished unit, with "t" (epoch seconds)
    and optionally "status"), or None while there is none: a job that has not started."""
    try:
        st = os.stat(path)
    except OSError:
        _job_starts.pop(path, None)  # gone: a file made there again is a new job
        return None
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _progress_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    n, first, last = 0, None, None
    start = created_at(st)
    seen = _job_starts.get(path)
    if seen and seen[:2] == (st.st_dev, st.st_ino) and st.st_size >= seen[2]:
        start = min(start, seen[3])  # the same file, only grown: its start never moves forward
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue  # valid JSON but not a unit's record ("x", 3, []): not a finished unit
                t = _unit_time(e.get("t"))
                if t is not None:
                    start = min(start, t)  # a failed unit's record says the job was running too
                if e.get("status") not in (None, "ok", "done"):
                    continue
                n += 1
                if t is not None:
                    first = t if first is None else min(first, t)
                    last = t if last is None else max(last, t)
    except OSError:
        return hit[1] if hit else JobFile(0, None, None, start)  # it exists: begun, units unknown for now
    _job_starts[path] = (st.st_dev, st.st_ino, st.st_size, start)
    job = JobFile(n, first, last, start)
    _progress_cache[path] = (key, job)
    return job


def progress_of(path: str) -> tuple[int, float | None, float | None]:
    """(units finished, first unit's time, last unit's time) from a detached job's progress file
    (job_file()); (0, None, None) while there is none."""
    job = job_file(path)
    return (0, None, None) if job is None else (job.units, job.first, job.last)


def stage_begun(spec, labels: dict) -> bool:
    """A stage has begun: one of its agents is known, or its detached job's progress file exists."""
    if isinstance(spec, dict):
        return job_file(spec["progress"]) is not None
    return any(x in labels for x in (spec if isinstance(spec, list) else [spec] if spec else []))


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


def _stage_attempt(label: dict, paused: bool, prev_start: float) -> dict:
    """The attempt a stage reads of one of its labels: the one latest_by_label chose, unless that one
    stands in for a later attempt that died (died_after) and no longer stands itself. In a held item
    the attempt that died is read: the owner holds the run that was doing it again, so the stage is
    held, never done. And a result older than an earlier stage's latest start reads the attempt that
    died when that one began at or after it: the stage ran again after that re-run and died, so it
    needs a re-run, where the stale result alone would leave it queued."""
    died = died_after(label)
    if died is not None and (paused or (label["status"] == "done"
                                        and (label["t0"] or 0) < prev_start <= (died["t0"] or 0))):
        return died
    return label


def stages_progress(stages: Sequence, labels: dict, *, now: float, cal: Calibration | None, default_fix_share: float,
                    wait_before: float | None = 0.0, after: str = "", done_before: float = 0.0,
                    paused: bool = False, hold: float = 0.0) -> Prog:
    """Progress through a sequence of stages. `cal` scales planned minutes by the release's
    calibration (release items only; None elsewhere); `done_before` is work already behind the
    first stage. `paused` (held by the owner): an unfinished stage is held, not running or
    failed, and its fill stops at its last activity.

    `wait_before`/`after`: what the row still waits for (wait_for()): the time left of the work it
    runs after, and that work's short name. The wait sits before the stages that have not begun:
    what has begun runs on beside it, so the row's time left is max(its begun stages', the wait)
    plus its stages not begun, never less than the wait. While nothing of its own runs, its stage
    reads "after <name>"; a wait of None (that work has no finish time) leaves it none either.

    `hold`: seconds until a hold with an end is over, nothing of the row running before then. Its
    begun stages resume after the hold, beside the wait, and its stages not begun come after both:
    max(the hold + its begun stages', the wait) plus its stages not begun. The hold is a wait like
    any other, so a chain of rows held to one time counts it once: a row after a held one waits for
    that one's time left, which already holds the hold, and its own hold runs beside that wait. The
    share done (the bar) is the work's alone: a hold is no work."""
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
    begun = [stage_begun(sp, labels) for _n, sp, _p in stages]
    for i, (name, spec, planned) in enumerate(stages):
        minutes = planned * (cal.get(name, 1.0) if cal is not None else 1.0)
        later_started = any(begun[i + 1:])
        if isinstance(spec, dict):
            total = int(spec["total"])
            job = job_file(spec["progress"])
            if job is None and later_started:
                # No progress file, and the work after it has begun: behind it, as a stage with no
                # agent is (a finished job's folder cleaned up once its results were used).
                marks.append(("●", C.GREEN))
                continue
            if job is None:  # no progress file, nothing after it begun: the job has not started
                queued += minutes * 60
                marks.append(("○", C.FAINT))
                continue
            n, start = job.units, job.start
            starts.append(start)
            prev_start = max(prev_start, start)  # a later stage that ran before the job began runs again
            if n >= total:  # a detached job is done only when every unit is (stages can overlap)
                marks.append(("●", C.GREEN))
                if job.last is not None:
                    ends.append(job.last)
                progressed += (job.last - start) if job.last is not None and job.last > start else minutes * 60
                continue
            # Running from the moment its file exists, even with no unit finished yet.
            elapsed = max(0.0, now - start)
            projected = eta_from_json(spec.get("eta_json"), spec.get("eta_key", "projected_finish_utc"), now)
            # Its planned minutes, as an agent's stage has: past them, at least 10 min, or a quarter
            # of its time so far.
            planned_left = minutes * 60 - elapsed
            if planned_left < 10 * 60:
                planned_left = max(10 * 60, 0.25 * elapsed)
            if projected:
                # The job's own projection (e.g. a cost model over pages) beats a unit count when
                # units differ in size, as they do in a largest-first queue.
                left = max(0.0, projected - now)
            elif n:
                # The units' pace since the job began, weighed against the plan as if the plan had
                # done PACE_PRIOR of the units: the pace takes over as units come in, but one unit
                # of a long job (the largest first, several at once) does not multiply its time left.
                pace = max(1.0, elapsed) * (total - n) / n
                k = max(1.0, PACE_PRIOR * total)
                left = (k * planned_left + n * pace) / (k + n)
            else:
                left = planned_left  # no unit has finished to give a pace
                over = max(over, elapsed - max(minutes, planned) * 60)
            remaining += left
            progressed += elapsed
            current = f"{name} {n}/{total}"
            marks.append(("◉", C.ACCENT))
            continue
        specs = label_sets[i]
        if not specs and later_started:
            marks.append(("●", C.GREEN))  # no agent of its own, and the work after it has begun
            continue
        found = [_stage_attempt(labels[x], paused, prev_start) for x in specs if x in labels]
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
        # A label reads failed only once every attempt of it died (latest_by_label). The stage needs a
        # re-run only while none of its labels runs: a run working on it runs the died ones again.
        if any(a["status"] == "failed" for a in found) and not any(a["status"] == "running" for a in found):
            failed = True
            marks.append(("✕", C.RED_SOFT))
            progressed += max(0.0, max(t1s) - min(t0s)) if t0s and t1s else 0.0  # the fill stays
            remaining += minutes * 60  # a re-run
            current = f"{name} failed"
            continue
        # Running: timed from this round of it, its labels at work or returned with no attempt of them
        # dying since. A died attempt, and a result a later attempt of its label died after (a resumed
        # or relaunched run started it again), are an earlier round's: their starts are no overrun.
        this_round = [a for a in found if a["status"] == "running" or (a["status"] == "done" and died_after(a) is None)]
        started = min((a["t0"] or now) for a in (this_round or [a for a in found if a["status"] != "failed"]))
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
    work = max(remaining, wait) + queued  # the share done is the work's: the hold is not counted in it
    fraction = progressed / (progressed + work) if (progressed + work) > 0 else 0.0
    remaining = max(remaining + max(0.0, hold), wait) + queued
    p = Prog(None if wait_unknown else remaining, current, marks, failed, fraction, max(0.0, over), waiting,
             start=min(starts) if starts else None)
    p.waits = bool(after)
    return p


def stages_started(stages: Sequence, labels: dict) -> bool:
    """Any of the stages has begun: one of its agents is known, or its detached job's progress
    file exists."""
    return any(stage_begun(spec, labels) for _n, spec, _m in stages)


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


def item_stages(plan: Plan, item: Item) -> list:
    """A release item's stages: its "before" stages (measure, design), then build → review → fix,
    labelled build:<key> etc. ([] for an item with no key: it is done)."""
    key = item.key
    if key is None:
        return []
    return plan.before(key) + [("build", f"build:{key}", item.build), ("review", f"review:{key}", item.review),
                               ("fix", f"fix:{key}", item.fix)]


def item_progress(plan: Plan, item: Item, labels: dict, *, now: float, cal: Calibration, default_fix_share: float,
                  wait_before: float | None = 0.0, after: str = "", paused: bool | None = None,
                  hold: float = 0.0) -> Prog:
    """A release item: its "before" stages (measure, design), then build → review → fix,
    labelled build:<key> etc. `paused`: its stages are held (stages_progress()); by default while
    the owner holds it (Item.held). `hold`: seconds until its hold ends (stages_progress())."""
    if item.key is None:
        return Prog(0.0, "done", [("●", C.GREEN)] * 3, fraction=1.0)
    return stages_progress(item_stages(plan, item), labels, now=now, cal=cal, default_fix_share=default_fix_share,
                           wait_before=wait_before, after=after, paused=item.held(now) if paused is None else paused,
                           hold=hold)


def hold_left(item, at: float) -> float:
    """Seconds until a held item's hold ends: 0 for a hold with no end, or one not holding it at `at`.
    `item` is anything with held() and paused_until (a release item, a planned one)."""
    return max(0.0, item.paused_until - at) if item.held(at) and item.paused_until is not None else 0.0


def item_active_since(plan: Plan, item: Item, labels: dict, since: float) -> bool:
    """Any of the item's work has run since `since`, or runs now: an agent of it was active at or
    after that time, or has not gone silent (a long tool call writes nothing), or a detached job of
    it is running or finished a unit since. An attempt that died counts as the run it was: where a
    label reads the result before it (latest_by_label), the one that died rides along (died_after)."""
    for _name, spec, _m in item_stages(plan, item):
        if isinstance(spec, dict):
            job = job_file(spec["progress"])
            if job is not None and (job.units < int(spec["total"]) or (job.last or 0.0) >= since):
                return True
            continue
        for label in spec if isinstance(spec, list) else [spec] if spec else []:
            a = labels.get(label)
            if a and (a["status"] == "running" or max(a["t0"] or 0.0, a["t1"] or 0.0) >= since):
                return True
            died = died_after(a) if a else None
            if died is not None and max(died.get("t0") or 0.0, died.get("t1") or 0.0) >= since:
                return True
    return False


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

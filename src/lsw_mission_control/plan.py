"""The plan: what the dashboard tracks (docs/plan-schema.md). It is re-read whenever the file
changes, with no restart; a plan that will not parse is ignored (the title says why) and the
last good one stays."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence

from lsw_mission_control.util import iso

if TYPE_CHECKING:
    from lsw_mission_control.plugin import Plugin

# (name, spec, minutes). spec: an agent label, a list of labels, None (no agent), or
# {"progress": path, "total": n, "eta_json"?: path, "eta_key"?: str} for a detached job.
Stage = tuple


@dataclass(frozen=True)
class Item:
    """A release item: its `before` stages, then build -> review -> fix, labelled <kind>:<key>."""

    name: str
    key: str | None
    build: int
    review: int
    fix: int
    flags: tuple[str, ...] = ()
    before: tuple[Stage, ...] = ()
    paused: bool = False  # the plan holds it ("paused", or "paused_until"): held() says whether it still does
    paused_until: float | None = None  # when the hold ends (epoch s): its time left and what waits on it count it

    def held(self, at: float) -> bool:
        """Held by the owner at `at`: its unfinished stages read "paused", never failed or queued. A
        hold with an end holds until then only, whatever "paused" says beside it: the plan is read
        once, and the hold ends on the clock."""
        return self.paused and (self.paused_until is None or at < self.paused_until)

    def hold_ended(self, at: float) -> bool:
        """Its `paused_until` has passed."""
        return self.paused_until is not None and at >= self.paused_until


@dataclass(frozen=True)
class OtherItem:
    """Work in progress that no release waits for."""

    name: str
    stages: tuple[Stage, ...]
    paused: bool = False
    after: object = None  # another other-item's name
    after_server: bool = False  # the first stage mirrors a plugin's live job


@dataclass(frozen=True)
class NextItem:
    name: str
    key: object
    group: str
    stages: tuple[Stage, ...]
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class NextRelease:
    """A release after this one: the next (`next`), or one after that (`later`)."""

    release: str
    about: str
    items: tuple[NextItem, ...]


@dataclass(frozen=True)
class Plan:
    release: str
    items: tuple[Item, ...] = ()
    other: tuple[OtherItem, ...] = ()
    next: NextRelease | None = None
    plugin_data: Mapping[str, object] = field(default_factory=dict)
    pre: Mapping[str, tuple] = field(default_factory=dict)  # item key -> its stages before the build
    # When this release's final merge was done by hand (a hotfix released without a final-merge
    # workflow): `final_merge_by_hand` = {"release": ..., "at": ISO time}. Only the named release's.
    final_merge_by_hand: float | None = None
    later: tuple[NextRelease, ...] = ()  # the releases after `next`, in order

    def before(self, key: str | None) -> list:
        """An item's stages before its build ([] for none)."""
        return list(self.pre.get(key, ())) if key is not None else []

    def release_before(self, i: int) -> str:
        """The release `later[i]` comes after: the one before it in `later`, else the next release
        (this one, in a plan with no next release)."""
        if i > 0:
            return self.later[i - 1].release
        return self.next.release if self.next is not None else self.release


EMPTY_PLAN = Plan("?")


# A plan is typed by hand. These bounds refuse what can only be a typo, and what would otherwise
# load and then fail every frame (1e308 minutes overflow the clock; NaN compares false forever).
MAX_MINUTES = 1_000_000  # about two years
MAX_UNITS = 1_000_000  # a detached job's total


def _number(value, what: str, whole: bool = False) -> float:
    """A planned number of minutes: a number (or a numeric string, as always) from 0 to MAX_MINUTES."""
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number, not {value!r}")
    try:
        m = int(value) if whole else float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{what} must be a number, not {value!r}"[:120]) from None
    if not 0 <= m <= MAX_MINUTES:  # NaN and infinity fail this too
        raise ValueError(f"{what} must be from 0 to {MAX_MINUTES:,} minutes, not {value!r}"[:120])
    return m


def parse_stage(st: list, owner: str) -> Stage:
    """[name, spec, minutes]. spec: an agent label, a list of labels, null (no agent: it counts as
    behind once a later stage has started), or {"progress": path, "total": n} for a detached job
    that appends one JSON line per finished unit to `path` (it has begun once `path` exists)."""
    if not isinstance(st, list) or len(st) != 3:
        raise ValueError(f"a stage of {owner!r} must be [name, label, minutes], not {st!r}"[:120])
    name, spec, minutes = st[0], st[1], st[2]
    ok = (spec is None or isinstance(spec, str)
          or (isinstance(spec, list) and all(isinstance(x, str) for x in spec))
          or (isinstance(spec, dict) and isinstance(spec.get("progress"), str) and int(spec.get("total", 0)) > 0
              and (spec.get("eta_json") is None or isinstance(spec.get("eta_json"), str))))
    if not ok:
        raise ValueError(f"stage {name!r} of {owner!r}: label must be text, a list of text, null, "
                         "or {progress, total}")
    if isinstance(spec, dict):
        if int(spec["total"]) > MAX_UNITS:
            raise ValueError(f"stage {name!r} of {owner!r}: total must be at most {MAX_UNITS:,}")
        if not isinstance(spec.get("eta_key", ""), str):
            raise ValueError(f"stage {name!r} of {owner!r}: eta_key must be text")
    return (str(name), spec, _number(minutes, f"stage {name!r} of {owner!r}"))


def _list(value, what: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list")
    return value


def _obj(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    return value


def parse_plan(d: dict, plugins: Sequence[Plugin] = ()) -> Plan:
    """The whole plan, or an exception naming the first thing wrong with it. Unknown keys (a
    `note`, say) are ignored."""
    _obj(d, "the plan")
    items = []
    pre: dict = {}
    for it in _list(d["items"], "items"):
        it = _obj(it, "each of items")
        key = it.get("key")
        name = str(it["name"])
        fields_ = (name, None if key is None else str(key),
                   *(int(_number(it.get(k, 0), f"{k} of {name!r}", whole=True)) for k in ("build", "review", "fix")),
                   tuple(str(f) for f in _list(it.get("flags", []), f"flags of {name!r}")))
        before = ()
        if key is not None and it.get("before"):
            before = tuple(parse_stage(st, it["name"]) for st in _list(it["before"], f"before of {name!r}"))
            pre[str(key)] = before
        until = it.get("paused_until")
        if until is not None:
            try:
                until = iso(str(until))
            except ValueError:
                raise ValueError(f"paused_until of {name!r} must be an ISO time with its offset") from None
        items.append(Item(*fields_, before, bool(it.get("paused")) or until is not None, until))
    other = []
    for o in _list(d.get("other", []), "other"):
        o = _obj(o, "each of other")
        name = str(o["name"])
        stages = tuple(parse_stage(st, o["name"]) for st in _list(o["stages"], f"stages of {name!r}"))
        after = o.get("after")
        if after is not None and not isinstance(after, str):
            raise ValueError(f"after of {name!r} must be the name of another other item")
        after_server = bool(o.get("after_server") or o.get("after_live"))
        if after_server and not stages:
            raise ValueError(f"{name!r} waits on a live job: its first stage is the job's, so it needs one")
        other.append(OtherItem(name, stages, bool(o.get("paused")), after, after_server))
    nxt = _planned_release(d["next"], "next") if d.get("next") else None
    # Absent or null: none. Anything else but a list ({}, "", 0, false) is a typo the dashboard must not
    # read as "no later releases" in silence.
    later_raw = [] if d.get("later") is None else _list(d["later"], "later")
    later = tuple(_planned_release(r, f"later[{i}]") for i, r in enumerate(later_raw))
    release = str(d["release"])
    by_hand = None
    if d.get("final_merge_by_hand") is not None:
        fm = _obj(d["final_merge_by_hand"], "final_merge_by_hand")
        if "release" not in fm or "at" not in fm:
            raise ValueError('final_merge_by_hand needs "release" and "at" (an ISO time)')
        try:
            at = iso(str(fm["at"]))
        except (TypeError, ValueError):
            raise ValueError(f"final_merge_by_hand's at is not a time: {fm['at']!r}"[:120]) from None
        # A mark left over from the release before is ignored, never carried into this one.
        by_hand = at if str(fm["release"]) == release else None
    plugin_data = {p.name: p.parse_plan(d) for p in plugins}
    return Plan(release, tuple(items), tuple(other), nxt, plugin_data, pre, by_hand, later)


def _planned_release(n, where: str) -> NextRelease:
    """`next`, or one of `later`: {release, about, items: [{key, name, group, before, build, review,
    fix, flags}]}. Planned, not scheduled: build/review/fix become stages only above 0 minutes."""
    n = _obj(n, where)
    nitems = []
    for it in _list(n.get("items", []), f"{where}.items"):
        it = _obj(it, f"each of {where}.items")
        name = str(it["name"])
        key = it.get("key")
        if (key is not None and not isinstance(key, (str, int))) or isinstance(key, bool):
            raise ValueError(f"key of {name!r} must be text")
        key = None if key is None else str(key)
        stages = [parse_stage(st, it["name"]) for st in _list(it.get("before", []), f"before of {name!r}")]
        if key:
            mins = {k: int(_number(it.get(k, 0), f"{k} of {name!r}", whole=True)) for k in ("build", "review", "fix")}
            stages += [parse_stage([k, f"{k}:{key}", mins[k]], it["name"]) for k in ("build", "review", "fix")
                       if mins[k] > 0]
        nitems.append(NextItem(name, key, str(it.get("group", "")), tuple(stages),
                               tuple(str(f) for f in _list(it.get("flags", []), f"flags of {name!r}"))))
    return NextRelease(str(n["release"]), str(n.get("about", "")), tuple(nitems))


class PlanLoader:
    """The last good plan, re-read when the file's mtime changes."""

    def __init__(self, path: Path, plugins: Sequence[Plugin] = ()) -> None:
        self.path = path
        self.plugins = list(plugins)
        self.plan: Plan = EMPTY_PLAN
        self.note = ""
        self._mtime: float | None = None

    @property
    def loaded(self) -> bool:
        """A plan has parsed in this process (until then the view shows an empty stand-in)."""
        return self.plan is not EMPTY_PLAN

    def refresh(self) -> Plan:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self.note = f"{self.path.name} is missing"
            self._mtime = None  # read it again when it comes back, even with its old mtime
            return self.plan
        if mtime == self._mtime:
            return self.plan
        self._mtime = mtime
        try:
            plan = parse_plan(json.loads(self.path.read_text()), self.plugins)
        except Exception as e:  # noqa: BLE001 — a bad edit must never take the view down
            self.note = f"{self.path.name} not loaded ({type(e).__name__}: {e})"[:160]
            return self.plan
        self.plan = plan
        self.note = ""
        return plan

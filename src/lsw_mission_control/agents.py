"""Workflow agents, from the journals and transcripts Claude Code writes under ~/.claude/projects.

An agent is a dict (the finished store saves them as JSON): id, label, phase, status
(running | done | failed), result, run (the wf_* folder), t0/t1 (first and last transcript
timestamps), action (its latest tool call) and logs (the scratchpad logs its commands write).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, Collection

from lsw_mission_control import util
from lsw_mission_control.util import iso, write_json

if TYPE_CHECKING:
    from lsw_mission_control.config import FinalMergeCfg
    from lsw_mission_control.plan import Plan

# transcript path -> ((size, mtime), its facts): one entry per transcript, not one per change of it
# (a dashboard runs for days), and scan_agents drops the transcripts it no longer sees.
_ts_cache: dict = {}
# A log belongs to the agent whose shell command WRITES it (> x.log, >> x.log, | tee x.log).
_LOG_WRITE = re.compile(r"(?:>>?|\btee\s+(?:-a\s+)?)\s*[^\s;|&<>]*?([\w.-]+)\.log\b")
TAIL_BYTES = 200_000


def transcript_facts(path: Path) -> tuple[float | None, float | None, str, frozenset]:
    """First and last timestamps of an agent transcript, its latest tool call, and the
    scratchpad logs its recent tool calls mention (so a test log can be named after its agent)."""
    try:
        st = path.stat()
    except OSError:
        return None, None, "", frozenset()
    key = (st.st_size, st.st_mtime)
    hit = _ts_cache.get(str(path))
    if hit is not None and hit[0] == key:
        return hit[1]
    t0 = t1 = None
    action = ""
    logs: set = set()
    try:
        with open(path, "rb") as f:
            first = f.readline()
            f.seek(max(0, st.st_size - TAIL_BYTES))
            tail = f.read().splitlines()
        try:
            t0 = iso(json.loads(first)["timestamp"])
        except Exception:
            pass
        for raw in tail:
            if b".log" not in raw or b'"tool_use"' not in raw:
                continue
            try:
                content = (json.loads(raw).get("message") or {}).get("content")
            except Exception:
                continue
            for item in content if isinstance(content, list) else []:
                cmd = (item.get("input") or {}).get("command") if isinstance(item, dict) else None
                if item.get("type") == "tool_use" and isinstance(cmd, str):
                    logs.update(_LOG_WRITE.findall(cmd))
        for raw in reversed(tail):
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            if t1 is None and rec.get("timestamp"):
                t1 = iso(rec["timestamp"])
            content = (rec.get("message") or {}).get("content")
            if not action and isinstance(content, list):
                for item in reversed(content):
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        inp = item.get("input") or {}
                        what = inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                        action = f"{item.get('name')} · {str(what).splitlines()[0] if what else ''}"
                        break
            if t1 and action:
                break
    except OSError:
        pass
    facts = (t0, t1, action, frozenset(logs))
    _ts_cache[str(path)] = (key, facts)
    return facts


def scan_agents(projects_dir: Path, now: float, window_s: float = 48 * 3600) -> list[dict]:
    """Every workflow agent of the scan window (two days by default), with its status and timing."""
    agents = []
    cutoff = now - window_s
    read: set = set()
    for run_dir in projects_dir.glob("*/subagents/workflows/wf_*"):
        journal = run_dir / "journal.jsonl"
        try:
            if run_dir.stat().st_mtime < cutoff or not journal.exists():
                continue
            lines = journal.read_text(errors="replace").splitlines()
        except OSError:
            continue
        by_key: dict = {}
        for line in lines:
            try:
                e = json.loads(line)
            except Exception:
                continue
            kind = e.get("type")
            if kind == "started":
                by_key[e.get("key")] = {"id": str(e.get("agentId")), "label": str(e.get("label") or "?"),
                                         "phase": str(e.get("phase") or ""),
                                         "status": "running", "result": None, "run": run_dir.name}
            elif kind == "result" and e.get("key") in by_key:
                by_key[e["key"]]["status"] = "done"
                by_key[e["key"]]["result"] = e.get("result")
            elif kind in ("failed", "error") and e.get("key") in by_key:
                by_key[e["key"]]["status"] = "failed"
        for a in by_key.values():
            transcript = run_dir / f"agent-{a['id']}.jsonl"
            read.add(str(transcript))
            t0, t1, action, logs = transcript_facts(transcript)
            a.update(t0=t0, t1=t1, action=action, logs=logs)
            agents.append(a)
    for gone in [k for k in _ts_cache if k not in read and k.startswith(str(projects_dir))]:
        del _ts_cache[gone]  # out of the window (or deleted): its facts are not needed again
    return agents


def _spec_labels(spec) -> list:
    return spec if isinstance(spec, list) else [spec] if isinstance(spec, str) else []


def final_merge_labels(fm: FinalMergeCfg) -> frozenset[str]:
    """The final merge's agent labels. Its work belongs to one release only: every release has a
    final merge of its own under the same labels."""
    return frozenset(f"{kind}:{fm.key}" for kind, _m in fm.stages)


def label_names(plan: Plan, fm: FinalMergeCfg) -> dict[str, str]:
    """Agent label (or "wf_<run>/label") -> the release item or other work it belongs to."""
    names: dict = {}
    for it in plan.items:
        if it.key:
            for kind in ("build", "review", "fix"):
                names[f"{kind}:{it.key}"] = it.name
            for _stage, spec, _m in plan.before(it.key):
                for label in _spec_labels(spec):
                    names[label] = it.name
    for kind, _m in fm.stages:
        names[f"{kind}:{fm.key}"] = fm.name
    for item in plan.other:
        for _stage, spec, _m in item.stages:
            for label in _spec_labels(spec):
                names[label] = item.name
    for item in (plan.next.items if plan.next else ()):
        for _stage, spec, _m in item.stages:
            for label in _spec_labels(spec):
                names[label] = item.name
    return names


def findings_of(agent: dict | None) -> list[dict]:
    """A review's findings, whatever shape its result has (an agent's result is free-form)."""
    result = agent.get("result") if isinstance(agent, dict) else None
    found = result.get("findings") if isinstance(result, dict) else None
    return [f for f in found if isinstance(f, dict)] if isinstance(found, list) else []


def stored_ok(rec) -> bool:
    """A finished-store record the dashboard can use; anything else (a hand edit, an older
    shape) is dropped rather than crashing the view at every refresh."""
    return (isinstance(rec, dict) and all(isinstance(rec.get(k), str) for k in ("id", "label", "run"))
            and rec.get("status") in ("done", "failed", "running")
            and all(rec.get(k) is None or (isinstance(rec.get(k), (int, float)) and not isinstance(rec.get(k), bool))
                    for k in ("t0", "t1")))


def store_since(store, now: float | None = None) -> float:
    """When the store's release began (epoch seconds): 0 for a store written before this was kept,
    or with a value no clock holds (not a number, not above 0, or later than now, such as
    milliseconds typed by hand), so everything in it counts, as it always did. A later one would
    otherwise hide this release's own final merge with nothing on screen to say why."""
    since = store.get("since", 0) if isinstance(store, dict) else 0
    ok = isinstance(since, (int, float)) and not isinstance(since, bool) and math.isfinite(since)
    return float(since) if ok and 0 < since <= (util.now() if now is None else now) else 0.0


class FinishedStore:
    """Every tracked stage seen during this release, running ones included (`<cache>/finished.json`:
    {"release", "since", "agents", "previous"}; `since` is when the dashboard first saw that
    release, and `previous` is {"release", "since", "agents"}: the release it left and the
    records the change dropped, given back if the release moves back to it).

    Journals leave the scan window long before a release ships; without this, finished
    items would fall back to 'queued' two days after they were built, and a paused run's
    re-run stage would vanish and bring back the stage it replaced. `readonly` never writes
    (the reload gate's self-check renders with one).
    """

    def __init__(self, path: Path, readonly: bool = False) -> None:
        self.path = path
        self.readonly = readonly

    def merge(self, agents: list[dict], plan: Plan, names: dict[str, str], loaded: bool = True,
              release_bound: Collection[str] = ()) -> list[dict]:
        """The scan plus the stored stages. `loaded` is False while no plan has loaded (a broken or
        missing plan at start-up): the stand-in plan's release "?" is not a new release, so the
        store is only read, never pruned or written (a prune there once wiped a release's record).

        `release_bound`: labels whose work belongs to one release only (the final merge's). A new
        release drops their records and starts `since` now (a release moved straight back to gets
        its own back instead); an agent under one of them that started
        before `since` is the shipped release's: never stored again, and returned marked
        `earlier_release`, so Agents at work still shows it but no row of this release counts it
        (latest_by_label skips it). One the journals still hold would otherwise read as this
        release's final merge, done, and the tag row would take main's CI for the tag's."""
        release = plan.release
        bound = frozenset(release_bound)
        try:
            store = json.loads(self.path.read_text())
        except (OSError, ValueError):
            store = None
        changed = False
        if not isinstance(store, dict) or not isinstance(store.get("agents"), dict):
            store = {"release": release, "agents": {}}
        elif store.get("release") != release and loaded:
            # A new release: keep only the stages of work still on the plan (what was the next
            # release's is now this one's), and drop the shipped release's, its final merge included.
            # What it drops is set aside with the release it left: moved straight back (a typo, a
            # move undone), that release gets its records and its `since` again, where a fresh
            # `since` would lose its final merge for good.
            # Saved at once: `since` must be when the release changed, not when a stage next moved.
            still = {label.split("/", 1)[-1] for label in names} - bound
            kept = {k: v for k, v in store["agents"].items() if isinstance(v, dict) and v.get("label") in still}
            left = {"release": store.get("release"), "since": store_since(store),
                    "agents": {k: v for k, v in store["agents"].items() if k not in kept and stored_ok(v)}}
            prev = store.get("previous")
            if isinstance(prev, dict) and prev.get("release") == release and isinstance(prev.get("agents"), dict):
                since, kept = store_since(prev), {**prev["agents"], **kept}
            else:
                since = util.now()
            store = {"release": release, "since": since, "agents": kept, "previous": left}
            changed = True
        since = store_since(store)

        def earlier(a: dict) -> bool:
            return a.get("label") in bound and (a.get("t0") or 0) < since

        saved = {k: v for k, v in store["agents"].items() if isinstance(k, str) and stored_ok(v) and not earlier(v)}
        changed = changed or len(saved) != len(store["agents"])
        store["agents"] = saved
        tracked = {label.split("/", 1)[-1] for label in names}
        seen = set()
        out = []
        for a in agents:
            if earlier(a):
                out.append(dict(a, earlier_release=True))
                continue
            out.append(a)
            if a["label"] not in tracked:
                continue
            k = f"{a['run']}/{a['id']}"
            seen.add(k)
            rec = {"id": a["id"], "label": a["label"], "phase": str(a["phase"] or ""), "status": a["status"],
                   "run": a["run"], "t0": a["t0"], "t1": a["t1"],
                   "result": {"findings": [{"severity": f.get("severity")} for f in findings_of(a)]}}
            old = saved.get(k)
            # A running agent's last-seen time moves every refresh: rewrite for it only every 10 min.
            if old is None or {**old, "t1": 0} != {**rec, "t1": 0} or abs((old["t1"] or 0) - (rec["t1"] or 0)) > 600:
                saved[k] = rec
                changed = True
        if changed and loaded and not self.readonly:
            write_json(self.path, store)
        for k, rec in saved.items():
            if k not in seen and rec["label"] in tracked:
                out.append(dict(rec, phase=str(rec.get("phase") or ""), action="", logs=frozenset()))
        return out


def latest_by_label(agents: list[dict], now: float, silent_stopped_s: float) -> dict[str, dict]:
    """The newest agent per label and per run-qualified label. A stopped workflow writes nothing
    to its journal, so an agent silent for longer than `silent_stopped_s` reads as failed. An
    agent of an earlier release (FinishedStore.merge marks it) is no row's."""
    out: dict = {}
    for a in agents:
        if a.get("earlier_release"):
            continue
        if a["status"] == "running" and (not a["t1"] or now - a["t1"] > silent_stopped_s):
            a = dict(a, status="failed")
        for key in (a["label"], f"{a['run']}/{a['label']}"):
            cur = out.get(key)
            if cur is None or (a["t0"] or 0) > (cur["t0"] or 0):
                out[key] = a
    return out


def review_needs_fix(review: dict | None) -> bool | None:
    if not review or review["status"] != "done":
        return None
    return any(f.get("severity") in ("blocker", "major", "minor") for f in findings_of(review))

"""Tokens used, counted from every Claude Code session log on this machine."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from lsw_mission_control.sources import Source
from lsw_mission_control.store import Store
from lsw_mission_control.util import iso, local_now, now, write_json

BUCKET = 600  # token counts are kept in 10-minute buckets
KEEP_S = 8 * 86400


def model_family(model) -> str:
    """'claude-opus-4-1' -> 'Opus': the family name the owner reads; '<synthetic>' and the rest -> 'other'."""
    m = str(model or "").lower()
    for fam in ("fable", "opus", "sonnet", "haiku"):
        if fam in m:
            return fam.capitalize()
    return "other"


class TokenCounter(Source):
    """Count tokens from every Claude Code session log on this machine, incrementally.

    Each reply is logged several times (once per content block) with the same usage, so a
    message id counts once. Per file: the byte offset already read, the last ids seen, and
    10-minute buckets of [input, output, cache writes, cache reads]; cached to disk (account-wide,
    <usage dir>/tokens.json) so a restart does not re-read hundreds of MB. Undocumented format:
    counts are a guide.
    """

    name = "tokens"
    interval_s = 30

    def __init__(self, projects_root: Path, cache_file: Path, store: Store) -> None:
        self.root, self.cache_file, self.store = projects_root, cache_file, store
        self.files: dict | None = None

    def _load(self) -> dict:
        try:
            files = json.loads(self.cache_file.read_text()).get("files", {})
        except (OSError, ValueError, AttributeError):
            files = {}
        # A malformed cache entry is re-read from the start rather than stopping the counts for good.
        # "m" (per-model output buckets) came later: an entry without it is re-read from the start, once.
        return {k: v for k, v in (files.items() if isinstance(files, dict) else [])
                if isinstance(v, dict) and isinstance(v.get("off"), int) and isinstance(v.get("last"), list)
                and isinstance(v.get("b"), dict) and isinstance(v.get("m"), dict)}

    def poll_once(self) -> None:
        if self.files is None:
            self.files = self._load()
        files = self.files
        cutoff = now() - KEEP_S
        changed = False
        seen = set()
        try:
            paths = list(self.root.rglob("*.jsonl"))
        except OSError:
            paths = []
        for path in paths:
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                continue
            key = str(path)
            seen.add(key)
            ent = files.get(key) or {"off": 0, "last": [], "b": {}, "m": {}}
            if st.st_size < ent["off"]:
                ent = {"off": 0, "last": [], "b": {}, "m": {}}
            if st.st_size == ent["off"]:
                files[key] = ent
                continue
            try:
                with open(path, "rb") as f:
                    f.seek(ent["off"])
                    chunk = f.read()
            except OSError:
                continue
            end = chunk.rfind(b"\n") + 1
            if end <= 0:
                continue
            for raw in chunk[:end].splitlines():
                if b'"usage"' not in raw or b'"assistant"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                    msg = rec.get("message") or {}
                    u = msg.get("usage") or {}
                    mid = msg.get("id") or rec.get("requestId")
                    if not u or rec.get("type") != "assistant" or mid in ent["last"]:
                        continue
                    ent["last"] = (ent["last"] + [mid])[-50:]
                    b = str(int(iso(rec["timestamp"]) // BUCKET * BUCKET))
                    row = ent["b"].setdefault(b, [0, 0, 0, 0])
                    row[0] += int(u.get("input_tokens") or 0)
                    row[1] += int(u.get("output_tokens") or 0)
                    row[2] += int(u.get("cache_creation_input_tokens") or 0)
                    row[3] += int(u.get("cache_read_input_tokens") or 0)
                    fam = model_family(msg.get("model"))
                    mrow = ent["m"].setdefault(b, {})
                    mrow[fam] = mrow.get(fam, 0) + int(u.get("output_tokens") or 0)
                except Exception:
                    continue
            ent["off"] += end
            files[key] = ent
            changed = True
        for key in [k for k in files if k not in seen]:
            del files[key]
        for ent in files.values():
            ent["b"] = {b: v for b, v in ent["b"].items() if int(b) >= cutoff}
            ent["m"] = {b: v for b, v in ent["m"].items() if int(b) >= cutoff}
        t_now = now()
        midnight = dt.datetime.combine(local_now().date(), dt.time()).timestamp()
        windows = (("5h", t_now - 5 * 3600), ("today", midnight), ("7d", t_now - 7 * 86400))
        totals = {name: [0, 0, 0, 0] for name in ("5h", "today", "7d")}
        for ent in files.values():
            for b, v in ent["b"].items():
                t = int(b)
                for name, since in windows:
                    if t + BUCKET > since:
                        totals[name] = [x + y for x, y in zip(totals[name], v)]
        by_model: dict = {}
        for ent in files.values():
            for b, fams in ent["m"].items():
                t = int(b)
                for fam, out in fams.items():
                    row = by_model.setdefault(fam, {"5h": 0, "today": 0, "7d": 0})
                    for name, since in windows:
                        if t + BUCKET > since:
                            row[name] += out
        self.store.update(tokens=totals, tokens_by_model=by_model)
        if changed:
            write_json(self.cache_file, {"files": files})  # a per-process temp file: safe with several windows open

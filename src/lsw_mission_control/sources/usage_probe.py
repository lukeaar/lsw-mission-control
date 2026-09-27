"""The plan limits, asked of the Claude Code CLI itself.

The status line only runs in the terminal CLI, so plan data goes stale whenever the owner works
in an editor or the desktop app. A non-interactive run emits a `rate_limit_event` (both windows,
account-wide) about 2 s in, before its model turn, and is stopped right there. It runs every
20 min while a dashboard is open, and skips a round when the plan data is younger than 15 min
(a terminal session, or another dashboard, just wrote it). Its cwd is an empty folder, so no
project files or CLAUDE.md load into it. A probe that comes back with nothing (no event, no CLI,
the timeout) is tried again 3 min later, then 6, 12, capped at the interval: waiting the whole
interval after a miss let the plan data age to twice it. A CLI that goes silent is killed at the
timeout by a watchdog, since reading its output would otherwise wait for a line that never comes.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from lsw_mission_control.sources import Source
from lsw_mission_control.util import now, write_json


def probe_usage(usage_dir: Path, model: str = "haiku", timeout_s: float = 60) -> dict | None:
    """One probe: the CLI's rate_limit_event as the status line's record shape, or None."""
    cwd = usage_dir / "probe-cwd"
    try:
        cwd.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ["claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose",
             "--setting-sources", "", "ok"],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    info = None
    deadline = now() + timeout_s
    watchdog = threading.Timer(timeout_s, proc.kill)  # a silent CLI ends the read below with EOF
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                e = {}
            if e.get("type") == "rate_limit_event":
                info = e.get("rate_limit_info") or {}
                break
            if now() > deadline:
                break
    finally:
        watchdog.cancel()
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 — a probe that will not die must not stop the dashboard
            pass
    wins = (info or {}).get("unifiedWindows") or {}
    limits = {}
    for key in ("five_hour", "seven_day"):
        w = wins.get(key)
        if isinstance(w, dict) and w.get("utilization") is not None:
            limits[key] = {"used_percentage": round(float(w["utilization"]) * 100, 1),
                           "resets_at": w.get("resetsAt")}
    if not limits:
        return None
    return {"at": now(), "rate_limits": limits, "source": "probe",
            "status": info.get("status"), "overage": bool(info.get("isUsingOverage"))}


RETRY_S = 3 * 60  # the first retry after a probe that came back with nothing; it doubles up to the interval


class UsageProbe(Source):
    name = "usage-probe"

    def __init__(self, usage_dir: Path, every_s: float = 20 * 60, fresh_s: float = 15 * 60, model: str = "haiku") -> None:
        self.usage_dir, self.every_s, self.fresh_s, self.model = usage_dir, every_s, fresh_s, model
        self.usage_file = usage_dir / "usage.json"
        self._sleep = every_s
        self._misses = 0  # probes in a row that came back with nothing

    def poll_once(self) -> None:
        try:
            age = now() - self.usage_file.stat().st_mtime
        except OSError:
            age = float("inf")
        if age < self.fresh_s:
            self._misses = 0
            self._sleep = max(60.0, self.every_s - age)
            return
        rec = probe_usage(self.usage_dir, self.model)
        if rec:
            write_json(self.usage_file, rec)
            self._misses = 0
            self._sleep = self.every_s
        else:
            self._misses += 1
            self._sleep = min(self.every_s, RETRY_S * 2 ** (self._misses - 1))

    def next_sleep(self) -> float:
        return self._sleep

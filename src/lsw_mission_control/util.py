"""Small helpers every part of the engine uses: the clock, time wording, processes, JSON files."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

_clock: Callable[[], float] | None = None


def now() -> float:
    """The time the dashboard reads. Tests and parity runs freeze it (set_clock, or LSW_MC_NOW)."""
    if _clock is not None:
        return _clock()
    frozen = os.environ.get("LSW_MC_NOW")
    if frozen:
        try:
            return float(frozen)
        except ValueError:
            pass
    return time.time()


def set_clock(fn: Callable[[], float] | None) -> None:
    global _clock
    _clock = fn


def local_now() -> dt.datetime:
    return dt.datetime.fromtimestamp(now())


def iso(ts: str) -> float:
    # Docker writes nanoseconds (".717472605Z"); Python 3.9's parser takes at most microseconds.
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts.replace("Z", "+00:00"))
    return dt.datetime.fromisoformat(ts).timestamp()


def human(seconds: float) -> str:
    try:
        seconds = max(0, int(seconds))
    except (OverflowError, ValueError):  # an infinite or NaN estimate: say so, never fail the frame
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    m = seconds // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}"
    d, h = divmod(h, 24)
    return f"{d}d{h:02d}h"


def ago(ts: float | None) -> str:
    return "—" if not ts else human(now() - ts) + " ago"


def clock(ts: float) -> str:
    """'HH:MM', with the weekday in front when it is not today ('Sat 00:46'); '?' for a time no
    calendar holds (an estimate that ran away)."""
    try:
        t = dt.datetime.fromtimestamp(ts)
    except (OverflowError, ValueError, OSError):
        return "?"
    today = local_now().date()
    day = "" if t.date() == today else t.strftime("%a ")
    return day + t.strftime("%H:%M")


def run(*cmd: str, timeout: int = 30, input_: str | None = None, cwd: Path | str | None = None) -> str:
    """A command's stdout, or "" when it could not run or timed out (callers keep their last answer)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input_, cwd=cwd).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def num(x) -> float:
    """A number from a cache or hook file, 0 when it is anything else."""
    try:
        return float(x) if not isinstance(x, bool) else 0.0
    except (TypeError, ValueError):
        return 0.0


def write_json(path: Path, data) -> None:
    """Atomic: a per-process temp file, then a rename, so several dashboards can share a cache."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        pass


def fit(width: int, *options: str) -> str:
    """The first wording that fits (the last one otherwise)."""
    return next((o for o in options if len(o) <= width), options[-1])


def count(n: float) -> str:
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}".replace(".0" + unit, unit)
    return f"{int(n)}"

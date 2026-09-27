"""Live reload: a running dashboard restarts itself into edited code, but only once the edit
compiles AND passes the self-check (`--check-net --self-check`, exit 0). A broken edit is not
loaded: the title says why, the running code stays, and only the next edit is tried again.

It restarts THROUGH the launcher path, so replacing the launcher (with a newer launcher, or with
an older single-file dashboard) reloads a running window into whatever that file now holds.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, Sequence

from lsw_mission_control.app import SCROLL_ENV

SETTLE_S = 2.0  # act only once the newest edit is this old: never in the middle of a multi-file save
GATE_TIMEOUT_S = 30
CONTRACT = "network cases correct"


def user_args(argv: Sequence[str]) -> list[str]:
    """The flags the owner gave: argv without `--launcher <path>` (the launcher adds it again)."""
    out, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a == "--launcher":
            skip = True
            continue
        if a.startswith("--launcher="):
            continue
        out.append(a)
    return out


def _mtimes(paths: Sequence[Path]) -> dict[Path, float | None]:
    out = {}
    for p in paths:
        try:
            out[p] = p.stat().st_mtime
        except OSError:
            out[p] = None
    return out


class Reloader:
    def __init__(self, watched: Sequence[Path], launcher: Path | None, argv: Sequence[str],
                 package_dir: Path | None = None, legacy_scroll_env: str | None = None,
                 rescan=None) -> None:
        self.launcher = launcher
        self.argv = list(argv)
        self.package_dir = package_dir
        self.legacy_scroll_env = legacy_scroll_env
        self.rescan = rescan  # returns the current watch list (new engine files count as edits)
        files = list(watched) + ([launcher] if launcher else [])
        self.born = _mtimes(files)
        self.note = ""  # shown in the title until a reload succeeds

    def _current(self) -> dict[Path, float | None]:
        files = list(self.rescan()) if self.rescan else list(self.born)
        if self.launcher and self.launcher not in files:
            files.append(self.launcher)
        return _mtimes(files)

    def changed(self) -> tuple[dict[Path, float | None], list[Path]]:
        now_m = self._current()
        keys = set(now_m) | set(self.born)
        return now_m, sorted(p for p in keys if now_m.get(p) != self.born.get(p))

    def label(self, path: Path) -> str:
        if self.launcher and path == self.launcher:
            return path.name
        if self.package_dir:
            try:
                return f"{self.package_dir.name}/{path.relative_to(self.package_dir)}"
            except ValueError:
                pass
        return path.name

    def poll(self, clock=time.time) -> Literal["none", "wait", "rejected", "go"]:
        now_m, changed = self.changed()
        if not changed:
            return "none"
        newest = max((now_m.get(p) or 0.0) for p in changed)
        if clock() - newest < SETTLE_S:
            return "wait"
        problem, culprit = self.gate(changed)
        if problem:
            self.note = f"edit to {self.label(culprit)} not loaded: {problem}"
            self.born = now_m  # only the NEXT edit is tried again
            return "rejected"
        return "go"

    def gate(self, changed: Sequence[Path]) -> tuple[str, Path]:
        """('' when the edit may load, else a short reason; the file to name)."""
        existing = [p for p in changed if p.suffix == ".py" and p.exists()]
        for p in existing:
            try:
                compile(p.read_text(), str(p), "exec")
            except SyntaxError as e:
                return f"SyntaxError, line {e.lineno}", p
            except (OSError, ValueError) as e:
                return type(e).__name__, p
        newest = max(changed, key=lambda p: (p.stat().st_mtime if p.exists() else 0.0))
        cmd = self.gate_command()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=GATE_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            return "its self-check did not finish", newest
        if CONTRACT in r.stdout and r.returncode == 0:
            return "", newest
        err = r.stderr.strip().splitlines()
        if err:
            return err[-1][:90], newest
        bad = [ln for ln in r.stdout.splitlines() if "MISMATCH" in ln]
        return (bad[0][:90] if bad else "its self-check failed" if CONTRACT in r.stdout else "it did not start"), newest

    def gate_command(self) -> list[str]:
        if self.launcher:
            return [sys.executable, str(self.launcher), "--check-net", "--self-check"]
        return [sys.executable, "-m", "lsw_mission_control", *user_args(self.argv), "--check-net", "--self-check"]

    def exec_argv(self) -> list[str]:
        if self.launcher:
            return [sys.executable, str(self.launcher), *user_args(self.argv)]
        return [sys.executable, "-m", "lsw_mission_control", *self.argv]

    def exec(self, scroll_offset: int):
        os.environ[SCROLL_ENV] = str(scroll_offset)
        if self.legacy_scroll_env:
            os.environ[self.legacy_scroll_env] = str(scroll_offset)
        argv = self.exec_argv()
        os.execv(argv[0], argv)

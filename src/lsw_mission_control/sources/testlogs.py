"""Test suites: recent logs in the session scratchpads, each with its last summary line."""

from __future__ import annotations

import glob as _glob
import re
from pathlib import Path

from lsw_mission_control.theme import C


def _split_glob(pattern: str) -> tuple[Path, str]:
    """('/abs/dir', 'rest/*/of/it.log'): the longest leading part with no wildcard."""
    parts = Path(pattern).parts
    for i, part in enumerate(parts):
        if _glob.has_magic(part):
            return Path(*parts[:i]) if i else Path("."), str(Path(*parts[i:]))
    return Path(pattern).parent, Path(pattern).name


def test_logs(globs, now: float, max_age_s: float = 2 * 3600, limit: int = 4) -> list[tuple[str, str, float]]:
    """[(stem, summary, mtime)] of the newest logs with a pytest/exit summary or a [NN%] line."""
    rows = []
    for pattern in globs:
        base, rest = _split_glob(pattern)
        for log in base.glob(rest):
            try:
                st = log.stat()
                if now - st.st_mtime > max_age_s:
                    continue
                with open(log, "rb") as f:
                    f.seek(max(0, st.st_size - 600))
                    tail = f.read().decode(errors="replace")
            except OSError:
                continue
            pct = re.findall(r"\[\s*(\d+)%\]", tail)
            summary = re.findall(r"(\d+ passed[^\n]*|\d+ failed[^\n]*|\d+ errors?\b[^\n]*|exit=\S+|EXIT=\S+)", tail)
            label = summary[-1].rstrip(" =") if summary else (f"{pct[-1]}%" if pct else "")
            if label:
                rows.append((log.stem, label, st.st_mtime))
    return sorted(rows, key=lambda r: -r[2])[:limit]


test_logs.__test__ = False  # not a pytest test


def suite_colour(label: str) -> str:
    """A test summary's colour: any failure, error (pytest's setup and collection failures) or
    non-zero exit (a kill is exit=-9) is red, whatever else passed."""
    if re.search(r"\b[1-9]\d* (?:failed|errors?)\b|exit=(?:[1-9]|-\d)", label, re.I):
        return C.RED_SOFT
    if re.search(r"exit=0\b", label, re.I) or re.search(r"\b\d+ passed", label):
        return C.GREEN
    return C.MUTED

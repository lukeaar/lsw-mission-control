"""This repository may be made public: no adopter's names, hosts, paths, accounts or secrets,
in the tree or in its history. The adopter terms to look for are NOT listed here (that list would
itself leak them): they come from LSW_MC_DENYLIST (comma-separated) or the untracked file
~/.config/lsw-mission-control/denylist (one term per line). Generic patterns are always checked."""

from __future__ import annotations

import os
import pwd
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)  # the tests run under a throwaway HOME
GENERIC = [
    re.compile(r"/Users/(?!x/)[A-Za-z][\w.-]*/"),  # a real macOS home path (the placeholder user is x)
    re.compile(r"/home/(?!x/)[a-z][\w.-]*/"),
    re.compile(r"[\w.+-]+@(?:gmail|googlemail|outlook|hotmail|icloud|me)\.com"),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"),
]


def denylist() -> list[str]:
    env = os.environ.get("LSW_MC_DENYLIST", "")
    terms = [t.strip() for t in env.split(",") if t.strip()]
    f = REAL_HOME / ".config" / "lsw-mission-control" / "denylist"
    if f.exists():
        terms += [t.strip() for t in f.read_text().splitlines() if t.strip() and not t.startswith("#")]
    return terms


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [ROOT / line for line in out.splitlines() if line and (ROOT / line).is_file()]


def scan(text: str, where: str, terms: list[str]) -> list[str]:
    bad = []
    low = text.lower()
    for term in terms:
        if term.lower() in low:
            bad.append(f"{where}: an adopter term ({len(term)} chars, starting {term[:2]!r})")
    for pat in GENERIC:
        m = pat.search(text)
        if m:
            bad.append(f"{where}: {pat.pattern} matches {m.group()[:12]!r}…")
    return bad


def test_the_tree_is_clean():
    terms = denylist()
    bad = []
    for path in tracked_files():
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        bad += scan(text, str(path.relative_to(ROOT)), terms)
        bad += scan(str(path.relative_to(ROOT)), "a file name", terms)
    assert not bad, "\n".join(bad)


def test_the_history_is_clean():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "-C", str(ROOT), "log", "-p", "--all", "--format=%B"], capture_output=True, text=True,
                         errors="replace").stdout
    bad = scan(out, "git history (patches and messages)", denylist())
    assert not bad, "\n".join(bad)


def test_a_denylist_is_configured_here():
    """Not a failure where no denylist exists (a public clone), but say so."""
    if not denylist():
        pytest.skip("no adopter denylist (LSW_MC_DENYLIST or ~/.config/lsw-mission-control/denylist): generic checks only")

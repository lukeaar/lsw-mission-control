from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lsw_mission_control  # noqa: E402
from lsw_mission_control import testing, theme  # noqa: E402

# Tests must import THIS tree (a worktree's), never whatever the venv's editable install points at.
assert Path(lsw_mission_control.__file__).resolve().is_relative_to(ROOT / "src"), lsw_mission_control.__file__

NOW = 1_790_000_000.0  # Mon 21 Sep 2026 14:13:20 UTC: every test runs at this frozen time


def pytest_addoption(parser):
    parser.addoption("--update-golden", action="store_true", help="rewrite tests/golden/* from the current code")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """UTC, a frozen clock, a throwaway HOME and caches: no test reads or writes the owner's."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LSW_MC_USAGE_DIR", str(tmp_path / "usage"))
    monkeypatch.delenv("LSW_MC_CACHE_DIR", raising=False)
    monkeypatch.delenv("LSW_MC_NOW", raising=False)
    monkeypatch.delenv("LSW_MC_SCROLL", raising=False)
    testing.freeze(NOW)
    theme.use(theme.LSW_DARK)
    yield
    testing.freeze(None)
    theme.use(theme.LSW_DARK)
    os.environ["TZ"] = "UTC"
    time.tzset()


@pytest.fixture
def update_golden(request) -> bool:
    return request.config.getoption("--update-golden")

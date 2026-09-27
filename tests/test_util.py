from __future__ import annotations

import json

import pytest
from rich.text import Text

from lsw_mission_control import testing, util
from lsw_mission_control.render.widgets import pack

from conftest import NOW


@pytest.mark.parametrize("seconds, text", [
    (-5, "0s"), (0, "0s"), (59, "59s"), (60, "1m"), (59 * 60 + 59, "59m"), (3600, "1h00"), (3660, "1h01"),
    (23 * 3600 + 59 * 60, "23h59"), (86400, "1d00h"), (90000, "1d01h"), (3 * 86400 + 7200, "3d02h"),
])
def test_human(seconds, text):
    assert util.human(seconds) == text


def test_human_and_clock_never_fail_on_a_runaway_estimate():
    assert util.human(float("inf")) == "?" and util.human(float("nan")) == "?"
    assert util.clock(1e308) == "?" and util.clock(float("nan")) == "?"


def test_iso_trims_nanoseconds_and_reads_z():
    assert util.iso("2026-09-21T14:13:20Z") == NOW
    assert util.iso("2026-09-21T14:13:20.717472605Z") == pytest.approx(NOW + 0.717472)
    assert util.iso("2026-09-21T16:13:20+02:00") == NOW


def test_clock_today_and_another_day():
    assert util.clock(NOW) == "14:13"
    assert util.clock(NOW + 10 * 3600) == "Tue 00:13"
    assert util.clock(NOW - 86400) == "Sun 14:13"


def test_ago_uses_the_frozen_clock():
    assert util.ago(NOW - 90) == "1m ago"
    assert util.ago(None) == "—" and util.ago(0) == "—"


def test_now_can_be_frozen_by_env(monkeypatch):
    testing.freeze(None)
    monkeypatch.setenv("LSW_MC_NOW", "123.5")
    assert util.now() == 123.5
    monkeypatch.setenv("LSW_MC_NOW", "garbage")
    assert util.now() > 1e9


def test_fit_takes_the_first_that_fits():
    assert util.fit(5, "longer", "short", "s") == "short"
    assert util.fit(1, "longer", "short") == "short"


@pytest.mark.parametrize("n, text", [(0, "0"), (999, "999"), (1000, "1k"), (1500, "1.5k"), (1_000_000, "1M"),
                                     (1_500_000, "1.5M"), (2_000_000_000, "2B"), (2_345_000_000, "2.3B")])
def test_count(n, text):
    assert util.count(n) == text


def test_num_never_raises():
    assert util.num("4.5") == 4.5 and util.num(None) == 0 and util.num(True) == 0 and util.num([1]) == 0


def test_write_json_is_atomic_and_quiet(tmp_path):
    p = tmp_path / "a" / "b.json"
    util.write_json(p, {"x": 1})
    assert json.loads(p.read_text()) == {"x": 1}
    assert not list(p.parent.glob("*.tmp"))
    (tmp_path / "ro").mkdir(mode=0o500)
    util.write_json(tmp_path / "ro" / "c.json", {})  # an unwritable dir: no exception


def test_run_returns_empty_on_failure():
    assert util.run("definitely-not-a-command-xyz") == ""
    assert util.run("sh", "-c", "echo hi") == "hi\n"
    assert util.run("sh", "-c", "sleep 5", timeout=1) == ""


def test_pack_wraps_only_between_phrases():
    phrases = [Text("a" * 10), Text("b" * 10), Text("c" * 10)]
    assert pack(phrases, 40).plain == "aaaaaaaaaa  ·  bbbbbbbbbb  ·  cccccccccc"
    assert pack(phrases, 25).plain == "aaaaaaaaaa  ·  bbbbbbbbbb\ncccccccccc"
    assert pack(phrases, 12, indent=4).plain == "aaaaaaaaaa\n    bbbbbbbbbb\n    cccccccccc"

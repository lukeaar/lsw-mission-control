from __future__ import annotations

import pytest

from lsw_mission_control.net import (
    FLAG_CASES,
    NET_CASES,
    NET_CHIP,
    NetRules,
    check_net_cases,
    etime_seconds,
    fit_list,
    flag_verdict,
    is_dashboard,
    net_label,
    network_critical,
    network_flag,
)


@pytest.mark.parametrize("command, label", NET_CASES)
def test_net_cases(command, label):
    assert net_label(command) == label


@pytest.mark.parametrize("crit, conn, room, want", FLAG_CASES)
def test_flag_cases(crit, conn, room, want):
    assert flag_verdict(crit, room, conn) == want


def test_check_net_prints_the_contract():
    lines = []
    assert check_net_cases(out=lines.append) == 0
    assert lines == [f"{len(NET_CASES)}/{len(NET_CASES)} network cases correct",
                     f"{len(FLAG_CASES)}/{len(FLAG_CASES)} indicator cases correct",
                     "39/39 connection cases correct"]
    assert len(NET_CASES) == 28 and len(FLAG_CASES) == 22


def test_check_net_reports_a_mismatch():
    lines = []
    assert check_net_cases(extra_cases=[("hg push", "hg push")], out=lines.append) == 1
    assert lines[0] == "MISMATCH want='hg push' got=None: hg push"
    assert lines[1] == "28/29 network cases correct"


def test_project_extras():
    rules = NetRules.with_extras(["mytool"], {"hg": ["push", "pull"], "git": ["archive"]})
    assert net_label("mytool --sync", rules) == "mytool" and net_label("hg push", rules) == "hg push"
    assert net_label("git archive --remote=x", rules) == "git archive" and net_label("git push", rules) == "git push"
    assert net_label("hg status", rules) is None and net_label("mytool") is None


@pytest.mark.parametrize("etime, seconds", [("05", 5), ("01:05", 65), ("1:02:03", 3723), ("2-03:04:05", 2 * 86400 + 11045)])
def test_etime(etime, seconds):
    assert etime_seconds(etime) == seconds


@pytest.mark.parametrize("cmd, yes", [
    ("/usr/bin/python3 /Users/x/p/.claude/status.py", True),
    (".../MacOS/Python /Users/x/p/.claude/status.py --no-server", True),
    ("/Users/x/.venvs/lsw-mission-control/bin/python -P -m lsw_mission_control --launcher /x/.claude/status.py", True),
    ("/opt/homebrew/bin/python3.14 -m lsw_mission_control --project /x", True),
    ("/Users/x/.venvs/v/bin/python3.14 /Users/x/.venvs/v/bin/lsw-mc --project /x", True),
    ("lsw-mc --once", True),
    ("vim notes.txt", False),
    ("python3 -m pytest lsw_mission_control", False),
    ("grep lsw_mission_control file", False),
])
def test_is_dashboard(cmd, yes):
    assert is_dashboard(cmd) is yes


def test_is_dashboard_config_markers():
    assert is_dashboard("node my-dash.js", ["my-dash"]) and not is_dashboard("node my-dash.js")


PS = """\
    1     0  3-00:00:00 /sbin/launchd
  100     1       30:00 /usr/bin/python3 /Users/x/p/.claude/status.py
  101   100       01:00 ssh -o ControlMaster=no hostname bash -s
  102   100       00:40 gh api repos/example/project/commits/main
  103   100       00:30 /x/Python -m pip install -q -e /x/engine
  200     1       20:00 /x/.venvs/lsw-mission-control/bin/python -P -m lsw_mission_control --launcher /y/.claude/status.py
  201   200       00:30 gh run list
  202   200       00:30 ssh -o BatchMode=yes otherhost bash -s
  300     1       00:20 git push origin main
  301     1       00:05 curl https://example.org
  302     1   bad-etime wget https://example.org
  303     1       00:09 git status
"""


def test_network_critical_excludes_only_dashboards_own_polling():
    got = network_critical(own_labels={"gh", "ssh → hostname"}, ps_text=PS)
    # the old dashboard's ssh and gh and the new one's gh are its own polling; a dashboard's pip
    # install and an ssh no plugin declared are not; a 5 s process is too young to count
    assert got == ["pip install (30s)", "ssh → otherhost (30s)", "git push (20s)", "wget (11d13h)"]


def test_network_critical_runs_ps_when_not_given():
    calls = []

    def fake_run(*cmd, timeout=30, input_=None):
        calls.append(cmd)
        return "  5  1  10:00 git fetch\n"
    assert network_critical(run=fake_run) == ["git fetch (10m)"]
    assert calls == [("ps", "-axo", "pid=,ppid=,etime=,command=")]


def test_a_failed_ps_is_never_safe():
    """ps that could not run or timed out (run() answers "") used to parse to [] and read "safe"."""
    assert network_critical(run=lambda *cmd, **kw: "") is None
    assert network_critical(run=lambda *cmd, **kw: "ps: some error\n") is None
    table = "  5  1  10:00 git fetch\n  77  1  00:30 /usr/bin/python3 /x/.claude/status.py\n"
    assert network_critical(run=lambda *cmd, **kw: table, self_pid=77) == ["git fetch (10m)"]
    assert network_critical(run=lambda *cmd, **kw: table, self_pid=78) is None  # cut short: not even us in it
    assert network_critical(ps_text="") == []  # an injected table (tests, parity) is taken as given


def test_the_engine_checks_its_own_pid(tmp_path, monkeypatch):
    import os

    import lsw_mission_control.net as net

    from scenarios import Project

    e = Project(tmp_path).engine()
    e.ps_text = None
    monkeypatch.setattr(net, "_run", lambda *cmd, **kw: f"  {os.getpid()}  1  01:00 python -m lsw_mission_control\n")
    assert e.network_critical() == []
    monkeypatch.setattr(net, "_run", lambda *cmd, **kw: "  1  0  01:00 /sbin/launchd\n")
    assert e.network_critical() is None


def test_fit_list():
    assert fit_list(["a", "b"], 10) == (2, "a, b")
    assert fit_list(["aaaa", "bbbb", "cccc"], 16) == (3, "aaaa, bbbb, cccc")
    assert fit_list(["aaaa", "bbbb", "cccc"], 15) == (1, "aaaa, +2 more")


def test_network_flag_never_says_safe_when_the_check_failed():
    assert "network check failed" in network_flag(None, 60).plain
    assert network_flag([], 60).plain == "● safe to switch networks"
    flag = network_flag(["git push (1m)"], 60)
    assert flag.plain == f"{NET_CHIP}  git push (1m)"
    assert network_flag(["git push (1m)"], 0).plain == NET_CHIP  # the chip always shows

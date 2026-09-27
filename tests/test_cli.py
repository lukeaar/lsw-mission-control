from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lsw_mission_control import cli

from scenarios import STUB_PLUGIN, Project, midway

ROOT = Path(__file__).resolve().parents[1]


def lswmc(*args, env=None, cwd=None):
    e = dict(os.environ)
    e["PYTHONPATH"] = str(ROOT / "src")
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "lsw_mission_control", *args], capture_output=True, text=True, env=e,
                          cwd=cwd, timeout=60)


def test_check_net_contract(tmp_path):
    p = Project(tmp_path)
    r = lswmc("--project", str(p.root), "--check-net")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "28/28 network cases correct\n7/7 indicator cases correct\n"


def test_check_net_with_project_cases(tmp_path):
    p = Project(tmp_path)
    p.write_config("""
        [network]
        tools = ["mytool"]

        [[network.cases]]
        command = "mytool --sync"
        label = "mytool"

        [[network.cases]]
        command = "othertool --sync"
        label = "othertool"
        """)
    r = lswmc("--config", str(p.dot / "mission-control.toml"), "--check-net")
    assert r.returncode == 1
    assert "MISMATCH want='othertool' got=None: othertool --sync" in r.stdout and "29/30 network cases correct" in r.stdout


def test_self_check_renders_and_writes_nothing(tmp_path):
    p = Project(tmp_path)
    midway(p)
    p.finish_runs()
    r = lswmc("--project", str(p.root), "--check-net", "--self-check")
    assert r.returncode == 0, r.stderr
    assert not p.cache.exists()  # read-only: the finished store was not written


def test_check_net_fails_on_a_broken_plugin_or_config(tmp_path):
    p = Project(tmp_path)
    p.write_config(f"""
        [[plugins]]
        name = "stub"
        file = "{STUB_PLUGIN}"
        class = "StubPlugin"
        """)
    r = lswmc("--project", str(p.root), "--check-net")
    assert r.returncode == 1 and "28/28 network cases correct" in r.stdout
    assert r.stderr.strip().splitlines()[-1] == "plugin stub not loaded: ConfigError: [plugins.options].host is missing (plugin stub)"
    (p.dot / "mission-control.toml").write_text("schema = 1\n[bogus]\n")
    r = lswmc("--project", str(p.root), "--check-net")
    assert r.returncode == 1 and r.stderr.strip() == "mission-control.toml: unknown key bogus"


def test_config_errors_name_the_file_once(tmp_path):
    p = Project(tmp_path)
    (p.dot / "mission-control.toml").write_text("schema = 1\n[project\n")
    r = lswmc("--project", str(p.root), "--check-net")
    assert r.returncode == 1
    assert r.stderr.strip() == ("mission-control.toml: Expected ']' at the end of a table declaration "
                                "(at line 2, column 9)")
    (p.dot / "mission-control.toml").unlink()
    r = lswmc("--project", str(p.root), "--once")
    assert r.returncode == 1 and r.stdout == ""
    assert r.stderr.strip() == (f"mission-control.toml: not found at {p.dot.resolve() / 'mission-control.toml'} "
                                "(`lsw-mc init` writes a starter config)")


def test_flags_are_exact_and_strays_are_ignored_with_a_launcher(tmp_path):
    p = Project(tmp_path)
    launcher = p.dot / "status.py"
    # --check is not --check-net (no abbreviations); a stray word is ignored, as it always was
    r = lswmc("--launcher", str(launcher), "once", "--check", "--check-net")
    assert r.returncode == 0 and "network cases correct" in r.stdout
    r = lswmc("--launcher", str(launcher), "--once", "--width", "90", "--no-usage-probe", "--no-plugins",
              env={"LSW_MC_NOW": "1790000000"})
    assert r.returncode == 0, r.stderr
    assert "DEMO" in r.stdout and ("safe to switch networks" in r.stdout or "NETWORK-CRITICAL" in r.stdout)
    assert max(len(line) for line in r.stdout.splitlines()) <= 90


def test_once_never_starts_the_usage_probe(tmp_path, monkeypatch):
    """A probe outlives --once and spends a model turn: --once shows the last plan-limit data."""
    import lsw_mission_control.app as app
    from lsw_mission_control.engine import Engine

    seen = {}
    monkeypatch.setattr(Engine, "start_sources", lambda self, probe=True: seen.update(probe=probe))
    monkeypatch.setattr(app, "run_once", lambda engine, console: 0)
    monkeypatch.setattr(app, "run_live", lambda engine, console, reloader: None)
    p = Project(tmp_path)
    assert cli.run_cmd(["--project", str(p.root), "--once"]) == 0 and seen == {"probe": False}
    assert cli.run_cmd(["--project", str(p.root)]) == 0 and seen == {"probe": True}
    assert cli.run_cmd(["--project", str(p.root), "--no-usage-probe"]) == 0 and seen == {"probe": False}


def test_help_lists_plugin_flags(tmp_path):
    p = Project(tmp_path)
    midway(p)
    r = lswmc("--project", str(p.root), "-h")
    assert r.returncode == 0 and "--no-stub" in r.stdout and "--check-net" in r.stdout and "subcommands:" in r.stdout


def test_unknown_subcommand():
    with pytest.raises(SystemExit) as e:
        cli.main(["frobnicate"])
    assert e.value.code == 2


def test_init_validate_doctor(tmp_path, capsys):
    proj = tmp_path / "newproj"
    proj.mkdir()
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--project", str(proj), "--github", "example/newproj"])
    assert e.value.code == 0
    dot = proj / ".claude"
    assert {f.name for f in dot.iterdir()} == {"mission-control.toml", "status_plan.json", "status_notes.json", "status.py"}
    assert 'repo = "example/newproj"' in (dot / "mission-control.toml").read_text()
    assert "ENGINE_SRC = " in (dot / "status.py").read_text() and os.access(dot / "status.py", os.X_OK)
    json.loads((dot / "status_plan.json").read_text())
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--project", str(proj)])
    assert e.value.code == 0 and "kept" in capsys.readouterr().out  # never overwrites without --force
    with pytest.raises(SystemExit) as e:
        cli.main(["validate", "--project", str(proj)])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "ok    plan: status_plan.json: release 0.1.0" in out
    (dot / "status_plan.json").write_text("{}")
    (dot / "status_notes.json").write_text('{"waiting_on_owner": [],}')
    with pytest.raises(SystemExit) as e:
        cli.main(["validate", "--project", str(proj)])
    out = capsys.readouterr().out
    assert e.value.code == 1 and "FAIL  plan" in out and "status_notes.json: JSONDecodeError: " in out
    (dot / "status_notes.json").write_text((ROOT / "src" / "lsw_mission_control" / "templates" / "status_notes.json").read_text())
    cfg = dot / "mission-control.toml"
    cfg.write_text(cfg.read_text().replace('[github]\nrepo = "example/newproj"', ""))  # no gh call from a test
    (dot / "status_plan.json").write_text((ROOT / "src" / "lsw_mission_control" / "templates" / "status_plan.json").read_text())
    with pytest.raises(SystemExit) as e:
        cli.main(["doctor", "--project", str(proj)])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "status line hook" in out and "engine venv" in out and "gh" not in out.split()


def test_init_escapes_the_name_and_validate_names_a_missing_config(tmp_path, capsys):
    proj = tmp_path / "quoted"
    proj.mkdir()
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--project", str(proj), "--name", 'my "app" \\ 2'])
    assert e.value.code == 0
    from lsw_mission_control.config import load_config

    cfg = load_config(proj / ".claude" / "mission-control.toml")
    assert cfg.name == 'my "app" \\ 2' and cfg.title == 'MY "APP" \\ 2'
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--project", str(tmp_path / "two"), "--name", "a\nb"])
    assert e.value.code == 2 and not (tmp_path / "two" / ".claude").exists()
    with pytest.raises(SystemExit) as e:
        cli.main(["validate", "--project", str(tmp_path / "nowhere")])
    out = capsys.readouterr().out
    assert e.value.code == 1 and out.startswith("FAIL  config: not found at ") and "lsw-mc init" in out


def test_statusline_subcommand(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"rate_limits": {"five_hour": {"used_percentage": 5}}})))
    cli.main(["statusline"])
    assert capsys.readouterr().out == "5h 5%\n"

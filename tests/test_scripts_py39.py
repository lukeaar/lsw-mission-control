"""The status line, its shim and the launcher run under macOS's /usr/bin/python3 (3.9) before
any venv exists: they must stay standard-library-only and 3.9-compatible."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "lsw_mission_control"
STATUSLINE = PKG / "statusline.py"
LAUNCHER = PKG / "templates" / "launcher.py"
SHIM = PKG / "templates" / "statusline_shim.py"
PY39 = [p for p in ("/usr/bin/python3",
                    "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/"
                    "Resources/Python.app/Contents/MacOS/Python") if Path(p).exists()]
PYTHONS = PY39 + [sys.executable]


@pytest.mark.parametrize("path", [STATUSLINE, LAUNCHER, SHIM])
def test_stdlib_only_and_39_syntax(path):
    tree = ast.parse(path.read_text(), feature_version=(3, 9))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
            [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
        for name in names:
            assert name.split(".")[0] in stdlib, f"{path.name} imports {name}"


def run(py, script, stdin="", env=None, args=()):
    e = dict(os.environ)
    for k, v in (env or {}).items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return subprocess.run([py, str(script), *args], input=stdin, capture_output=True, text=True, env=e, timeout=30)


@pytest.mark.parametrize("py", PYTHONS)
def test_statusline(tmp_path, py):
    usage = tmp_path / "usage"
    data = {"model": {"display_name": "Opus"}, "rate_limits": {"five_hour": {"used_percentage": 41.6, "resets_at": 1},
                                                               "seven_day": {"used_percentage": 3, "resets_at": 2}}}
    r = run(py, STATUSLINE, json.dumps(data), {"LSW_MC_USAGE_DIR": str(usage)})
    assert r.returncode == 0 and r.stdout == "Opus · 5h 42% · wk 3%\n"
    rec = json.loads((usage / "usage.json").read_text())
    assert rec["rate_limits"] == data["rate_limits"] and rec["at"] > 1e9
    assert not [p for p in usage.iterdir() if p.name != "usage.json"]  # the temp file was renamed into place


@pytest.mark.parametrize("stdin", ["", "garbage", "[1, 2]", '{"rate_limits": "x"}', '{"model": {"display_name": "Haiku"}}'])
def test_statusline_never_fails(tmp_path, stdin):
    r = run(PYTHONS[0], STATUSLINE, stdin, {"LSW_MC_USAGE_DIR": str(tmp_path / "u")})
    assert r.returncode == 0 and r.stdout in ("\n", "Haiku\n") and not (tmp_path / "u").exists()


def test_statusline_default_dir(tmp_path):
    env = {"HOME": str(tmp_path), "LSW_MC_USAGE_DIR": ""}
    r = run(PYTHONS[0], STATUSLINE, json.dumps({"rate_limits": {"five_hour": {"used_percentage": 1}}}), env)
    assert r.returncode == 0 and (tmp_path / ".cache" / "lsw-mission-control" / "usage.json").exists()


def test_shim_runs_the_engines_statusline(tmp_path):
    r = run(PYTHONS[0], SHIM, json.dumps({"rate_limits": {"seven_day": {"used_percentage": 9}}}),
            {"LSW_MC_SRC": str(ROOT), "LSW_MC_USAGE_DIR": str(tmp_path / "u")})
    assert r.returncode == 0 and r.stdout == "wk 9%\n" and (tmp_path / "u" / "usage.json").exists()
    r = run(PYTHONS[0], SHIM, "{}", {"LSW_MC_SRC": str(tmp_path / "nowhere")})
    assert r.returncode == 0 and r.stdout == "mission control: engine not found (set ENGINE_SRC in this file, or LSW_MC_SRC)\n"


def fake_venv(tmp_path: Path, marker: bool = True, importable: bool = False) -> tuple[Path, Path]:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    record = tmp_path / "argv.json"
    py = venv / "bin" / "python"
    # The launcher's import probe (`python -c ...`) succeeds only for a venv that has the engine.
    probe = 'if [ "$1" = "-c" ]; then echo 0.1.0; exit 0; fi\n' if importable else 'if [ "$1" = "-c" ]; then exit 1; fi\n'
    py.write_text("#!/bin/sh\n" + probe + f'printf "%s\\n" "$@" > {record}\necho "PREFIX=$PYTHONPYCACHEPREFIX" >> {record}\n')
    py.chmod(0o755)
    if marker:
        (venv / ".lsw-mc-installed").write_text("0.1.0\n")
    return venv, record


@pytest.mark.parametrize("py", PYTHONS)
def test_launcher_execs_the_engine(tmp_path, py):
    proj = tmp_path / "proj" / ".claude"
    proj.mkdir(parents=True)
    launcher = proj / "status.py"
    launcher.write_text(LAUNCHER.read_text())
    venv, record = fake_venv(tmp_path)
    r = run(py, launcher, env={"LSW_MC_VENV": str(venv), "LSW_MC_SRC": str(ROOT), "PYTHONPYCACHEPREFIX": None},
            args=("--once", "--no-plugins", "stray"))
    assert r.returncode == 0, r.stderr
    argv = record.read_text().splitlines()
    assert argv[:-1] == ["-P", "-m", "lsw_mission_control", "--launcher", str(launcher.resolve()), "--once", "--no-plugins",
                         "stray"]
    assert argv[-1] == f"PREFIX={Path.home() / '.cache' / 'lsw-mission-control' / 'pycache'}"


def test_launcher_never_installs_inside_the_gate(tmp_path):
    launcher = tmp_path / "status.py"
    launcher.write_text(LAUNCHER.read_text())
    venv, record = fake_venv(tmp_path, marker=False)  # half-installed: no marker
    r = run(PYTHONS[0], launcher, env={"LSW_MC_VENV": str(venv), "LSW_MC_SRC": str(ROOT)}, args=("--check-net",))
    assert r.returncode == 1 and "venv missing: run python3 .claude/status.py once" in r.stderr
    assert not record.exists() and "network cases correct" not in r.stdout


def test_launcher_adopts_a_venv_installed_by_hand(tmp_path):
    # The README's manual install writes no marker: a venv that already imports the engine is
    # adopted (marker written, [dev] extras kept), never rebuilt.
    launcher = tmp_path / "status.py"
    launcher.write_text(LAUNCHER.read_text())
    venv, record = fake_venv(tmp_path, marker=False, importable=True)
    r = run(PYTHONS[0], launcher, env={"LSW_MC_VENV": str(venv), "LSW_MC_SRC": str(ROOT)}, args=("--once",))
    assert r.returncode == 0, r.stderr
    assert "First run" not in r.stderr and record.exists()
    assert (venv / ".lsw-mc-installed").read_text().strip() == "0.1.0"


def test_launcher_rebuilds_a_dangling_venv_and_needs_the_engine(tmp_path):
    launcher = tmp_path / "status.py"
    launcher.write_text(LAUNCHER.read_text())
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(tmp_path / "gone" / "python3.14")  # its base Python was removed
    (venv / ".lsw-mc-installed").write_text("0.1.0\n")
    r = run(PYTHONS[0], launcher, env={"LSW_MC_VENV": str(venv), "LSW_MC_SRC": str(tmp_path / "no-engine")})
    assert r.returncode == 1 and "lsw-mission-control not found at" in r.stderr


def test_init_writes_the_engine_path(tmp_path):
    from lsw_mission_control.tools import with_engine_path

    text = with_engine_path(LAUNCHER.read_text(), Path("/opt/engine"))
    assert "ENGINE_SRC = '/opt/engine'" in text and "ENGINE_SRC = None" not in text
    ast.parse(text, feature_version=(3, 9))

#!/usr/bin/env python3
"""Mission control for this project: a thin launcher for lsw-mission-control.

    python3 .claude/status.py            live dashboard, refreshes every 5 s; scroll with the wheel,
                                         ↑↓, PgUp/PgDn, g/G; q or Ctrl+C to quit
    python3 .claude/status.py --once     print one snapshot and exit
    python3 .claude/status.py --no-usage-probe   never ask the CLI for plan limits
    python3 .claude/status.py --check-net   check the network indicator
    python3 .claude/status.py -h         every flag, plugin flags included

The engine runs from its own venv (~/.venvs/lsw-mission-control, or $LSW_MC_VENV), built here on
the first run from the engine checkout (ENGINE_SRC below, or $LSW_MC_SRC). The venv's editable
install then decides which checkout runs: to run another one (a worktree), install it into another
venv and point $LSW_MC_VENV at that. This project's config is .claude/mission-control.toml,
beside this file.

Standard library only and Python 3.9-compatible: macOS's /usr/bin/python3 runs this first.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE_SRC = None  # `lsw-mc init` writes the engine checkout's absolute path here

HERE = Path(__file__).resolve()
VENV = Path(os.environ.get("LSW_MC_VENV") or Path.home() / ".venvs" / "lsw-mission-control").expanduser()
SRC = Path(os.environ.get("LSW_MC_SRC") or ENGINE_SRC or HERE.parents[2] / "lsw-mission-control").expanduser()
PY = VENV / "bin" / "python"
MARKER = VENV / ".lsw-mc-installed"  # written only once pip has succeeded
PYCACHE = Path.home() / ".cache" / "lsw-mission-control" / "pycache"
PTH = "import os, sys; sys.pycache_prefix = sys.pycache_prefix or os.path.expanduser('~/.cache/lsw-mission-control/pycache')\n"


def _base_python():
    for cand in (sys.executable, "/opt/homebrew/bin/python3", shutil.which("python3.14"), shutil.which("python3.13"),
                 shutil.which("python3.12"), shutil.which("python3.11")):
        if cand and subprocess.run([cand, "-c", "import sys; sys.exit(sys.version_info < (3, 11))"]).returncode == 0:
            return cand
    raise SystemExit("lsw-mission-control needs Python 3.11 or newer (brew install python)")


def _ready():
    # A dangling interpreter link (its base Python was removed) means: build the venv again.
    if not PY.resolve().exists():
        return False
    if MARKER.exists():
        return True
    # No marker: a half-finished install, or one made by hand from the README. A venv that
    # already imports the engine is adopted as it is (its [dev] extras included), never rebuilt.
    probe = subprocess.run([str(PY), "-c", "import lsw_mission_control as m; print(m.__version__)"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        return False
    MARKER.write_text(probe.stdout.strip() + "\n")
    return True


def _install():
    if not (SRC / "pyproject.toml").exists():
        raise SystemExit("lsw-mission-control not found at %s (set LSW_MC_SRC)" % SRC)
    sys.stderr.write("First run: creating %s with lsw-mission-control…\n" % VENV)  # never into --once's output
    sys.stderr.flush()
    subprocess.run([_base_python(), "-m", "venv", "--clear", str(VENV)], check=True)
    subprocess.run([str(PY), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    subprocess.run([str(PY), "-m", "pip", "install", "-q", "-c", str(SRC / "constraints.txt"), "-e", str(SRC)],
                   check=True)
    site = subprocess.run([str(PY), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    Path(site, "lsw_mc_pycache.pth").write_text(PTH)  # no __pycache__ in the (synced) checkout
    version = subprocess.run([str(PY), "-c", "import lsw_mission_control as m; print(m.__version__)"],
                             capture_output=True, text=True).stdout.strip()
    MARKER.write_text(version + "\n")


def main():
    args = sys.argv[1:]
    if not _ready():
        if "--check-net" in args:
            # A reload gate must never install (it would time out half-way): say what to do instead.
            sys.stderr.write("lsw-mission-control venv missing: run python3 .claude/status.py once\n")
            raise SystemExit(1)
        _install()
    env = dict(os.environ)
    env.setdefault("PYTHONPYCACHEPREFIX", str(PYCACHE))
    os.execve(str(PY), [str(PY), "-P", "-m", "lsw_mission_control", "--launcher", str(HERE)] + args, env)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Claude Code status line: runs lsw-mission-control's (standard library only; never fails).

Point the statusLine hook in ~/.claude/settings.json at this file (or straight at the engine's
src/lsw_mission_control/statusline.py). Python 3.9-compatible.
"""
import os
import runpy
from pathlib import Path

ENGINE_SRC = None  # `lsw-mc init` writes the engine checkout's absolute path here

try:
    SRC = Path(os.environ.get("LSW_MC_SRC") or ENGINE_SRC or Path(__file__).resolve().parents[2] / "lsw-mission-control")
    TARGET = SRC.expanduser() / "src" / "lsw_mission_control" / "statusline.py"
    if TARGET.exists():
        runpy.run_path(str(TARGET), run_name="__main__")  # compiles in memory: no __pycache__ beside the engine
    else:  # say so in the status line itself, rather than print nothing
        print("mission control: engine not found (set ENGINE_SRC in this file, or LSW_MC_SRC)")
except BaseException:  # a status line must never break the session
    print("")

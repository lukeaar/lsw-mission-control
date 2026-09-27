"""lsw-mission-control: a live terminal view of everything Claude Code is doing on a project."""

import os as _os
import sys as _sys

__version__ = "0.1.0"

# Keep bytecode out of the checkout (it may live in a synced folder). The venv's
# lsw_mc_pycache.pth sets this before any import; this covers every other interpreter.
if not _sys.pycache_prefix:
    _sys.pycache_prefix = _os.environ.get("PYTHONPYCACHEPREFIX") or _os.path.expanduser(
        "~/.cache/lsw-mission-control/pycache")

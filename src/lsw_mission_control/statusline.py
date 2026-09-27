#!/usr/bin/env python3
"""Claude Code status line that also feeds mission control.

Claude Code runs this after each reply and passes a JSON description of the session on stdin.
For claude.ai subscribers that JSON carries `rate_limits` (the 5-hour and weekly windows:
used_percentage, resets_at in epoch seconds): the only documented way to read them
(https://code.claude.com/docs/en/statusline). This saves them to
$LSW_MC_USAGE_DIR/usage.json (default ~/.cache/lsw-mission-control/usage.json), account-wide,
for every dashboard, and prints a one-line summary.

Standard library only and Python 3.9-compatible (macOS's /usr/bin/python3 runs it), runnable by
path, and it never fails: a status line must not break the session.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path


def usage_file():
    env = os.environ.get("LSW_MC_USAGE_DIR")
    base = Path(env).expanduser() if env else Path.home() / ".cache" / "lsw-mission-control"
    return base / "usage.json"


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    limits = data.get("rate_limits") or {}
    if not isinstance(limits, dict):
        limits = {}
    if limits:
        try:
            out = usage_file()
            out.parent.mkdir(parents=True, exist_ok=True)
            record = {"at": time.time(), "rate_limits": limits}
            fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".usage-")
            with os.fdopen(fd, "w") as f:
                json.dump(record, f)
            os.replace(tmp, str(out))
        except Exception:
            pass
    parts = []
    for key, label in (("five_hour", "5h"), ("seven_day", "wk")):
        try:
            pct = (limits.get(key) or {}).get("used_percentage")
            if pct is not None:
                parts.append("%s %.0f%%" % (label, float(pct)))
        except Exception:
            pass
    try:
        model = ((data.get("model") or {}).get("display_name")) or ""
    except Exception:
        model = ""
    print(" · ".join(([str(model)] if model else []) + parts))


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # a status line must never break the session
        print("")

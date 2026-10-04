"""lsw-mc init / doctor / validate."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import lsw_mission_control
from lsw_mission_control.config import CONFIG_NAME, ConfigError, find_config, load_config
from lsw_mission_control.notes import load_notes
from lsw_mission_control.plan import parse_plan
from lsw_mission_control.plugin import Flags, load_plugins
from lsw_mission_control.util import human, run

PACKAGE_DIR = Path(lsw_mission_control.__file__).resolve().parent
TEMPLATES = PACKAGE_DIR / "templates"
ENGINE_SRC = PACKAGE_DIR.parents[1]  # the checkout (an editable install runs from it)
SNIPPET = ENGINE_SRC / "docs" / "claude-md-snippet.md"


def with_engine_path(text: str, engine: Path) -> str:
    """A launcher or status-line template with the engine checkout's absolute path filled in."""
    return re.sub(r"^ENGINE_SRC = None.*$", f"ENGINE_SRC = {str(engine)!r}", text, count=1, flags=re.M)


def _project_parser(prog: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"lsw-mc {prog}", allow_abbrev=False)
    p.add_argument("--project", help="the project dir (default: the current dir)")
    p.add_argument("--config", help="a mission-control.toml")
    return p


def init(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="lsw-mc init", allow_abbrev=False,
                                description="Write a starter config, plan, notes and launcher into <project>/.claude/.")
    p.add_argument("--project", default=".", help="the project dir (default: the current dir)")
    p.add_argument("--name", help="the project's name (default: the dir's name)")
    p.add_argument("--github", help="OWNER/NAME of its GitHub repository")
    p.add_argument("--force", action="store_true", help="overwrite files that exist")
    a = p.parse_args(argv)
    root = Path(a.project).expanduser().resolve()
    name = a.name or root.name
    if a.github and not re.fullmatch(r"[\w.-]+/[\w.-]+", a.github):
        print("--github must be OWNER/NAME", file=sys.stderr)
        return 2
    if any(ord(ch) < 32 for ch in name):
        print("--name must be one line of text", file=sys.stderr)
        return 2
    dot = root / ".claude"
    dot.mkdir(parents=True, exist_ok=True)
    github_block = f'[github]\nrepo = "{a.github}"\n' if a.github else '# [github]\n# repo = "OWNER/NAME"\n'
    files = {
        # json.dumps writes a valid TOML basic string: quotes and backslashes in a name are escaped
        CONFIG_NAME: (TEMPLATES / "mission-control.toml").read_text().format(
            label=name, name=json.dumps(name, ensure_ascii=False), title=json.dumps(name.upper(), ensure_ascii=False),
            github_block=github_block),
        "status_plan.json": (TEMPLATES / "status_plan.json").read_text(),
        "status_notes.json": (TEMPLATES / "status_notes.json").read_text(),
        "status.py": with_engine_path((TEMPLATES / "launcher.py").read_text(), ENGINE_SRC),
    }
    for fname, text in files.items():
        path = dot / fname
        if path.exists() and not a.force:
            print(f"kept      {path} (exists; --force overwrites)")
            continue
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        if fname == "status.py":
            tmp.chmod(0o755)
        os.replace(tmp, path)
        print(f"wrote     {path}")
    try:
        load_config(dot / CONFIG_NAME)  # the starter must load
    except ConfigError as e:
        print(f"{CONFIG_NAME}: {e} (edit it, then run lsw-mc validate)", file=sys.stderr)
        return 1
    print(f"\nRun it:   python3 {dot / 'status.py'}")
    print(f"Status line (plan limits): see {ENGINE_SRC / 'README.md'}, 'The status line'")
    print(f"For the project's CLAUDE.md: {SNIPPET}")
    return 0


def _check(label: str, ok: bool | None, detail: str = "") -> bool:
    mark = {True: "ok  ", False: "FAIL", None: "--  "}[ok]
    print(f"{mark}  {label}" + (f": {detail}" if detail else ""))
    return ok is not False


def validate(argv: list[str]) -> int:
    a = _project_parser("validate").parse_args(argv)
    path = find_config(config=a.config, project=a.project)
    good = True
    try:
        cfg = load_config(path)
        _check("config", True, str(path))
    except ConfigError as e:
        _check("config", False, f"{path}: {e}" if path.exists() else str(e))  # a missing one names its path
        return 1
    plugins, errors = load_plugins(cfg, Flags())
    for name, msg in errors:
        good = _check(f"plugin {name}", False, msg) and good
    for pl in plugins:
        _check(f"plugin {pl.name}", True)
    try:
        plan = parse_plan(json.loads(cfg.plan_file.read_text()), plugins)
        # `later` by its releases: a misspelt key is ignored like any unknown one, and reads "later no"
        _check("plan", True, f"{cfg.plan_file.name}: release {plan.release}, {len(plan.items)} items, "
                             f"{len(plan.other)} other, next {'yes' if plan.next else 'no'}, "
                             f"later {' '.join(r.release for r in plan.later) or 'no'}")
    except Exception as e:  # noqa: BLE001
        good = _check("plan", False, f"{cfg.plan_file}: {type(e).__name__}: {e}") and good
    try:
        notes = load_notes(cfg.notes_file)
        _check("notes", True, f"{len(notes.waiting_on_owner)} waiting on the owner")
    except (OSError, ValueError) as e:
        good = _check("notes", False, f"{cfg.notes_file}: {type(e).__name__}: {e}") and good
    return 0 if good else 1


def doctor(argv: list[str]) -> int:
    a = _project_parser("doctor").parse_args(argv)
    code = validate(argv)
    try:
        cfg = load_config(find_config(config=a.config, project=a.project))
    except ConfigError:
        return 1
    runs = list(cfg.projects_dir.glob("*/subagents/workflows/wf_*")) if cfg.projects_dir.exists() else []
    _check("sessions", cfg.projects_dir.exists() or None, f"{cfg.projects_dir} ({len(runs)} workflow runs)")
    if cfg.github.repo:
        out = run("gh", "auth", "status", timeout=20)  # never --show-token
        _check("gh", "Logged in" in out or "logged in" in out.lower(), "authenticated" if out else "gh is missing or not signed in")
    usage = cfg.usage_dir / "usage.json"
    try:
        age = time.time() - usage.stat().st_mtime
        _check("plan limits", age < 25 * 60 or None, f"{usage} is {human(age)} old")
    except OSError:
        _check("plan limits", None, f"{usage} not written yet (status line or probe)")
    hook = ""
    try:
        settings = json.loads((Path.home() / ".claude" / "settings.json").read_text())
        hook = str(((settings.get("statusLine") or {}).get("command")) or "")
    except (OSError, ValueError, AttributeError):
        pass
    _check("status line hook", bool(hook) or None, hook or "none in ~/.claude/settings.json")
    marker = Path(os.environ.get("LSW_MC_VENV") or Path.home() / ".venvs" / "lsw-mission-control") / ".lsw-mc-installed"
    _check("engine venv", marker.exists() or None, str(marker.parent))
    _check("pycache kept out of the checkout", bool(sys.pycache_prefix), str(sys.pycache_prefix or ""))
    return code

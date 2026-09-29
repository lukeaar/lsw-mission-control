"""lsw-mc: the command line.

    lsw-mc [run] [--launcher FILE | --config FILE | --project DIR] [--once] [--check-net]
                 [--self-check] [--no-usage-probe] [--no-plugins] [--cache-dir DIR] [--width N]
                 [plugin flags]
    lsw-mc init [--project DIR] [--name NAME] [--github OWNER/REPO] [--force]
    lsw-mc doctor [--project DIR | --config FILE]
    lsw-mc validate [--project DIR | --config FILE]
    lsw-mc statusline

Flags are matched EXACTLY (`--check` is not `--check-net`), and with --launcher every token that
is not a known option is ignored, as the single-file dashboard always did.
"""

from __future__ import annotations

import argparse
import importlib
import io
import os
import pkgutil
import sys
import traceback
from pathlib import Path

import lsw_mission_control
from lsw_mission_control.config import ConfigError, find_config, load_config
from lsw_mission_control.net import NetRules, check_net_cases
from lsw_mission_control.plugin import Flags, load_plugin_class, load_plugins

BOOL_FLAGS = {
    "--once": "print one snapshot and exit",
    "--check-net": "check the network indicator (its process-line and drawing cases) and exit",
    "--self-check": "with --check-net: also import everything and render one frame (the reload gate)",
    "--no-usage-probe": "never ask the Claude CLI for plan limits",
    "--no-plugins": "load no plugins",
}
SUBCOMMANDS = ("run", "init", "doctor", "validate", "statusline")


def _run_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lsw-mc", allow_abbrev=False, add_help=False,
                                description="lsw-mission-control: a live terminal dashboard of a project's Claude Code work.")
    p.add_argument("--launcher", help="the project's .claude/status.py launcher (the config is beside it)")
    p.add_argument("--config", help="a mission-control.toml")
    p.add_argument("--project", help="a project dir (config: <dir>/.claude/mission-control.toml)")
    p.add_argument("--cache-dir", help="use this cache dir instead of the config's (tests, rehearsals)")
    p.add_argument("--width", type=int, help="fix the console width (for --once)")
    return p


def _help(opts, argv) -> str:
    p = _run_parser()
    for flag, text in BOOL_FLAGS.items():
        p.add_argument(flag, action="store_true", help=text)
    try:
        cfg = load_config(find_config(config=opts.config, launcher=opts.launcher, project=opts.project))
        for spec in cfg.plugins:
            try:
                for f in load_plugin_class(spec).cli_flags:
                    p.add_argument(f.flag, action="store_true", help=f"{f.help}  [plugin {spec.name}]")
            except Exception:  # noqa: BLE001
                pass
    except ConfigError:
        pass
    return p.format_help() + "\nsubcommands: " + ", ".join(SUBCOMMANDS) + "\n"


def import_all() -> list[str]:
    """Import every module of the package: an edit that breaks an import is refused by the gate
    before a live view could exec into it."""
    problems = []
    for mod in pkgutil.walk_packages(lsw_mission_control.__path__, lsw_mission_control.__name__ + "."):
        if mod.name.endswith(".__main__"):
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{mod.name}: {type(e).__name__}: {e}")
    return problems


def self_check(cfg, flags: Flags, plugins, load_errors) -> int:
    """Render one frame through the live view's Scroll at 150x50 into a null console, turn the
    logo once and feed the key parser, with nothing started and nothing written. It fails on any
    error the owner's window would show: building the frame, building or DRAWING any panel (a
    markup error in a cell only surfaces while drawing), or any plugin call."""
    from rich.console import Console

    from lsw_mission_control.app import Scroll, apply_keys, scroll_envs
    from lsw_mission_control.engine import Engine

    try:
        engine = Engine(cfg, flags, readonly=True, plugins=plugins, load_errors=load_errors)
        console = Console(file=io.StringIO(), width=150, height=50, force_terminal=True, color_system="truecolor",
                          legacy_windows=False, style=f"{cfg.theme.text} on {cfg.theme.bg}")
        scroll = Scroll(cfg.layout.max_width, engine.logo, scroll_envs(cfg.compat.legacy_scroll_env))
        scroll.body, scroll.net, _w = engine.safe_frame(console)
        console.set_alt_screen(True)
        console.print(scroll)
        scroll.spin(console)
        console.set_alt_screen(False)
        rest, quit_ = apply_keys(scroll, b"jjk \x1b[B\x1b[6~b\x1b[5~Gg\x1bOAq\x1b[")
        if rest != b"\x1b[" or not quit_:
            print("self-check: the key parser is broken", file=sys.stderr)
            return 1
        drawn = [scroll.error] if scroll.error else []
        drawn += [f"{name} failed: {tb}" for name, tb in engine.panel_errors.items()]
        for failed in ([engine.last_error] if engine.last_error else []) + drawn + engine.plugin_tracebacks:
            sys.stderr.write(failed)
            return 1
        if engine.logo is not None and engine.logo.off:
            print("self-check: the logo failed to turn", file=sys.stderr)
            return 1
    except Exception:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())
        return 1
    return 0


def check_net(cfg, flags: Flags) -> int:
    rules = NetRules.with_extras(cfg.network.tools, cfg.network.subcommands)
    code = check_net_cases(rules, cfg.network.cases)
    problems = import_all()
    plugins, load_errors = load_plugins(cfg, flags) if not flags.has("--no-plugins") else ([], [])
    for p in problems:
        print(f"not importable: {p}", file=sys.stderr)
    for name, msg in load_errors:
        print(f"plugin {name} not loaded: {msg}", file=sys.stderr)
    if problems or load_errors:
        code = 1
    if flags.has("--self-check") and not problems:
        code = self_check(cfg, flags, plugins, load_errors) or code
    return code


def run_cmd(argv: list[str]) -> int:
    opts, _rest = _run_parser().parse_known_args(argv)
    flags = Flags(argv)
    if flags.has("-h") or flags.has("--help"):
        print(_help(opts, argv), end="")
        return 0
    cfg_path = find_config(config=opts.config, launcher=opts.launcher, project=opts.project)
    try:
        cfg = load_config(cfg_path, cache_dir=opts.cache_dir)
    except ConfigError as e:
        print(f"{cfg_path.name}: {e}", file=sys.stderr)
        return 1
    if flags.has("--check-net"):
        return check_net(cfg, flags)

    from rich.console import Console

    from lsw_mission_control.app import run_live, run_once
    from lsw_mission_control.engine import PACKAGE_DIR, Engine
    from lsw_mission_control.reload import Reloader

    kwargs = {"width": opts.width} if opts.width else {}
    console = Console(style=f"{cfg.theme.text} on {cfg.theme.bg}", **kwargs)
    engine = Engine(cfg, flags, no_plugins=flags.has("--no-plugins"))
    # --once never probes: it exits before the probe's answer (~7-10 s), and an orphaned probe
    # goes on to spend a model turn. The plan limits it shows are what the last probe or the
    # status line wrote.
    engine.start_sources(probe=not flags.has("--no-usage-probe") and not flags.has("--once"))
    if flags.has("--once"):
        return run_once(engine, console)
    launcher = Path(opts.launcher).resolve() if opts.launcher else None
    reloader = Reloader(engine.watched_files(), launcher, argv, PACKAGE_DIR, cfg.compat.legacy_scroll_env,
                        rescan=engine.watched_files)
    run_live(engine, console, reloader)
    return 0


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--launcher" in argv or not argv or argv[0].startswith("-") or argv[0] == "run":
        if argv and argv[0] == "run":
            argv = argv[1:]
        raise SystemExit(run_cmd(argv))
    cmd, rest = argv[0], argv[1:]
    if cmd == "statusline":
        from lsw_mission_control import statusline

        statusline.main()
        return
    if cmd in ("init", "doctor", "validate"):
        from lsw_mission_control import tools

        raise SystemExit(getattr(tools, cmd)(rest))
    print(f"lsw-mc: unknown command {cmd!r} (try: lsw-mc -h)", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("PYTHONPYCACHEPREFIX", str(Path.home() / ".cache" / "lsw-mission-control" / "pycache"))
    main()

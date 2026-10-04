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
import re
import sys
import traceback
from pathlib import Path

import lsw_mission_control
from lsw_mission_control.config import ConfigError, find_config, load_config
from lsw_mission_control.connectivity import CAPTIVE, OFFLINE, ONLINE, UNKNOWN, Conn
from lsw_mission_control.net import NetRules, NetState, check_net_cases
from lsw_mission_control.plugin import Flags, load_plugin_class, load_plugins

# The pinned row's states --self-check draws beyond its frame's own: not connected with work in
# flight, network-critical, safe, and an answer too old with ps failed.
SELF_CHECK_ROWS = (NetState(["git push (1m)"], Conn(OFFLINE, CAPTIVE)), NetState(["git push (1m)"], Conn(ONLINE)),
                   NetState([], Conn(ONLINE)), NetState(None, Conn(UNKNOWN, "last checked 40s ago")))
# The window sizes --self-check draws the live view at (columns, rows): the default width cap; 99
# columns, one short of the 100 the logo once needed, where a real window had no logo and a check
# at 150 alone could not tell; and 80, a narrow one, where Model usage usually leaves it no room.
# Each pass also draws at the tightest width the logo fits in, SELF_CHECK_TIGHT_ROWS rows high.
SELF_CHECK_SIZES = ((150, 50), (99, 60), (80, 40))
SELF_CHECK_TIGHT_ROWS = 60
# What --self-check's second pass shows as counted: made up, and held only in its own engine's
# memory (it starts nothing and writes nothing). A live window shows token counts from its counter's
# first answer on, and Model usage is then taller (10 rows on a real window): beside it the logo is
# narrower than square where room is tight, a path the first pass, nothing counted, never draws (its
# Model usage is 5 rows, and a 5-row square logo is already the smallest).
SELF_CHECK_TOKENS = {"5h": (1_200, 350_000, 2_400_000, 41_000_000), "today": (2_100, 610_000, 4_100_000, 88_000_000),
                     "7d": (52_000, 4_900_000, 120_000_000, 2_300_000_000)}
SELF_CHECK_BY_MODEL = {"Opus": {"5h": 300_000, "today": 520_000, "7d": 4_100_000},
                       "Sonnet": {"5h": 50_000, "today": 90_000, "7d": 700_000}}
BOX_OR_SPACE = re.compile(r"[\u2500-\u257f\s]")  # box drawing and white space

BOOL_FLAGS = {
    "--once": "print one snapshot and exit",
    "--check-net": "check the network indicator (its process-line and drawing cases) and exit",
    "--self-check": "with --check-net: also import everything and draw the dashboard at three sizes (the reload gate)",
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


def drawn_lines(console, body, width: int) -> list:
    """The dashboard's body drawn `width` wide, as the live view draws it: lines of Segments."""
    return console.render_lines(body, console.options.update(width=width, height=None), pad=True)


def model_usage_content(console, body, width: int) -> list[str] | None:
    """What Model usage shows when the dashboard is drawn `width` wide: its lines with the borders and
    spaces taken out (so a row cut short, wrapped or truncated reads differently), from its top border
    to the end (while the logo is on, the config keeps Model usage last). None when there is none."""
    lines = ["".join(seg.text for seg in line) for line in drawn_lines(console, body, width)]
    tops = [i for i, line in enumerate(lines) if line.startswith("╭") and " Model usage " in line]
    return [BOX_OR_SPACE.sub("", line) for line in lines[tops[-1]:]] if tops else None


def usage_fit(console, usage, width: int) -> tuple[int, int]:
    """(Model usage's rows, the narrowest width it is drawn whole at), for the panel `usage` alone:
    from that width up to `width` it shows exactly what it shows `width` wide, its borders and spaces
    taken out, so a row cut short or a subtitle truncated reads differently. Found by drawing, never
    by the rule that places the logo (render/usage.py measures with rich's Measurement)."""
    def content(w: int) -> list[str]:
        return [BOX_OR_SPACE.sub("", "".join(seg.text for seg in line))
                for line in console.render_lines(usage, console.options.update(width=w, height=None))]
    whole = content(width)
    need = width
    while need > 1 and content(need - 1) == whole:
        need -= 1
    return len(whole) - 2, need


def logo_left_out_with_room(console, body, width: int) -> bool:
    """True when the frame has no logo though it had room for one: Model usage, at least LOGO_MIN_ROWS
    rows, shows exactly the same drawn beside the smallest logo panel (and the space between) as
    drawn the whole width. That is measured by drawing, not by the rule that placed the logo
    (render/usage.py measures with rich's Measurement), so a rule that leaves the logo out with room
    for it, as the fixed 100-column cut-off did at 99, is caught."""
    from lsw_mission_control.render.logo import LOGO_MIN_COLS, LOGO_MIN_ROWS, LOGO_PANEL_PAD

    whole = model_usage_content(console, body, width)
    if whole is None or len(whole) - 2 < LOGO_MIN_ROWS:
        return False
    return model_usage_content(console, body, width - (LOGO_MIN_COLS + LOGO_PANEL_PAD) - 1) == whole


def cells_at(lines: list, top: int, x: int, rows: int, cols: int) -> list[str] | None:
    """The text of the `rows` x `cols` cells at (top, x) of drawn lines, what a turn of the logo found
    there would redraw: cut short at a line's end ("" wholly past it), None when the rows run past the
    last line or x is negative."""
    from rich.segment import Segment

    if top < 0 or x < 0 or top + rows > len(lines):
        return None
    out = []
    for line in lines[top:top + rows]:
        parts = list(Segment.divide(line, [x, x + cols]))  # before x, then the cells; one part when x is past the end
        out.append("".join(seg.text for seg in parts[1]) if len(parts) > 1 else "")
    return out


def logo_problem(scroll, console, width: int, built: int, need: int | None = None) -> str:
    """Why the logo would stand still in the live view at this size, or cut Model usage short ("" when
    it turns beside a whole Model usage, or when there is no room for it here): left out though Model
    usage leaves it room; drawn in more columns than Model usage leaves it (`need`: the narrowest width
    Model usage is drawn whole at, from usage_fit; unchecked when None); drawn where the live view does
    not find it (nowhere, or at other cells than its own); or a turn that redraws nothing. `width` is
    the dashboard's, `built` the frame of the turn its body holds. The view is moved to the end of the
    dashboard first, where the logo is, so a turn must redraw its cells."""
    from lsw_mission_control.render.logo import LOGO_PANEL_PAD

    logo = scroll.logo
    if logo is None or logo.off:
        return ""  # off is the caller's to report
    if not logo.geom:
        return "the logo is left out though Model usage leaves it room" if logo_left_out_with_room(
            console, scroll.body, width) else ""
    rows, cols = logo.geom
    if need is not None and width - (cols + LOGO_PANEL_PAD) - 1 < need:  # the row: Model usage, a space, the logo's panel
        return "the logo cuts Model usage short"
    scroll.offset = 10**6  # clamped to the end while drawing
    console.print(scroll)
    drawn = ["".join(ch for ch, _side in row) for row in logo.frame(rows, cols, built)]
    at = scroll.logo_at
    if not at or cells_at(drawn_lines(console, scroll.body, width), at[0], at[1], rows, cols) != drawn:
        return "the logo is drawn where the live view does not find it"
    before = len(console.file.getvalue())
    scroll.spin(console)
    if not logo.off and len(console.file.getvalue()) == before:
        return "the logo does not turn: a turn drew nothing"
    return ""


def self_check(cfg, flags: Flags, plugins, load_errors) -> int:
    """Render the dashboard through the live view's Scroll into a null console, turn the logo and
    feed the key parser, with nothing started and nothing written, every frame at one instant. It
    draws in two passes: nothing counted, as a window starts, then with token counts
    (SELF_CHECK_TOKENS, in its own memory), as a window runs; each at SELF_CHECK_SIZES and at the
    tightest width the logo fits in (Model usage's narrowest whole width, found by drawing, plus the
    smallest logo panel and the space before it), where the logo is narrower than square once Model
    usage is over LOGO_MIN_ROWS rows. It fails on any error the owner's window would show: building
    the frame, building or DRAWING any panel (a markup error in a cell only surfaces while drawing),
    or any plugin call; and on a logo that would stand still or cut Model usage short: left out
    though Model usage leaves it room, drawn in more columns than Model usage leaves it, drawn where
    the live view does not find it, or a turn that redraws nothing while the logo is on screen."""
    from rich.console import Console

    from lsw_mission_control import util
    from lsw_mission_control.app import Scroll, apply_keys, scroll_envs
    from lsw_mission_control.engine import Engine
    from lsw_mission_control.render.logo import LOGO_MIN_COLS, LOGO_MIN_ROWS, LOGO_PANEL_PAD
    from lsw_mission_control.render.usage import usage_panel

    def null_console(columns: int, lines: int) -> Console:
        return Console(file=io.StringIO(), width=columns, height=lines, force_terminal=True, color_system="truecolor",
                       legacy_windows=False, style=f"{cfg.theme.text} on {cfg.theme.bg}")

    # One instant for every frame: Model usage's width, found once a pass, must be that of the Model
    # usage each frame draws (its subtitle says "· X ago", which a passing minute can widen).
    held, instant = util._clock, util.now()
    util.set_clock(lambda: instant)
    try:
        engine = Engine(cfg, flags, readonly=True, plugins=plugins, load_errors=load_errors)
        scroll = None
        for counted in (False, True):
            if counted:
                engine.store.update(tokens=SELF_CHECK_TOKENS, tokens_by_model=SELF_CHECK_BY_MODEL)
            sizes, need = list(SELF_CHECK_SIZES), None
            if engine.logo is not None and "usage" in cfg.layout.panels:
                widest = cfg.layout.max_width
                try:
                    rows, need = usage_fit(null_console(widest, SELF_CHECK_TIGHT_ROWS),
                                           usage_panel(engine.build_frame(widest), widest), widest)
                except Exception:  # noqa: BLE001 — Model usage failing is its frame's to report, in its place
                    rows = 0
                tight = (need or 0) + 1 + LOGO_PANEL_PAD + LOGO_MIN_COLS  # Model usage, a space, the smallest logo
                if rows >= LOGO_MIN_ROWS and tight <= widest and all(w != tight for w, _l in sizes):
                    sizes.append((tight, SELF_CHECK_TIGHT_ROWS))
            for columns, lines in sizes:
                console = null_console(columns, lines)
                scroll = Scroll(cfg.layout.max_width, engine.logo, scroll_envs(cfg.compat.legacy_scroll_env))
                scroll.body, scroll.net, width = engine.safe_frame(console)
                built = engine.logo.i if engine.logo is not None else 0  # the frame of the turn this body holds
                console.set_alt_screen(True)
                console.print(scroll)
                scroll.spin(console)
                if not counted and (columns, lines) == SELF_CHECK_SIZES[0]:
                    # Nothing is started, so that frame's row says the internet check has not answered
                    # yet: draw the row's other states too, through the same Scroll and this config.
                    for net in SELF_CHECK_ROWS:
                        scroll.net = net
                        console.print(scroll)
                # Each frame's errors are its own (the next frame starts them afresh): read them before the
                # logo's checks, which draw the body again (Model usage narrower, too) beyond the frame.
                drawn = [scroll.error] if scroll.error else []
                drawn += [f"{name} failed: {tb}" for name, tb in engine.panel_errors.items()]
                for failed in ([engine.last_error] if engine.last_error else []) + drawn + engine.plugin_tracebacks:
                    sys.stderr.write(failed)
                    return 1
                problem = logo_problem(scroll, console, width, built, need)
                console.set_alt_screen(False)
                if engine.logo is not None and engine.logo.off:
                    print("self-check: the logo failed to turn", file=sys.stderr)
                    return 1
                if problem:
                    print(f"self-check: {problem} ({columns}x{lines}{', tokens counted' if counted else ''})", file=sys.stderr)
                    return 1
        rest, quit_ = apply_keys(scroll, b"jjk \x1b[B\x1b[6~b\x1b[5~Gg\x1bOAq\x1b[")
        if rest != b"\x1b[" or not quit_:
            print("self-check: the key parser is broken", file=sys.stderr)
            return 1
    except Exception:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())
        return 1
    finally:
        util.set_clock(held)
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

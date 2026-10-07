from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lsw_mission_control import cli, testing
from lsw_mission_control.render.logo import LOGO_MIN_COLS, LOGO_MIN_ROWS

from conftest import NOW
from scenarios import HOUR, STUB_PLUGIN, UNREADABLE, Project, midway

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
    assert r.stdout == "28/28 network cases correct\n22/22 indicator cases correct\n39/39 connection cases correct\n"


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


def in_process_self_check(p: Project) -> int:
    """cli.self_check in this process, so that a test can break one part of what it checks."""
    from lsw_mission_control.config import load_config
    from lsw_mission_control.plugin import Flags, load_plugins

    cfg = load_config(p.dot / "mission-control.toml")
    flags = Flags(["--check-net", "--self-check"])
    plugins, errors = load_plugins(cfg, flags)
    return cli.self_check(cfg, flags, plugins, errors)


def gate_frames(p: Project, monkeypatch) -> tuple[int, list[list[tuple]]]:
    """cli.self_check in this process, with what it found at each frame it checked, per pass:
    [[(columns, lines, the logo's (rows, cols) or None, found by the live view), ...], ...]."""
    passes: list[list[tuple]] = []
    real = cli.logo_problem

    def spy(scroll, console, width, built, need=None):
        problem = real(scroll, console, width, built, need)
        if (console.width, console.height) == cli.SELF_CHECK_SIZES[0]:
            passes.append([])  # every pass starts at the first size
        passes[-1].append((console.width, console.height, scroll.logo.geom, scroll.logo_at is not None))
        return problem
    monkeypatch.setattr(cli, "logo_problem", spy)
    return in_process_self_check(p), passes


def test_self_check_draws_the_live_view_at_every_size(tmp_path, monkeypatch):
    """One size (150x50) could not see a logo left out at a narrower window, or drawn there where
    the live view does not find it: it draws at several, 99 columns among them (the logo needed
    100), and turns the logo at each. And it draws twice: nothing counted, as a window starts (Model
    usage 5 rows, whose square logo is the smallest), then with token counts, as a window runs (Model
    usage taller), each time also at the tightest width the logo fits in, where with token counts it
    is narrower than square: the path an owner's 99-column window takes (review, 2026-10-04)."""
    p = Project(tmp_path)
    midway(p)
    p.finish_runs()
    code, passes = gate_frames(p, monkeypatch)
    assert code == 0 and len(passes) == 2
    assert {(150, 50), (99, 60), (80, 40)} <= set(cli.SELF_CHECK_SIZES)
    for frames in passes:
        sizes = [(w, h) for w, h, _geom, _found in frames]
        assert sizes[:-1] == list(cli.SELF_CHECK_SIZES) and sizes[-1][1] == cli.SELF_CHECK_TIGHT_ROWS
        # this project's Model usage leaves room for the logo at 99 columns and 150, not at 80
        assert [bool(geom) for _w, _h, geom, _found in frames] == [True, True, False, True]
        assert all(found for _w, _h, geom, found in frames if geom)  # found wherever it is drawn
        tight = frames[-1][2]
        assert tight[1] == LOGO_MIN_COLS  # the tightest width: the smallest logo, which fits there
    nothing_counted, counted = (frames[-1][2] for frames in passes)
    assert nothing_counted == (LOGO_MIN_ROWS, LOGO_MIN_COLS)  # square: the smallest IS a square
    assert counted[0] > LOGO_MIN_ROWS and counted[1] < counted[0] * 2 + 2  # narrower than square


def test_self_check_refuses_a_fault_confined_to_the_narrower_logo(tmp_path, monkeypatch, capsys):
    """The gate's engine has no token counts (nothing is started), so its Model usage is the plan-limit
    rows and the placeholder: 5 rows, whose square logo IS the smallest (12 columns). It never drew a
    logo narrower than square, the path an owner's 99-column window takes with token counts (10 rows,
    17 columns). A fault confined to that path (here: the narrow panel one row short, so the live view
    no longer finds it and it stands still) passed the gate. Review, 2026-10-04."""
    import io

    from rich.console import Console

    import lsw_mission_control.render.usage as usage_mod
    from lsw_mission_control.app import Scroll

    real = usage_mod.logo_panel

    def one_row_short_when_narrow(anim, rows, cols=None):
        narrow = cols is not None and cols < rows * 2 + 2
        return real(anim, rows - 1 if narrow else rows, cols)
    monkeypatch.setattr(usage_mod, "logo_panel", one_row_short_when_narrow)
    p = Project(tmp_path)
    midway(p)
    # Precondition: in the live view (token counts present) at 99x60 the fault leaves the logo standing.
    e = p.engine()
    console = Console(file=io.StringIO(), width=99, height=60, force_terminal=True, color_system="truecolor")
    s = Scroll(150, e.logo)
    s.body, s.net, width = e.safe_frame(console)
    built = e.logo.i
    console.set_alt_screen(True)
    console.print(s)
    assert e.logo.geom is not None and e.logo.geom[1] < e.logo.geom[0] * 2 + 2  # drawn narrower than square
    assert cli.logo_problem(s, console, width, built) == "the logo is drawn where the live view does not find it"
    # The reload gate must refuse it.
    assert in_process_self_check(p) == 1, "the reload gate passed a logo that stands still in a 99-column window"
    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert last.startswith("self-check: the logo is drawn where the live view does not find it (") and \
        last.endswith(", tokens counted)"), last


def test_self_check_draws_the_narrower_logo_where_no_fixed_size_does(tmp_path, monkeypatch, capsys):
    """A status the panel does not know is drawn as it comes, and a long one widens Model usage (98
    columns here), so the logo is square at 150 and absent at 99 and 80, and only the tightest width
    (98 + 17 = 115) draws it narrower than square: the same fault, confined to that path, is refused
    there, in the first pass already (the status row makes Model usage 6 rows before any token is
    counted). Stale plan data widened Model usage this way (" · as of HH:MM") until its rows stopped
    saying how old they are."""
    import lsw_mission_control.render.usage as usage_mod

    real = usage_mod.logo_panel
    monkeypatch.setattr(usage_mod, "logo_panel", lambda anim, rows, cols=None: real(
        anim, rows - 1 if cols is not None and cols < rows * 2 + 2 else rows, cols))
    p = Project(tmp_path)
    midway(p)
    record = json.loads((p.usage_dir / "usage.json").read_text())
    p.usage({**record, "status": "allowed_but_with_a_status_this_dashboard_has_never_seen_before_in_any_of_its_runs"})
    p.finish_runs()
    e = p.engine()  # with token counts, as the live view has them
    geoms = {}
    for width in (150, 99, 80):
        e.frame(testing.record_console(width))
        geoms[width] = e.logo.geom
    assert geoms[150] == (13, 28) and geoms[99] is None and geoms[80] is None
    assert in_process_self_check(p) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == \
        "self-check: the logo is drawn where the live view does not find it (115x60)"


def test_self_check_fails_when_the_logo_is_drawn_where_the_live_view_does_not_find_it(tmp_path, monkeypatch, capsys):
    """The live view finds the logo by its place (its panel closes the dashboard) and turns it
    there. Drawn anywhere else it never turns, and nothing said so: the gate failed only on an
    error in a turn."""
    from rich.console import Group
    from rich.text import Text

    import lsw_mission_control.engine as eng

    p = Project(tmp_path)
    midway(p)
    p.finish_runs()
    real = eng.usage_row
    monkeypatch.setattr(eng, "usage_row", lambda *a: Group(real(*a), Text("a line after the logo")))
    assert in_process_self_check(p) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == \
        "self-check: the logo is drawn where the live view does not find it (150x50)"


def test_self_check_fails_when_a_turn_redraws_nothing(tmp_path, monkeypatch, capsys):
    from lsw_mission_control.render.logo import LogoAnimator

    p = Project(tmp_path)
    midway(p)
    p.finish_runs()
    monkeypatch.setattr(LogoAnimator, "segments", lambda self, rows, cols, i: [])
    assert in_process_self_check(p) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == "self-check: the logo does not turn: a turn drew nothing (150x50)"


def usage_row_until_2026_10_04(console, f, width, logo):
    """usage_row as it was until 2026-10-04, verbatim: the logo only from 100 columns, whatever Model
    usage held."""
    from rich.table import Table

    from lsw_mission_control.render.logo import logo_panel
    from lsw_mission_control.render.usage import usage_panel

    logo_w = None
    if logo is not None and width >= 100:
        probe = usage_panel(f, width - 40)
        rows = len(console.render_lines(probe, console.options.update(width=width - 40, height=None))) - 2
        if rows >= 5:
            logo_w = (rows * 2 + 2) + 4
    if not logo_w:
        return usage_panel(f, width)
    use_w = width - logo_w - 1
    usage = usage_panel(f, use_w)
    rows = len(console.render_lines(usage, console.options.update(width=use_w, height=None))) - 2
    g = Table.grid(padding=(0, 1))
    g.add_column(width=use_w)
    g.add_column(width=(rows * 2 + 2) + 4)
    g.add_row(usage, logo_panel(logo, rows))
    return g


def test_self_check_fails_on_a_logo_left_out_with_room_for_it(tmp_path, monkeypatch, capsys):
    """The owner's 99-column window had no logo (2026-10-04): it was drawn only from 100 columns,
    though Model usage left it room at 99. The gate drew 150 columns only, and a frame with no logo
    at all is not a logo that fails to turn. Under that rule it now fails at 99x60."""
    import lsw_mission_control.engine as eng

    p = Project(tmp_path)
    midway(p)
    p.finish_runs()
    monkeypatch.setattr(eng, "usage_row", usage_row_until_2026_10_04)
    assert in_process_self_check(p) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == \
        "self-check: the logo is left out though Model usage leaves it room (99x60)"


def test_self_check_passes_a_logo_left_out_beside_a_short_model_usage(tmp_path, monkeypatch):
    """Model usage under LOGO_MIN_ROWS rows (no plan data, no token counts yet) has the logo nowhere,
    with room to spare at every size: that is the rule, not a fault. With token counts it is taller,
    and the logo is drawn."""
    p = Project(tmp_path)
    midway(p)
    p.usage(None)
    p.finish_runs()
    code, (nothing_counted, counted) = gate_frames(p, monkeypatch)
    assert code == 0
    assert [(w, h) for w, h, _geom, _found in nothing_counted] == list(cli.SELF_CHECK_SIZES)  # no tightest width
    assert not any(geom for _w, _h, geom, _found in nothing_counted)
    assert all(found for _w, _h, geom, found in counted if geom) and any(geom for _w, _h, geom, _found in counted)


class MeasuredNarrower:
    """Model usage as drawn, but measured `by` columns narrower than it draws: a rule that trusts the
    measurement gives the logo columns Model usage needs."""

    def __init__(self, panel, by: int) -> None:
        self.panel, self.by, self.subtitle = panel, by, panel.subtitle

    def __rich_console__(self, console, options):
        yield self.panel

    def __rich_measure__(self, console, options):
        from rich.measure import Measurement

        m = Measurement.get(console, options, self.panel)
        return Measurement(m.minimum - self.by, m.maximum - self.by)


def test_self_check_fails_on_a_logo_that_cuts_model_usage_short(tmp_path, monkeypatch, capsys):
    """The logo takes only the room Model usage leaves; the gate tells that room by drawing Model usage
    narrower and narrower (cli.usage_fit), not by the measurement the rule reads. Measured 2 columns
    narrower than it draws, Model usage is cut short beside the logo, at the tightest width at least."""
    import lsw_mission_control.render.usage as usage_mod

    real = usage_mod.usage_panel
    monkeypatch.setattr(usage_mod, "usage_panel", lambda f, width: MeasuredNarrower(real(f, width), 2))
    p = Project(tmp_path)
    midway(p)
    p.finish_runs()
    assert in_process_self_check(p) == 1
    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert last.startswith("self-check: the logo cuts Model usage short (") and last.endswith(", tokens counted)"), last


def test_self_check_fails_on_a_model_usage_measured_the_whole_width(tmp_path, monkeypatch, capsys):
    """A plan-data record with no window Model usage reads gave it an empty grid, which rich measures
    the whole width: the logo was left out at every size, beside a Model usage of 9 rows and about 60
    columns (review, 2026-10-04). The gate had no token counts, so that Model usage was 3 rows there
    and the logo not expected. With token counts it is expected, and the gate fails."""
    from rich.table import Table

    import lsw_mission_control.render.usage as usage_mod

    real = usage_mod.usage_panel

    def with_an_empty_grid(f, width):
        panel = real(f, width)
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        panel.renderable.renderables.insert(0, grid)
        return panel
    monkeypatch.setattr(usage_mod, "usage_panel", with_an_empty_grid)
    p = Project(tmp_path)
    midway(p)
    p.usage(UNREADABLE)
    p.finish_runs()
    assert in_process_self_check(p) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == \
        "self-check: the logo is left out though Model usage leaves it room (150x50, tokens counted)"


def test_self_check_draws_every_frame_at_one_instant(tmp_path, monkeypatch):
    """Model usage's width is found once a pass (cli.usage_fit) and held against every frame of the
    pass, and its plan-limit rows count down to their resets: "in 1h00" reads "in 59m" a few seconds
    later, a column narrower, so a frame drawn after those seconds gives the logo a column that Model
    usage, as found, needs, and the gate would refuse a sound edit. It holds the clock still while it
    draws, and gives the caller's clock back after."""
    from lsw_mission_control import util

    p = Project(tmp_path)
    midway(p)
    record = json.loads((p.usage_dir / "usage.json").read_text())
    for window in record["rate_limits"].values():
        window["resets_at"] = NOW + HOUR + 3  # "in 1h00" until NOW + 3, then "in 59m"
    p.usage(record)
    p.finish_runs()
    passed = [0.0]
    util.set_clock(lambda: NOW + passed[0])
    real = cli.usage_fit

    def then_two_seconds_pass(*args):
        found = real(*args)
        passed[0] += 2  # Model usage found at NOW + 2 in the second pass, its frames at NOW + 4
        return found
    monkeypatch.setattr(cli, "usage_fit", then_two_seconds_pass)
    try:
        assert in_process_self_check(p) == 0
        assert util.now() == NOW + 4  # the caller's clock again
    finally:
        testing.freeze(NOW)


def test_cells_at_reads_cells_and_never_fails_past_a_line():
    """What a turn would redraw at a place the live view found: past a line's end there is nothing
    there, which reads as not the logo (the gate says so), never as an IndexError."""
    from rich.segment import Segment

    line = [Segment("ab"), Segment("⢠⡄"), Segment("cd")]
    assert cli.cells_at([line, line], 0, 2, 2, 2) == ["⢠⡄", "⢠⡄"]
    assert cli.cells_at([line], 0, 5, 1, 3) == ["d"]  # cut short at the line's end
    assert cli.cells_at([line], 0, 9, 1, 3) == [""]  # wholly past it
    assert cli.cells_at([line], 0, 2, 2, 2) is None and cli.cells_at([line], 0, -1, 1, 2) is None


def test_self_check_fails_when_the_live_view_finds_the_logo_beside_its_cells(tmp_path, monkeypatch, capsys):
    """Found one column off (its panel's padding changed, say), each turn would draw the logo shifted
    over its own border: the live view must find it at exactly the cells it was drawn in."""
    import lsw_mission_control.app as app

    p = Project(tmp_path)
    midway(p)
    p.finish_runs()
    real = app.Scroll.__rich_console__

    def one_column_off(self, console, options):
        yield from real(self, console, options)
        if self.logo_at:
            top, x, offset, view = self.logo_at
            self.logo_at = (top, x + 1, offset, view)
    monkeypatch.setattr(app.Scroll, "__rich_console__", one_column_off)
    assert in_process_self_check(p) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1] == \
        "self-check: the logo is drawn where the live view does not find it (150x50)"


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
    p.write_config("[network]\ncheck_internet = false\n")  # no request to the internet from a test
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
    assert e.value.code == 0 and "ok    plan: status_plan.json: release 0.1.0" in out and "next no, later no" in out
    plan = json.loads((dot / "status_plan.json").read_text())
    plan["later"] = [{"release": "0.3.0", "items": []}, {"release": "0.4.0", "items": []}]
    (dot / "status_plan.json").write_text(json.dumps(plan))
    with pytest.raises(SystemExit) as e:
        cli.main(["validate", "--project", str(proj)])
    assert e.value.code == 0 and "later 0.3.0 0.4.0" in capsys.readouterr().out  # read, not a misspelt key
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

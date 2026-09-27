from __future__ import annotations

import io

from rich.console import Console

from lsw_mission_control.app import SCROLL_ENV, Scroll, apply_keys, scroll_envs

from scenarios import Project, midway


def test_keys():
    s = Scroll()
    s.page = 10
    rest, quit_ = apply_keys(s, b"jjj\x1b[Bk")
    assert s.offset == 3 and rest == b"" and not quit_
    rest, quit_ = apply_keys(s, b"\x1b[6~ \x1b[5~b")
    assert s.offset == 3
    apply_keys(s, b"G")
    assert s.offset == 10**6
    apply_keys(s, b"\x1bOH")
    assert s.offset == 0
    rest, quit_ = apply_keys(s, b"j\x1b[")  # a partial escape waits for the rest of it
    assert rest == b"\x1b[" and s.offset == 1
    rest, quit_ = apply_keys(s, rest + b"A")
    assert s.offset == 0 and rest == b""
    assert apply_keys(s, b"Q")[1] and apply_keys(s, b"xq")[1]


def test_scroll_offset_from_the_environment(monkeypatch):
    monkeypatch.setenv("OLD_SCROLL", "7")
    assert Scroll(envs=scroll_envs("OLD_SCROLL")).offset == 7
    monkeypatch.setenv(SCROLL_ENV, "3")
    assert Scroll(envs=scroll_envs("OLD_SCROLL")).offset == 3
    monkeypatch.setenv(SCROLL_ENV, "x")
    assert Scroll().offset == 0


def render_scroll(s: Scroll, height=40, width=150) -> list[str]:
    console = Console(file=io.StringIO(), width=width, height=height, force_terminal=True, color_system=None)
    console.print(s)
    return console.file.getvalue().splitlines()


def test_scroll_window_pins_the_bottom_row(tmp_path):
    p = Project(tmp_path)
    midway(p)
    e = p.engine()
    s = Scroll(150, e.logo)
    console = Console(file=io.StringIO(), width=150, height=40)
    s.body, s.crit, _ = e.safe_frame(console)
    lines = render_scroll(s)
    assert len(lines) == 40 and lines[-1].startswith("▼") and "safe to switch networks" in lines[-1]
    s.offset = 7
    lines = render_scroll(s)
    assert lines[-1].startswith("▲7 ▼") and s.page == 37
    s.offset = 10**6
    lines = render_scroll(s)
    assert lines[-1].startswith("▲") and "▼" not in lines[-1].split("Key")[0]
    total = s.offset + 39
    assert s.logo_at is not None and s.logo_at[0] == total - e.logo.geom[0] - 1
    assert "λ∿ 2026" in "".join(lines)


def test_logo_spins_on_the_alt_screen(tmp_path):
    p = Project(tmp_path)
    midway(p)
    e = p.engine()
    console = Console(file=io.StringIO(), width=150, height=80, force_terminal=True, color_system="truecolor")
    s = Scroll(150, e.logo)
    s.body, s.crit, _ = e.safe_frame(console)
    console.set_alt_screen(True)
    console.print(s)
    before = len(console.file.getvalue())
    s.spin(console)
    written = console.file.getvalue()[before:]
    assert e.logo.i == 1 and not e.logo.off and 0 < len(written) < before / 4  # only the logo's cells
    e.logo.geom = None
    s.spin(console)
    assert e.logo.i == 2


def test_a_failing_body_shows_the_error_panel():
    class Boom:
        def __rich_console__(self, console, options):
            raise ValueError("bad body")
    s = Scroll()
    s.body = Boom()
    text = "\n".join(render_scroll(s, height=10, width=80))
    assert "Mission control error" in text and "ValueError: bad body" in text

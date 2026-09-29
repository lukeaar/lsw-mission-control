"""The live view: a scrollable window onto the dashboard, the keys, the main loop; and --once."""

from __future__ import annotations

import os
import re
import sys
import threading
import time
import traceback

from rich.console import Console
from rich.live import Live
from rich.text import Text

from lsw_mission_control.engine import Engine
from lsw_mission_control.net import NetState
from lsw_mission_control.render.frame import bottom_line
from lsw_mission_control.render.logo import LOGO_FPS, LogoAnimator
from lsw_mission_control.render.widgets import panel
from lsw_mission_control.theme import C

SCROLL_ENV = "LSW_MC_SCROLL"  # carries the scroll offset across a self-reload
DATA_EVERY_S = 5.0
FIRST_FRAME_AFTER_S = 1.5
ONCE_WAIT_S = 25.0


def scroll_envs(legacy: str | None) -> tuple[str, ...]:
    return (SCROLL_ENV, legacy) if legacy else (SCROLL_ENV,)


class Scroll:
    """A terminal-high window onto the dashboard, which is often taller than the terminal.

    The wheel (which a terminal turns into arrow keys on the alternate screen) and the keys move
    this window; the offset is clamped at render time, when the height is known. The Key and
    network row is pinned as the last terminal row, outside the window, so it is on screen at
    every offset and appears once; its left end shows how many rows are above and below.
    """

    def __init__(self, max_width: int = 150, logo: LogoAnimator | None = None, envs: tuple[str, ...] = (SCROLL_ENV,)) -> None:
        self.body = None
        self.error = ""  # the traceback of the last draw that failed as a whole ("" when it worked)
        self.logo = logo
        self.logo_at = None
        self.net = NetState()
        self.page = 20
        self.max_width = max_width
        self.offset = 0
        for name in envs:
            if os.environ.get(name) is not None:
                try:
                    self.offset = int(os.environ.get(name, "0"))
                except ValueError:
                    self.offset = 0
                break

    def __rich_console__(self, console, options):
        from rich.segment import Segment

        height = options.height or console.height
        width = min(options.max_width, self.max_width)
        opts = options.update(width=width, height=None)
        try:
            lines = console.render_lines(self.body, opts, pad=True)
            self.error = ""
        except Exception as e:  # noqa: BLE001 — a renderable that fails must not end the view
            self.error = traceback.format_exc()  # the reload gate refuses on it
            lines = console.render_lines(panel(Text(f"{type(e).__name__}: {e}"[:300], style=f"bold {C.RED_SOFT}"),
                                               "Mission control error"), opts, pad=True)
        total = len(lines)
        view = max(1, height - 1)
        pos = ""
        if total <= view:
            self.offset = 0
            window = lines
        else:
            self.page = max(1, view - 2)
            self.offset = min(max(self.offset, 0), total - view)
            window = lines[self.offset:self.offset + view]
            above, below = self.offset, total - view - self.offset
            pos = " ".join(x for x in (f"▲{above}" if above else "", f"▼{below}" if below else "") if x)
        self.logo_at = None
        geom = self.logo.geom if self.logo is not None else None
        if geom:
            rows = geom[0]
            top = total - rows - 1  # the logo panel closes the body; its content ends one row above
            border = "".join(seg.text for seg in lines[top - 1]) if 0 < top <= total else ""
            box_x = border.rfind("╭")
            if box_x > 0:
                self.logo_at = (top, box_x + 2, self.offset, view)
        for line in window:
            yield from line
            yield Segment.line()
        yield from console.render_lines(bottom_line(width, self.net.crit, pos, self.net.conn), opts.update(height=1),
                                        pad=True)[0]

    def spin(self, console) -> None:
        """Advance the logo one frame and redraw only its cells: a few hundred bytes, not a
        whole screen. It runs on the main loop, so a frozen dashboard stops it turning."""
        logo = self.logo
        if logo is None:
            return
        logo.advance()
        if logo.off or not self.logo_at or not logo.geom:
            return
        top, x, offset, view = self.logo_at
        rows, cols = logo.geom
        try:
            segs = logo.segments(rows, cols, logo.i)
            shown = [(top + r - offset, line) for r, line in enumerate(segs) if 0 <= top + r - offset < view]
            if shown:  # the visible rows are contiguous: one write per frame
                console.update_screen_lines([line for _y, line in shown], x, shown[0][0])
        except Exception:  # noqa: BLE001 — decoration only: stop spinning, never break the view
            logo.off = True


KEYS = re.compile(rb"\x1b\[[0-9;]*[A-Za-z~]|\x1bO[A-Za-z]|.", re.S)
PARTIAL = re.compile(rb"\x1b(\[[0-9;]*|O)?$")


def apply_keys(scroll: Scroll, buf: bytes) -> tuple[bytes, bool]:
    """Move the window for every whole key in `buf`: (the unfinished escape sequence left over,
    whether q was pressed)."""
    quit_ = False
    tail = PARTIAL.search(buf)
    todo, rest = (buf[:tail.start()], buf[tail.start():]) if tail else (buf, b"")
    for m in KEYS.finditer(todo):
        k = m.group()
        if k in (b"\x1b[A", b"\x1bOA", b"k"):
            scroll.offset -= 1
        elif k in (b"\x1b[B", b"\x1bOB", b"j"):
            scroll.offset += 1
        elif k in (b"\x1b[5~", b"b"):
            scroll.offset -= scroll.page
        elif k in (b"\x1b[6~", b" "):
            scroll.offset += scroll.page
        elif k in (b"\x1b[H", b"\x1bOH", b"\x1b[1~", b"g"):
            scroll.offset = 0
        elif k in (b"\x1b[F", b"\x1bOF", b"\x1b[4~", b"G"):
            scroll.offset = 10**6
        elif k in (b"q", b"Q"):
            quit_ = True
    return rest, quit_


def keys_loop(scroll: Scroll, wake: threading.Event, quit_flag: threading.Event) -> None:
    """Read keys (echo is off, so nothing reaches the screen) and move the window."""
    fd = sys.stdin.fileno()
    buf = b""
    while True:
        try:
            data = os.read(fd, 256)
        except OSError:
            return
        if not data:
            return
        buf, quit_ = apply_keys(scroll, buf + data)
        if quit_:
            quit_flag.set()
        wake.set()  # a key redraws at once, not at the next data refresh


def run_once(engine: Engine, console: Console, wait_s: float = ONCE_WAIT_S) -> int:
    """One snapshot: wait (up to 25 s) for the sources, print, and exit 1 (traceback on stderr)
    when the frame or a plugin failed."""
    deadline = time.time() + wait_s
    while time.time() < deadline and not engine.ready():
        time.sleep(0.5)
    console.print(engine.render(console))
    errors = engine.errors_for_once()
    if errors:
        sys.stderr.write(errors)
        return 1
    return 0


def run_live(engine: Engine, console: Console, reloader=None) -> None:
    time.sleep(FIRST_FRAME_AFTER_S)
    scroll = Scroll(engine.cfg.layout.max_width, engine.logo, scroll_envs(engine.cfg.compat.legacy_scroll_env))
    wake, quit_flag = threading.Event(), threading.Event()
    scroll.body, scroll.net, _w = engine.safe_frame(console)  # the first frame is ready before Live starts
    # Keys drive the scroll window; the terminal must not echo them (the wheel's arrow
    # keys used to print as escape codes) or buffer them for the shell after we exit.
    saved = None
    if sys.stdin.isatty():
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)  # no echo, no line buffering; Ctrl+C still interrupts
        threading.Thread(target=keys_loop, args=(scroll, wake, quit_flag), daemon=True).start()

    def restore() -> None:
        if saved is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)

    try:
        with Live(scroll, console=console, screen=True, auto_refresh=False) as live:
            live.refresh()
            next_data = time.time() + DATA_EVERY_S
            tick = 1.0 / LOGO_FPS
            while not quit_flag.is_set():
                woke = wake.wait(timeout=max(0.0, min(tick, next_data - time.time())))
                wake.clear()
                if quit_flag.is_set():
                    break
                if not woke and time.time() < next_data:
                    scroll.spin(console)  # the only work on a plain tick: a few cells redrawn
                    continue
                if time.time() >= next_data:
                    # An edit to the engine, a plugin, the config or the launcher reloads the view,
                    # once it compiles and passes the self-check; a broken edit is not loaded (the
                    # title says so) and the next edit is tried again. The offset rides along.
                    if reloader is not None:
                        decision = reloader.poll()
                        engine.reload_note = reloader.note
                        if decision == "go":
                            live.stop()
                            restore()
                            reloader.exec(scroll.offset)
                    scroll.body, scroll.net, _w = engine.safe_frame(console)
                    next_data = time.time() + DATA_EVERY_S
                live.refresh()
    except KeyboardInterrupt:
        pass
    finally:
        restore()

"""The logo beside Model usage: an SVG path ([logo] in the config; the LSW logo by default) drawn
in braille (2x4 dots per character), turning slowly. It turns on the MAIN loop, so a frozen
dashboard visibly stops turning."""

from __future__ import annotations

import math

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from lsw_mission_control.config import LogoCfg
from lsw_mission_control.theme import C

LOGO_FRAMES = 48  # one turn = LOGO_FRAMES / LOGO_FPS seconds
LOGO_FPS = 8
# The smallest logo: LOGO_MIN_ROWS rows (4 dots each) high and square, LOGO_MIN_COLS columns (twice
# as many as rows, plus 2). Model usage must be at least that tall to have the logo beside it, and
# leave it at least that many columns of drawing (render/usage.py).
LOGO_MIN_ROWS = 5
LOGO_MIN_COLS = LOGO_MIN_ROWS * 2 + 2
LOGO_PANEL_PAD = 4  # a logo panel is its drawing and, each side, a border and a space


def logo_points(segs, step=0.25):
    pts = []
    for seg in segs:
        if seg[0] == "L":
            (x0, y0), (x1, y1) = seg[1], seg[2]
            n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
            pts += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]
        else:
            (x0, y0), (cx, cy), (x1, y1) = seg[1], seg[2], seg[3]
            n = 400
            for i in range(n + 1):
                t = i / n
                a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
                pts.append((a * x0 + b * cx + c * x1, a * y0 + b * cy + c * y1))
    return pts


def logo_cells(pts, split_x: float, rows: int, cols: int, stroke: float = 6.0, angle: float = 0.0):
    """The logo on a rows x cols grid of braille characters: [(char, 'L'|'R'|None), ...] per row.
    Each cell is 2x4 dots, square on a normal terminal, so the drawing keeps its proportions."""
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    # Spin about the vertical axis through the drawing's centre, as if the logo stood on a
    # table turning under it; a little perspective makes the near edge larger. The scale is
    # fixed from the unturned drawing so the size never pumps.
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    ca, sa, depth = math.cos(angle), math.sin(angle), 320.0
    pad = stroke
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    W, H = cols * 2, rows * 4
    s = min(W / (x1 - x0), H / (y1 - y0))
    ox = (W - (x1 - x0) * s) / 2 - x0 * s
    oy = (H - (y1 - y0) * s) / 2 - y0 * s
    r = max(0.6, stroke * s / 2)
    lit = {}
    for (px, py) in pts:
        z = (px - cx) * sa
        f = depth / (depth + z)
        rx, ry = cx + (px - cx) * ca * f, cy + (py - cy) * f
        dx, dy = rx * s + ox, ry * s + oy
        for gx in range(int(dx - r - 1), int(dx + r + 2)):
            for gy in range(int(dy - r - 1), int(dy + r + 2)):
                if 0 <= gx < W and 0 <= gy < H and (gx + 0.5 - dx) ** 2 + (gy + 0.5 - dy) ** 2 <= r * r:
                    side = "L" if px < split_x else "R"
                    lit.setdefault((gx, gy), side)
    bits = {(0, 0): 1, (0, 1): 2, (0, 2): 4, (1, 0): 8, (1, 1): 16, (1, 2): 32, (0, 3): 64, (1, 3): 128}
    out = []
    for row in range(rows):
        line = []
        for col in range(cols):
            code, sides = 0, []
            for (bx, by), bit in bits.items():
                k = (col * 2 + bx, row * 4 + by)
                if k in lit:
                    code |= bit
                    sides.append(lit[k])
            side = None if not sides else ("L" if sides.count("L") >= sides.count("R") else "R")
            line.append((chr(0x2800 + code) if code else " ", side))
        out.append(line)
    return out


class LogoAnimator:
    """The turn: its frame index, the frames computed per size, and where the panel was drawn."""

    def __init__(self, cfg: LogoCfg) -> None:
        self.cfg = cfg
        self.i = 0
        self.cache: dict = {}
        self.geom: tuple[int, int] | None = None  # (rows, cols) of the panel in the last frame
        self.off = False  # set for good on any drawing error: decoration must never break the view
        self._pts = None

    def points(self):
        if self._pts is None:
            self._pts = logo_points(self.cfg.segments)
        return self._pts

    def frame(self, rows: int, cols: int, i: int):
        """Frame i of the turn, computed once per size and kept. On a grid narrower than square the
        drawing fits the width, about cols / 2 rows high, and gets the stroke of a square grid of
        cols / 2 rows, whose drawing is that size (not of a square panel cols wide: (cols - 2) / 2
        rows)."""
        frames = self.cache.setdefault((rows, cols), {})
        if i not in frames:
            frames[i] = logo_cells(self.points(), self.cfg.split_x, rows, cols, stroke=max(6.0, 56.0 / min(rows, cols / 2)),
                                   angle=2 * math.pi * i / LOGO_FRAMES)
        return frames[i]

    def segments(self, rows: int, cols: int, i: int):
        """The frame as screen lines of Segments, background included (they are written raw)."""
        from rich.segment import Segment
        from rich.style import Style

        left, right = self.cfg.colours
        styles = {"L": Style(color=left, bgcolor=C.BG), "R": Style(color=right, bgcolor=C.BG), None: Style(bgcolor=C.BG)}
        return [[Segment(ch, styles[side]) for ch, side in row] for row in self.frame(rows, cols, i)]

    def advance(self) -> None:
        self.i = (self.i + 1) % LOGO_FRAMES


def logo_panel(anim: LogoAnimator, rows: int, cols: int | None = None) -> Panel:
    """A panel `rows` high holding the logo, at the current frame of its turn (the main loop
    advances the turn between full refreshes): square (twice as many columns as rows, plus 2)
    unless given fewer `cols`, when the drawing shrinks to fit them, centred in the same height."""
    cols = rows * 2 + 2 if cols is None else cols
    anim.geom = (rows, cols)
    left, right = anim.cfg.colours
    lines = []
    for row in anim.frame(rows, cols, anim.i):
        t = Text()
        for ch, side in row:
            t.append(ch, style=left if side == "L" else right if side == "R" else None)
        lines.append(t)
    caption = anim.cfg.caption
    return Panel(Group(*lines), box=box.ROUNDED, border_style=C.BORDER, padding=(0, 1), style=f"on {C.BG}",
                 width=cols + LOGO_PANEL_PAD, subtitle=Text(caption, style=anim.cfg.caption_style or C.FAINT) if caption else None,
                 subtitle_align="right")

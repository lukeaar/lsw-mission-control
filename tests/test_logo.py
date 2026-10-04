from __future__ import annotations

import copy
import math

import pytest
from rich.console import Group
from rich.table import Table

from lsw_mission_control import testing
from lsw_mission_control.config import DEFAULT_LOGO_PATH, LogoCfg, parse_logo_path
from lsw_mission_control.render.logo import LOGO_FRAMES, LOGO_MIN_COLS, LOGO_PANEL_PAD, LogoAnimator, logo_cells, logo_panel, logo_points
from lsw_mission_control.render.usage import usage_panel, usage_row

from golden_util import check
from scenarios import UNREADABLE, Project, midway

# The drawing the dashboard has always shown, in its own 150x150 units.
LSW_SEGMENTS = (("L", (17, 105), (57, 25)), ("Q", (57, 25), (71, 65), (85, 105)),
                ("Q", (85, 105), (97, 145), (109, 105)), ("Q", (109, 105), (121, 65), (133, 105)))


def default_logo() -> LogoCfg:
    return LogoCfg(segments=parse_logo_path(DEFAULT_LOGO_PATH))


def test_the_default_path_is_the_lsw_logo():
    assert parse_logo_path(DEFAULT_LOGO_PATH) == LSW_SEGMENTS


@pytest.mark.parametrize("frame", [0, 12, 24, 36])
def test_frames(update_golden, frame):
    anim = LogoAnimator(default_logo())
    rows = 11
    cells = anim.frame(rows, rows * 2 + 2, frame)
    text = "\n".join("".join(ch for ch, _side in row) for row in cells) + "\n"
    sides = "\n".join("".join(side or "." for _ch, side in row) for row in cells) + "\n"
    check(f"logo/frame-{frame}.txt", text + sides, update_golden)
    # computed once per size and kept
    assert anim.frame(rows, rows * 2 + 2, frame) is cells


def test_cells_match_a_direct_computation():
    pts = logo_points(LSW_SEGMENTS)
    anim = LogoAnimator(default_logo())
    assert anim.frame(9, 20, 5) == logo_cells(pts, 85.0, 9, 20, stroke=max(6.0, 56.0 / 9), angle=2 * math.pi * 5 / LOGO_FRAMES)
    # Narrower than square, the drawing fits the width, about cols / 2 rows high: its stroke is the
    # one of a square grid of cols / 2 rows, whose drawing is that size, not of the panel's height.
    assert anim.frame(10, 17, 5) == logo_cells(pts, 85.0, 10, 17, stroke=max(6.0, 56.0 / 8.5),
                                               angle=2 * math.pi * 5 / LOGO_FRAMES)


def test_a_panel_narrower_than_square():
    anim = LogoAnimator(default_logo())
    plain, _ = testing.render_text(logo_panel(anim, 10, 17), 40)
    lines = plain.splitlines()
    assert anim.geom == (10, 17) and len(lines) == 12  # as tall as asked: the live view finds it by that
    assert {len(line.rstrip()) for line in lines} == {21} and "λ∿ 2026" in lines[-1]
    assert any(ch != " " for line in lines[1:-1] for ch in line[2:19])  # something is drawn


def usage_row_at(e, width: int):
    """(the Model usage row drawn at `width`, plain; the logo's (rows, cols), None when not drawn)."""
    f = e.build_frame(width)
    e.logo.geom = None
    row = usage_row(testing.record_console(width), f, width, e.logo)
    return testing.render_text(row, width)[0], e.logo.geom, f


def test_model_usage_keeps_its_width_and_the_logo_gets_the_rest(tmp_path, update_golden):
    """The logo was drawn only from 100 columns, and there it clipped Model usage's rows ("resets
    Thu 14:13 ·…"); one column narrower it was not drawn at all. Model usage now keeps the width
    its rows measure, and the logo takes the rest of the row: square when there is room, narrower
    (the drawing shrinks, the panel keeps Model usage's height) when there is not, and left out only
    when not even the smallest (LOGO_MIN_ROWS high, square) fits beside an unclipped Model usage."""
    p = Project(tmp_path)
    midway(p)
    e = p.engine()
    smallest = LOGO_MIN_COLS
    geoms = {}
    for width in range(60, 151):
        plain, geom, f = usage_row_at(e, width)
        geoms[width] = geom
        if geom is None:
            # not drawn: even the smallest logo would have clipped Model usage
            beside = testing.render_text(usage_panel(f, width), width - (smallest + LOGO_PANEL_PAD) - 1)[0]
            assert "…" in beside, width
            continue
        rows, cols = geom
        assert "…" not in plain, width  # Model usage is never clipped for the logo
        assert len(plain.splitlines()) == rows + 2, width  # the logo panel is exactly as tall
        assert smallest <= cols <= rows * 2 + 2, width
        if cols < rows * 2 + 2:
            # narrower than square: the logo took all the room Model usage left, no more
            tighter = testing.render_text(usage_panel(f, width), width - (cols + LOGO_PANEL_PAD) - 1 - 1)[0]
            assert "…" in tighter, width
    assert geoms[99] is not None and geoms[99][1] < geoms[99][0] * 2 + 2  # 99 columns: a narrower logo
    assert geoms[150] == (12, 26)  # room to spare: square, as it always was
    assert geoms[min(w for w, g in geoms.items() if g)][1] == smallest  # it appears at its smallest
    check("usage/row-99.txt", usage_row_at(e, 99)[0], update_golden)


def test_logo_beside_a_model_usage_with_no_plan_limit_row(tmp_path):
    """Model usage here is its token tables (10 rows, under 60 columns): the logo has room for its
    square at 150, 120 and 99, and for a narrower one at 80. usage_row's Measurement of Model usage
    was the full width offered (150 at 150, 99 at 99), so the logo was left out at every width (before
    the logo took the room Model usage leaves, it was drawn from 100 columns). Review, 2026-10-04."""
    p = Project(tmp_path)
    midway(p)
    p.usage(UNREADABLE)
    e = p.engine()
    geoms = {}
    for width in (150, 120, 99, 80):
        f = e.build_frame(width)
        e.logo.geom = None
        usage_row(testing.record_console(width), f, width, e.logo)
        geoms[width] = e.logo.geom
    assert geoms == {150: (10, 22), 120: (10, 22), 99: (10, 22), 80: (10, 17)}
    # the empty grid usage_panel leaves out drew nothing: Model usage draws exactly as it did with it
    now = usage_panel(e.build_frame(150), 150)
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(no_wrap=True, width=12)
    grid.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
    was = copy.copy(now)
    was.renderable = Group(grid, *now.renderable.renderables)
    for width in (150, 99, 60):
        assert testing.render_text(now, width) == testing.render_text(was, width), width


def test_panel_caption_and_colours(update_golden):
    anim = LogoAnimator(default_logo())
    plain, styled = testing.render_text(logo_panel(anim, 8), 40)
    assert anim.geom == (8, 18) and "λ∿ 2026" in plain
    assert "38;2;162;33;217" in styled and "38;2;217;109;33" in styled  # #A221D9 and #D96D21
    custom = LogoAnimator(LogoCfg(segments=parse_logo_path("M 10 10 L 140 140"), colours=("#112233", "#445566"),
                                  caption="acme", split_x=75.0))
    plain, styled = testing.render_text(logo_panel(custom, 6), 40)
    assert "acme" in plain and "λ∿ 2026" not in plain and "38;2;17;34;51" in styled
    none = LogoAnimator(LogoCfg(segments=LSW_SEGMENTS, caption=""))
    assert "╰" + "─" * 16 + "╯" in testing.render_text(logo_panel(none, 6), 40)[0]


def test_segments_carry_the_background():
    anim = LogoAnimator(default_logo())
    lines = anim.segments(6, 14, 0)
    assert len(lines) == 6 and all(len(line) == 14 for line in lines)
    assert all(seg.style.bgcolor is not None for line in lines for seg in line)
    anim.advance()
    assert anim.i == 1
    anim.i = LOGO_FRAMES - 1
    anim.advance()
    assert anim.i == 0

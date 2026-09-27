from __future__ import annotations

import math

import pytest

from lsw_mission_control import testing
from lsw_mission_control.config import DEFAULT_LOGO_PATH, LogoCfg, parse_logo_path
from lsw_mission_control.render.logo import LOGO_FRAMES, LogoAnimator, logo_cells, logo_panel, logo_points

from golden_util import check

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

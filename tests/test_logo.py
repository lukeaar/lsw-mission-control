from __future__ import annotations

import copy
import datetime as dt
import json
import math

import pytest
from rich.console import Group
from rich.table import Table

from lsw_mission_control import cli, testing
from lsw_mission_control.config import DEFAULT_LOGO_PATH, LogoCfg, parse_logo_path
from lsw_mission_control.render.logo import LOGO_FRAMES, LOGO_MIN_COLS, LOGO_PANEL_PAD, LogoAnimator, logo_cells, logo_panel, logo_points
from lsw_mission_control.render.usage import usage_panel, usage_row
from lsw_mission_control.render.widgets import METER_W

from conftest import NOW
from golden_util import check
from scenarios import HOUR, MIN, UNREADABLE, Project, midway

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
    """(the Model usage row drawn at `width`, plain; the logo's (rows, cols), None when not drawn; the
    frame; the row)."""
    f = e.build_frame(width)
    e.logo.geom = None
    row = usage_row(testing.record_console(width), f, width, e.logo)
    return testing.render_text(row, width)[0], e.logo.geom, f, row


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
        plain, geom, f, row = usage_row_at(e, width)
        geoms[width] = geom
        if geom is None:
            # not drawn: even the smallest logo would have clipped Model usage
            beside = testing.render_text(usage_panel(f, width), width - (smallest + LOGO_PANEL_PAD) - 1)[0]
            assert "…" in beside, width
            # and the reload gate, which tells room by drawing Model usage narrower, agrees
            assert not cli.logo_left_out_with_room(testing.record_console(width), row, width), width
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
    assert geoms[150] == (7, 16)  # room to spare: square, beside Model usage's tables side by side (7 rows)
    assert geoms[min(w for w, g in geoms.items() if g)][1] == smallest  # it appears at its smallest
    check("usage/row-99.txt", usage_row_at(e, 99)[0], update_golden)


def test_logo_beside_a_model_usage_with_no_plan_limit_row(tmp_path):
    """Model usage here is its token tables (10 rows, under 60 columns; 5 side by side, at 150): the
    logo has room for its square at 150, 120 and 99, and for a narrower one at 80. usage_row's Measurement of Model usage
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
    assert geoms == {150: (5, 12), 120: (10, 22), 99: (10, 22), 80: (10, 17)}
    # the empty grid usage_panel leaves out drew nothing: Model usage draws exactly as it did with it
    now = usage_panel(e.build_frame(150), 150)
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(no_wrap=True, width=12)
    grid.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
    was = copy.copy(now)
    was.renderable = Group(grid, *now.renderable.renderables)
    for width in (150, 99, 60):
        assert testing.render_text(now, width) == testing.render_text(was, width), width


def test_model_usage_fills_a_wide_row_with_its_tables_side_by_side(tmp_path):
    """A wide window left Model usage's right half empty (owner, 2026-10-25): its meters were METER_W
    cells whatever the width, and the by-model table sat under the token table. Drawn wider than it
    needs, its meters take the room to spare, all one length (the shares line up), and the by-model
    table goes beside the token table, at the right end, once both fit; narrower, it is as it was.
    Model usage measures as it did, so the logo's room is decided as before (the sweep above)."""
    p = Project(tmp_path)
    midway(p)
    e = p.engine()
    beside = {w: any("tokens" in line and "by model" in line for line in usage_row_at(e, w)[0].splitlines())
              for w in range(60, 201)}
    first = min(w for w, b in beside.items() if b)
    assert first == 136 and all(beside[w] for w in range(first, 201))  # beside a square logo, 16 columns
    f = e.build_frame(150)
    need = cli.usage_fit(testing.record_console(150), usage_panel(f, 150), 150)[1]
    for width in (need, 100, 150):
        lines = testing.render_text(usage_panel(f, width), width)[0].splitlines()
        limits = [line for line in lines if line.startswith(("│ 5-hour", "│ weekly"))]
        meters = {line.count("▰") + line.count("▱") for line in limits}
        assert len(limits) == 2 and len(meters) == 1, width  # one length
        assert (meters.pop() == METER_W) == (width == need), width  # longer only with room to spare
        assert max(len(line[:-1].rstrip()) for line in limits) == width - 2, width  # to the padding
        side = [line for line in lines if "tokens" in line and "by model" in line]
        assert len(side) == (width == 150), width
        if side:  # by model ends where the longest limit row does
            assert side[0][:-1].rstrip().endswith("share · 7 days") and len(side[0][:-1].rstrip()) == width - 2


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


LIMITS = {"five_hour": {"used_percentage": 42.0, "resets_at": NOW + 2 * HOUR},
          "seven_day": {"used_percentage": 81.0, "resets_at": NOW + 3 * 86400}}
PLAN_DATA = {
    "fresh": {"at": NOW - 3 * MIN, "rate_limits": LIMITS, "source": "probe"},
    "stale": {"at": NOW - 2 * HOUR, "rate_limits": LIMITS, "source": "probe"},
    "stale-of-unknown-age": {"rate_limits": LIMITS, "source": "probe"},
    "reset-passed": {"at": NOW - 6 * HOUR, "rate_limits": {
        "five_hour": {"used_percentage": 97.0, "resets_at": NOW - HOUR}, "seven_day": LIMITS["seven_day"]}},
    # on an earlier day: the reset reads its weekday too ("reset Sun 12:13"), the widest row there is
    "reset-passed-yesterday": {"at": NOW - 30 * HOUR, "rate_limits": {
        "five_hour": {"used_percentage": 97.0, "resets_at": NOW - 26 * HOUR}, "seven_day": LIMITS["seven_day"]}},
    "both-reset-on-earlier-days": {"at": NOW - 8 * 86400, "rate_limits": {
        "five_hour": {"used_percentage": 97.0, "resets_at": NOW - 7 * 86400 - HOUR},
        "seven_day": {"used_percentage": 99.0, "resets_at": NOW - 2 * 86400}}},
    "warning-credits": {"at": NOW - MIN, "rate_limits": LIMITS, "status": "allowed_warning", "overage": True},
    "rejected-credits": {"at": NOW - MIN, "rate_limits": LIMITS, "status": "rejected", "overage": True},
    "unreadable": UNREADABLE,
    # a plan without a 5-hour window may send it with no share: it drew "5-hour 0%" with no reset
    "five-hour-without-a-share": {"at": NOW - 3 * MIN, "rate_limits": {
        "five_hour": {"used_percentage": None, "resets_at": None}, "seven_day": LIMITS["seven_day"]}, "source": "probe"},
}


@pytest.mark.parametrize("state", sorted(PLAN_DATA))
def test_the_logo_shows_at_99_columns_whatever_the_plan_data_says(tmp_path, state):
    """At 99 columns the logo was left out while the plan data was stale: each limit row grew by
    " · as of HH:MM", and Model usage, 91 columns wide, left the logo 3. Stale rows no longer grow (the
    subtitle says how old the data is, in amber), so the logo shows in every state of the plan data,
    beside a Model usage it never cuts short. So it does after a reset on an earlier day with no plan
    data since: that row read "· no plan data since" (Model usage 85, the logo from 102)."""
    p = Project(tmp_path)
    midway(p)
    p.usage(PLAN_DATA[state])
    e = p.engine()
    plain, geom, f, row = usage_row_at(e, 99)
    assert geom is not None and geom[1] >= LOGO_MIN_COLS, geom
    assert "…" not in plain and "as of" not in plain.split("╰")[0]  # no row carries the age
    assert not cli.logo_left_out_with_room(testing.record_console(99), row, 99)


NO_SHARE = {"null": {"used_percentage": None, "resets_at": None}, "missing": {"resets_at": NOW + HOUR},
            "reset-passed": {"used_percentage": None, "resets_at": NOW - HOUR}, "bool": {"used_percentage": True},
            "text": {"used_percentage": "n/a"}, "nan": {"used_percentage": math.nan}}


@pytest.mark.parametrize("shape", sorted(NO_SHARE))
def test_a_window_with_no_share_draws_no_row(tmp_path, shape):
    """A plan with no 5-hour window may still send the window with no share in it: num() read that
    as 0, and Model usage drew "5-hour 0%", an unused limit
    that does not exist (or "?% reset … no data since" once its reset had passed). Such a window
    draws no row, as a window that is not sent at all; the weekly row is drawn as ever."""
    p = Project(tmp_path)
    midway(p)
    p.usage({"at": NOW - 3 * MIN, "rate_limits": {"five_hour": NO_SHARE[shape], "seven_day": LIMITS["seven_day"]},
             "source": "probe"})
    f = p.engine().build_frame(150)
    lines = testing.render_text(usage_panel(f, 150), 150)[0].splitlines()
    assert not [line for line in lines if line.startswith("│ 5-hour")]
    weekly = [line for line in lines if line.startswith("│ weekly")]
    assert len(weekly) == 1 and " 81%" in weekly[0] and "resets" in weekly[0]


def test_the_credits_row_counts_the_spend_files_against_the_balance(tmp_path):
    """[usage.credits]: a prepaid balance the plan limits never see (the Console's API credits), drawn right
    under them and like them: the share of the balance its spend files record, the dollars, and the day it
    expires. A file that is not a JSON object with total_cost_usd counts nothing; one two globs match, once.
    With no plan data the row still draws, under "limits: no data yet"; past its day it reads "expired"."""
    spend = tmp_path / "spend"
    spend.mkdir()
    for name, body in (("a.json", {"total_cost_usd": 1.5}), ("b.json", {"total_cost_usd": 2.25, "result": "{}"}),
                       ("c.json", [1, 2]), ("d.json", {"is_error": True})):
        (spend / name).write_text(json.dumps(body))
    (spend / "e.json").write_text("not json")
    p = Project(tmp_path)

    def lines(until: str) -> list[str]:
        p.write_config(f'[usage.credits]\nusd = 200\nuntil = "{until}"\nspend = ["{spend}/*.json", "{spend}/a.json"]\n')
        return testing.render_text(usage_panel(p.engine().build_frame(150), 150), 150)[0].splitlines()

    midway(p)
    p.usage(PLAN_DATA["fresh"])
    day = dt.date.fromtimestamp(NOW) + dt.timedelta(days=26)
    rows = lines(day.isoformat())
    names = [line[2:9].rstrip() for line in rows if line.startswith("│ ") and line[2:9].strip()]
    api = [line for line in rows if line.startswith("│ API")]
    assert names.index("API") == names.index("weekly") + 1
    assert len(api) == 1 and "  2%" in api[0] and f"$3.75 of $200 · until {day.day} {day.strftime('%b')}" in api[0]
    meters = {line.count("▰") + line.count("▱") for line in rows if line.startswith(("│ API", "│ weekly", "│ 5-hour"))}
    assert len(meters) == 1  # one length with the plan limits'
    p.usage(None)
    past = dt.date.fromtimestamp(NOW) - dt.timedelta(days=1)
    rows = lines(past.isoformat())
    assert any(line.startswith("│ plan") and "no data yet" in line for line in rows)
    assert any(line.startswith("│ API") and f"expired {past.day} {past.strftime('%b')}" in line for line in rows)


def test_stale_plan_data_keeps_model_usage_as_wide_as_fresh_and_says_so_in_amber(tmp_path):
    """The stale rows draw as wide as the fresh ones; the subtitle, faint while the data is fresh,
    is amber once it is stale, and names an age it does not know."""
    from lsw_mission_control.render.usage import usage_panel
    from lsw_mission_control.theme import C

    def amber_sgr() -> str:
        h = C.AMBER.lstrip("#")
        return "38;2;" + ";".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))

    p = Project(tmp_path)
    midway(p)
    widths, subtitles = {}, {}
    for state in ("fresh", "stale", "stale-of-unknown-age"):
        p.usage(PLAN_DATA[state])
        e = p.engine()
        console = testing.record_console(150)
        widths[state] = cli.usage_fit(console, usage_panel(e.build_frame(150), 150), 150)[1]
        plain, styled = testing.render_text(usage_panel(e.build_frame(150), 150), 150)
        bottom = styled.splitlines()[-1]
        subtitles[state] = (plain.splitlines()[-1], amber_sgr() in bottom)
    assert widths["stale"] == widths["fresh"] and widths["stale-of-unknown-age"] <= widths["fresh"]
    assert "plan data as of 14:10 · 3m ago · probe" in subtitles["fresh"][0] and not subtitles["fresh"][1]
    assert "plan data as of 12:13 · 2h00 ago · probe" in subtitles["stale"][0] and subtitles["stale"][1]
    assert "plan data of unknown age · probe" in subtitles["stale-of-unknown-age"][0]
    assert subtitles["stale-of-unknown-age"][1]

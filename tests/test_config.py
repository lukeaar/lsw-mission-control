from __future__ import annotations

from pathlib import Path

import pytest

from lsw_mission_control.config import DEFAULT_PANELS, ConfigError, claude_slug, find_config, load_config, parse_logo_path


def write(tmp_path: Path, text: str) -> Path:
    d = tmp_path / "proj" / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "mission-control.toml"
    p.write_text(text)
    return p


def test_defaults(tmp_path):
    cfg = load_config(write(tmp_path, "schema = 1\n"))
    root = (tmp_path / "proj").resolve()
    assert cfg.root == root and cfg.name == "proj" and cfg.title == "PROJ" and cfg.subtitle == "mission control"
    assert cfg.claude_slug == claude_slug(root)
    assert cfg.projects_dir == Path.home() / ".claude" / "projects" / cfg.claude_slug
    assert cfg.scratch_dir.name == cfg.claude_slug and cfg.scratch_dir.parent.name.startswith("claude-")
    assert cfg.plan_file == root / ".claude" / "status_plan.json"
    assert cfg.notes_file == root / ".claude" / "status_notes.json"
    assert cfg.cache_dir == Path.home() / ".cache" / "lsw-mission-control" / "projects" / "proj"
    assert cfg.github.repo is None and cfg.plugins == ()
    assert cfg.layout.panels == DEFAULT_PANELS and cfg.layout.max_width == 150
    assert cfg.release.final_merge.minutes == 210 and cfg.release.fix_share == 0.7
    assert cfg.release.later_title == "Later release {release}" and cfg.release.later_wait == "after {release}"
    assert cfg.logo.caption == "λ∿ 2026" and cfg.logo.colours == ("#A221D9", "#D96D21")
    assert cfg.theme.bg == "#252226"


def test_slug_matches_claude_code():
    assert claude_slug("/Users/x/work/project") == "-Users-x-work-project"
    # every non-alphanumeric character, as Claude Code names a cwd with a dot in it
    assert claude_slug("/Users/x/.cache/tool-status/probe-cwd") == "-Users-x--cache-tool-status-probe-cwd"
    assert claude_slug("/a/b_c d") == "-a-b-c-d"


def test_relative_paths_and_home(tmp_path):
    cfg = load_config(write(tmp_path, 'schema = 1\n[files]\nplan = "../plan.json"\n[cache]\ndir = "~/somewhere"\n'))
    assert cfg.plan_file == (tmp_path / "proj" / "plan.json").resolve()
    assert cfg.cache_dir == Path.home() / "somewhere"


def test_cache_dir_overrides(tmp_path, monkeypatch):
    p = write(tmp_path, 'schema = 1\n[cache]\ndir = "~/c"\n')
    monkeypatch.setenv("LSW_MC_CACHE_DIR", str(tmp_path / "env"))
    assert load_config(p).cache_dir == tmp_path / "env"
    assert load_config(p, cache_dir=tmp_path / "flag").cache_dir == tmp_path / "flag"


@pytest.mark.parametrize("text, message", [
    ("", "schema must be 1"),
    ("schema = 2\n", "schema must be 1"),
    ("schema = 1\nfoo = 1\n", "unknown key foo"),
    ("schema = 1\n[release]\ntag_prefx = 'v'\n", "unknown key [release].tag_prefx"),
    ("schema = 1\n[github]\nrepo = 'nope'\n", "[github].repo must be OWNER/NAME"),
    ("schema = 1\n[git]\npoll_s = true\n", "[git].poll_s must be a whole number"),
    ("schema = 1\n[layout]\npanels = ['notes', 'bogus']\n", "unknown panel 'bogus'"),
    ("schema = 1\n[layout]\npanels = ['usage', 'notes']\n", "'usage' must come last"),
    ("schema = 1\n[theme]\nbg = 'black'\n", "[theme].bg must be a colour"),
    ("schema = 1\n[theme]\nbackground = '#000000'\n", "unknown key [theme].background"),
    ("schema = 1\n[logo]\npath = 'M 0 0 C 1 1 2 2 3 3'\n", "only absolute M, L and Q"),
    ("schema = 1\n[logo]\npath = 'M 0 0 L 500 0'\n", "leaves its 150x150 viewbox"),
    ("schema = 1\n[logo]\ncolours = ['#000000']\n", "two colours"),
    ("schema = 1\n[release]\ntitle = 'Release {version}'\n", "may use only {release}"),
    ("schema = 1\n[release.final_merge]\nstages = [['build', 'x']]\n", "list of [name, minutes]"),
    ("schema = 1\n[[plugins]]\nname = 'a'\nclass = 'A'\n", "either file or module"),
    ("schema = 1\n[[plugins]]\nname = 'a'\nclass = 'A'\nfile = 'x.py'\nmodule = 'x'\n", "either file or module"),
    ("schema = 1\n[[plugins]]\nname = 'a'\nclass = 'A'\nfile = 'x.py'\nextra = 1\n", "unknown key [[plugins]] #1.extra"),
    ("schema = 1\n[[network.cases]]\ncommand = 'x'\n", "[[network.cases]] #1"),
    ("schema = 1\n[project\n", "Expected ']' at the end of a table declaration"),
    ("schema = 1\n[project]\nprojects_dir = 5\n", "[project].projects_dir must be text"),
    ("schema = 1\n[release]\ntitle = '{release.upper.x}'\n", "[release].title may use only {release}"),
    ("schema = 1\n[release]\ntag_row = 'Tag {0}'\n", "[release].tag_row may use only {release}"),
    ("schema = 1\n[release]\nlater_title = 'Then {version}'\n", "[release].later_title may use only {release}"),
    ("schema = 1\n[release]\nlater_wait = 'after {0}'\n", "[release].later_wait may use only {release}"),
    ("schema = 1\n[test_logs]\nglobs = ['{nope}/*.log']\n", "[test_logs].globs: '{nope}/*.log' may use only"),
    ("schema = 1\n[plan]\nafter_server = 'nope'\n", "[plan].after_server: no [[plugins]] entry is named 'nope'"),
    ("schema = 1\n[layout]\npanels = ['notes', 'plugin:nope']\n", "'plugin:nope' names no [[plugins]] entry"),
    ("schema = 1\n[git]\npoll_s = 0\n", "[git].poll_s must be at least 2 (seconds)"),
    ("schema = 1\n[github]\npoll_s = 5\n", "[github].poll_s must be at least 30 (seconds)"),
    ("schema = 1\n[github]\ntiming_poll_s = 0\n", "[github].timing_poll_s must be at least 60"),
    ("schema = 1\n[agents]\nrows = 0\n", "[agents].rows must be at least 1"),
    ("schema = 1\n[agents]\nscan_window_h = 0\n", "[agents].scan_window_h must be more than 0"),
    ("schema = 1\n[agents]\nidle_red_min = -1\n", "[agents].idle_red_min must be at least 0"),
    ("schema = 1\n[layout]\nmax_width = 0\n", "[layout].max_width must be at least 60"),
    ("schema = 1\n[release]\nfix_share = 1.5\n", "[release].fix_share must be between 0 and 1"),
    ("schema = 1\n[release]\nhands_minutes = -5\n", "[release].hands_minutes must be at least 0"),
    ("schema = 1\n[release]\nfallback_minutes = 10\n", "fallback_minutes must be at least hands_minutes + release_run"),
    ("schema = 1\n[release.final_merge]\nstages = [['build', -30]]\n", "minutes must be at least 0"),
    ("schema = 1\n[usage]\nprobe_every_min = 0\n", "[usage].probe_every_min must be at least 5"),
    ("schema = 1\n[test_logs]\nlimit = -1\n", "[test_logs].limit must be at least 0"),
    ("schema = 1\n[test_logs]\nmax_age_h = 0\n", "[test_logs].max_age_h must be more than 0"),
    ("schema = 1\n[logo]\nviewbox = 0\n", "[logo].viewbox must be more than 0"),
])
def test_errors_name_the_key(tmp_path, text, message):
    with pytest.raises(ConfigError, match=None) as e:
        load_config(write(tmp_path, text))
    assert message in str(e.value)
    # the caller that prints the error names the file, once
    assert "mission-control.toml" not in str(e.value)


def test_a_missing_config(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_config(tmp_path / "nope.toml")
    assert str(e.value) == f"not found at {tmp_path.resolve() / 'nope.toml'} (`lsw-mc init` writes a starter config)"


def test_the_plugin_names_a_config_refers_to_must_exist(tmp_path):
    cfg = load_config(write(tmp_path, """schema = 1
[plan]
after_server = "q"

[layout]
panels = ["notes", "plugin:q", "usage"]

[[plugins]]
name = "q"
file = "q.py"
class = "Q"
"""))
    assert cfg.after_server == "q" and cfg.layout.panels[1] == "plugin:q"


def test_plugins_and_network(tmp_path):
    cfg = load_config(write(tmp_path, """schema = 1
[network]
tools = ["mytool"]
subcommands = {hg = ["pull", "push"]}
dashboard_markers = ["my-dash"]

[[network.cases]]
command = "mytool --sync"
label = "mytool"

[[network.cases]]
command = "hg status"
label = ""

[[plugins]]
name = "p1"
file = "mc_p1.py"
class = "P1"

[plugins.options]
host = "h"

[[plugins]]
name = "p2"
module = "some.module"
class = "P2"
"""))
    assert cfg.network.tools == ("mytool",) and cfg.network.subcommands == {"hg": ("pull", "push")}
    assert cfg.network.cases == (("mytool --sync", "mytool"), ("hg status", None))
    p1, p2 = cfg.plugins
    assert p1.file == (tmp_path / "proj" / ".claude" / "mc_p1.py").resolve() and p1.options == {"host": "h"}
    assert p2.module == "some.module" and p2.file is None and p2.options == {}


def test_logo_path_parses_to_the_segments():
    segs = parse_logo_path("M17 105 L57 25 Q71 65 85 105 Q97 145 109 105 Q121 65 133 105")
    assert segs == (("L", (17, 105), (57, 25)), ("Q", (57, 25), (71, 65), (85, 105)),
                    ("Q", (85, 105), (97, 145), (109, 105)), ("Q", (109, 105), (121, 65), (133, 105)))
    assert parse_logo_path("M0,0 L1.5,2")[0] == ("L", (0, 0), (1.5, 2))
    with pytest.raises(ConfigError):
        parse_logo_path("L 1 1")
    with pytest.raises(ConfigError):
        parse_logo_path("M 1")


def test_find_config(tmp_path):
    assert find_config(config=str(tmp_path / "x.toml")) == tmp_path / "x.toml"
    assert find_config(launcher=str(tmp_path / ".claude" / "status.py")) == tmp_path / ".claude" / "mission-control.toml"
    assert find_config(project=str(tmp_path)) == tmp_path / ".claude" / "mission-control.toml"
    assert find_config(cwd=tmp_path) == tmp_path / ".claude" / "mission-control.toml"

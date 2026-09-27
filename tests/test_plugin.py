from __future__ import annotations

import io
import sys

import pytest
from rich.console import Console

from lsw_mission_control import testing
from lsw_mission_control.app import run_once
from lsw_mission_control.config import ConfigError, PluginSpec, load_config
from lsw_mission_control.plugin import Flags, Plugin, PluginContext, load_plugin_class, load_plugins, validate_options

from scenarios import STUB_PLUGIN, Project, midway


def test_load_by_file_and_by_module(tmp_path):
    cls = load_plugin_class(PluginSpec("stub", "StubPlugin", file=STUB_PLUGIN))
    assert issubclass(cls, Plugin) and cls.__module__ == "lsw_mc_plugin_stub" and "lsw_mc_plugin_stub" in sys.modules
    (tmp_path / "mymod_for_test.py").write_text("from lsw_mission_control.plugin import Plugin\nclass P(Plugin):\n    pass\n")
    sys.path.insert(0, str(tmp_path))
    try:
        assert load_plugin_class(PluginSpec("m", "P", module="mymod_for_test")).__name__ == "P"
    finally:
        sys.path.remove(str(tmp_path))


def test_a_wrong_base_class_is_refused(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("class NotAPlugin:\n    pass\n")
    with pytest.raises(ConfigError, match="is not a lsw_mission_control.plugin.Plugin"):
        load_plugin_class(PluginSpec("bad", "NotAPlugin", file=f))


def test_a_plugin_knows_its_name_in_init_and_runs_in_a_directory(tmp_path):
    seen = {}

    class Named(Plugin):
        def __init__(self, ctx):
            super().__init__(ctx)
            seen["name"] = self.name

    p = Project(tmp_path)
    ctx = PluginContext("configured-name", {}, load_config(p.dot / "mission-control.toml"), Flags())
    Named(ctx)
    assert seen["name"] == "configured-name"
    (tmp_path / "here.txt").write_text("x")
    assert "here.txt" in ctx.run("ls", cwd=tmp_path)


def test_a_plugin_loaded_by_module_is_watched_for_reloads(tmp_path):
    mod = tmp_path / "mymod_watched.py"
    mod.write_text("from lsw_mission_control.plugin import Plugin\nclass P(Plugin):\n    pass\n")
    sys.path.insert(0, str(tmp_path))
    try:
        p = Project(tmp_path)
        p.write_config("""
            [[plugins]]
            name = "m"
            module = "mymod_watched"
            class = "P"
            """)
        assert mod.resolve() in [f.resolve() for f in p.engine().watched_files()]
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("mymod_watched", None)


def test_load_errors_are_collected(tmp_path):
    p = Project(tmp_path)
    (tmp_path / "broken.py").write_text("import nothing_here_xyz\n")
    p.write_config(f"""
        [[plugins]]
        name = "broken"
        file = "{tmp_path / 'broken.py'}"
        class = "X"

        [[plugins]]
        name = "stub"
        file = "{STUB_PLUGIN}"
        class = "StubPlugin"

        [plugins.options]
        host = "h"
        typo_key = 1
        """)
    plugins, errors = load_plugins(load_config(p.dot / "mission-control.toml"), Flags())
    assert plugins == []
    assert errors[0][0] == "broken" and "ModuleNotFoundError" in errors[0][1]
    assert errors[1] == ("stub", "ConfigError: unknown key [plugins.options].typo_key (plugin stub)")


def test_validate_options():
    assert validate_options("p", {"a": "x"}, {"a": str}, {"b": (int, 3)}) == {"a": "x", "b": 3}
    with pytest.raises(ConfigError, match="missing"):
        validate_options("p", {}, {"a": str})
    with pytest.raises(ConfigError) as e:
        validate_options("p", {"a": "x", "b": True}, {"a": str}, {"b": (int, 3)})
    assert str(e.value) == "[plugins.options].b has the wrong type: it must be a whole number (plugin p)"


def test_plugin_flags_and_plan_keys(tmp_path):
    p = Project(tmp_path)
    midway(p)
    plan = p.dot / "status_plan.json"
    import json

    raw = json.loads(plan.read_text())
    raw["stub_key"] = "owned by the plugin"
    plan.write_text(json.dumps(raw))
    e = p.engine(flags=["--no-stub"])
    f = e.build_frame(120)
    assert f.plan.plugin_data == {"stub": "owned by the plugin"}
    assert f.live_job is None  # the flag switched it off
    assert e.own_labels == frozenset({"gh", "ssh → stubhost"})


def test_a_failing_side_card_stays_in_its_card(tmp_path):
    p = Project(tmp_path)
    midway(p)
    p.plugin_state["fail"] = "side_card"
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert not e.last_error  # the rest of the frame rendered
    assert "RuntimeError: stub side_card broke" in plain and "Release 1.4.0" in plain and "Repository" in plain
    assert "plugin stub side_card():" in e.errors_for_once()


def test_a_failing_live_job_is_loud(tmp_path):
    p = Project(tmp_path)
    midway(p)
    p.plugin_state["fail"] = "live_job"
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert "plugin error" in plain  # in the row that waits on it, never the "not reached yet" path
    assert "RuntimeError: stub live_job broke" in plain  # and in the plugin's own card


def test_once_exits_1_on_a_plugin_error(tmp_path, capsys):
    p = Project(tmp_path)
    midway(p)
    p.plugin_state["fail"] = "live_job"
    e = p.engine()
    console = Console(file=io.StringIO(), width=120)
    assert run_once(e, console, wait_s=0) == 1
    assert "RuntimeError: stub live_job broke" in capsys.readouterr().err
    p2 = Project(tmp_path / "ok")
    midway(p2)
    assert run_once(p2.engine(), Console(file=io.StringIO(), width=120), wait_s=0) == 0


def test_a_plugin_that_fails_to_load_shows_a_card(tmp_path):
    p = Project(tmp_path)
    midway(p)
    p.write_config(f"""
        [[plugins]]
        name = "stub"
        file = "{STUB_PLUGIN}"
        class = "StubPlugin"
        """)  # its required option `host` is missing
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert "not loaded: ConfigError: [plugins.options].host is" in plain and "missing (plugin stub)" in plain  # wrapped
    assert "plugin stub not loaded" in e.errors_for_once()


@pytest.mark.parametrize("named", [True, False])
def test_rows_waiting_on_a_plugin_that_did_not_load_say_plugin_error(tmp_path, named):
    p = Project(tmp_path)
    midway(p)
    p.write_config(("[plan]\nafter_server = \"stub\"\n" if named else "") + f"""
        [[plugins]]
        name = "stub"
        file = "{STUB_PLUGIN}"
        class = "StubPlugin"
        """)  # its required option `host` is missing: it does not load
    e = p.engine()
    f = e.build_frame(150)
    assert f.live_job is None and f.live_job_error.startswith("not loaded: ConfigError: [plugins.options].host")
    plain, _ = testing.render_engine(e, 150)
    row = next(line for line in plain.splitlines() if "Nightly data import" in line)
    assert "plugin error" in row and "import?" not in row


def test_a_plugin_that_failed_to_start_is_a_plugin_error_for_its_rows(tmp_path, monkeypatch):
    from lsw_mission_control.sources import Source

    monkeypatch.setattr(Source, "start", lambda self: None)  # no git, gh or token polling from a test
    p = Project(tmp_path)
    midway(p)
    e = p.engine()

    def boom():
        raise RuntimeError("no thread for you")
    e.plugins[0].start = boom
    e.plugins[0].live_job = lambda frame: None  # never polled
    e.start_sources(probe=False)
    f = e.build_frame(150)
    assert f.live_job_error == "not loaded: RuntimeError: no thread for you"


def test_ready_waits_for_every_plugin(tmp_path):
    p = Project(tmp_path)
    midway(p)
    e = p.engine()
    assert e.ready()

    class Slow(Plugin):
        def ready(self):
            return False
    e.plugins.append(Slow(e.plugins[0].ctx))
    assert not e.ready()
    e.plugins.pop()
    e.store.set("gh_polled", None)
    assert not e.ready()  # [github] is configured: --once waits for its first round too
    e.store.set("gh_polled", True)
    e.store.set("gh_timing", None)
    assert e.ready()  # answered without timings (no successful CI run, or gh offline): no 25 s wait

"""The plugin API (docs/plugins.md). A plugin polls in the background, renders a side card
and/or a full-width panel, may own plan keys, may expose a live job that `after_server` items
wait on, and may add CLI flags. Nothing else: extend this API rather than reach into the engine.

Everything a plugin may import is re-exported here and listed in __all__ (plus
lsw_mission_control.render.widgets, .render.side, .util and .theme). This surface only ever grows.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from rich.console import RenderableType
from rich.table import Table

from lsw_mission_control.config import Config, ConfigError, PluginSpec, _kind_name
from lsw_mission_control.store import Store
from lsw_mission_control.util import now as _now
from lsw_mission_control.util import run as _run
from lsw_mission_control.util import write_json as _write_json

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame

__all__ = ["CliFlag", "ConfigError", "Flags", "LiveJob", "Plugin", "PluginContext", "SideCard", "Store",
           "validate_options"]


@dataclass(frozen=True)
class CliFlag:
    flag: str  # "--no-remote"
    help: str  # "skip the remote panel (no SSH)"


@dataclass(frozen=True)
class LiveJob:
    """A long job the plan can wait on. `after_server` items render their FIRST stage from this;
    the rest of their stages run as usual."""

    left: int  # units still to do; 0 = finished
    frac: float  # share done, 0..1
    eta: float | None  # seconds left from the steady rate; None = stalled / not known
    eta_life: float | None  # a rough whole-run estimate; weights the bar when eta is None
    live: bool  # the source answered recently
    stalled_label: str = "stalled"


@dataclass
class SideCard:
    """One panel in the side row (beside Repository), padded to the row's height by the engine."""

    title: str
    grid: Table  # a 2-column Table.grid: the label column (widgets.LABEL_W), then the value
    subtitle: str = ""
    subtitle_style: str | None = None  # None: the theme's faint


class Flags:
    """The command line's flags, matched exactly (an unknown flag is ignored, never guessed at)."""

    def __init__(self, argv: Iterable[str] = ()) -> None:
        self.argv = tuple(argv)

    def has(self, flag: str) -> bool:
        return flag in self.argv


@dataclass
class PluginContext:
    name: str
    options: Mapping[str, Any]
    cfg: Config
    flags: Flags
    state: Store = field(default_factory=Store)

    @property
    def root(self) -> Path:
        return self.cfg.root

    @property
    def cache_dir(self) -> Path:
        return self.cfg.cache_dir

    def run(self, *cmd: str, timeout: int = 30, input_: str | None = None, cwd: Path | str | None = None) -> str:
        """A direct child of the dashboard (so the network row can tell it from anyone else's)."""
        return _run(*cmd, timeout=timeout, input_=input_, cwd=cwd)

    def spawn(self, target: Callable[[], None], name: str) -> None:
        threading.Thread(target=target, name=f"lsw-mc-{self.name}-{name}", daemon=True).start()

    def write_json(self, path: Path, data) -> None:
        _write_json(path, data)

    def now(self) -> float:
        return _now()


class Plugin:
    name: str = ""
    cli_flags: tuple[CliFlag, ...] = ()

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.name = ctx.name  # its [[plugins]] name, from the start (__init__ may use it)

    @property
    def net_labels(self) -> tuple[str, ...]:
        """Network labels this plugin's own polling produces (e.g. "ssh → host"): the network row
        ignores them when a dashboard runs them."""
        return ()

    def parse_plan(self, raw: dict) -> object:
        """Read the plan keys this plugin owns; raise to reject the whole plan (the last good plan
        stays, and the title says why)."""
        return None

    def start(self) -> None:
        """Start background polling (never called by --check-net or --self-check)."""

    def ready(self) -> bool:
        """--once waits (up to 25 s) until every plugin is ready."""
        return True

    def side_card(self, width: int, frame: Frame) -> SideCard | None:
        return None

    def panel(self, width: int, frame: Frame) -> RenderableType | None:
        return None

    def live_job(self, frame: Frame) -> LiveJob | None:
        """None: unknown (never answered, or switched off)."""
        return None


def validate_options(plugin: str, options: Mapping[str, Any], required: Mapping[str, type],
                     optional: Mapping[str, tuple[type, Any]] | None = None) -> dict:
    """A plugin's [plugins.options], checked: every required key present with its type, no
    unknown key. Returns the options with defaults filled in. Raises ConfigError."""
    optional = optional or {}
    out = {}
    for key in options:
        if key not in required and key not in optional:
            raise ConfigError(f"unknown key [plugins.options].{key} (plugin {plugin})")
    for key, kind in required.items():
        if key not in options:
            raise ConfigError(f"[plugins.options].{key} is missing (plugin {plugin})")
    for key, spec in list(required.items()) + [(k, v[0]) for k, v in optional.items()]:
        if key in options:
            v = options[key]
            kinds = spec if isinstance(spec, tuple) else (spec,)
            if isinstance(v, bool) and bool not in kinds or not isinstance(v, kinds):
                raise ConfigError(f"[plugins.options].{key} has the wrong type: it must be {_kind_name(kinds)} "
                                  f"(plugin {plugin})")
            out[key] = v
    for key, (_kind, default) in optional.items():
        out.setdefault(key, default)
    return out


def load_plugin_class(spec: PluginSpec) -> type[Plugin]:
    if spec.file is not None:
        modname = "lsw_mc_plugin_" + "".join(ch if ch.isalnum() else "_" for ch in spec.name)
        mspec = importlib.util.spec_from_file_location(modname, spec.file)
        if mspec is None or mspec.loader is None:
            raise ConfigError(f"plugin {spec.name}: cannot load {spec.file}")
        module = importlib.util.module_from_spec(mspec)
        sys.modules[modname] = module  # dataclasses in the plugin need their module registered
        try:
            mspec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(modname, None)
            raise
    else:
        module = importlib.import_module(spec.module)
    cls = getattr(module, spec.cls, None)
    if not (isinstance(cls, type) and issubclass(cls, Plugin)):
        raise ConfigError(f"plugin {spec.name}: {spec.cls} is not a lsw_mission_control.plugin.Plugin")
    return cls


def load_plugins(cfg: Config, flags: Flags) -> tuple[list[Plugin], list[tuple[str, str]]]:
    """(the plugins, [(name, "Type: message")] for each that failed to load)."""
    plugins, errors = [], []
    for spec in cfg.plugins:
        try:
            cls = load_plugin_class(spec)
            plugin = cls(PluginContext(spec.name, spec.options, cfg, flags))
            plugin.name = spec.name
            plugins.append(plugin)
        except Exception as e:  # noqa: BLE001 — shown in the view; --check-net fails on it
            errors.append((spec.name, f"{type(e).__name__}: {e}"))
    return plugins, errors

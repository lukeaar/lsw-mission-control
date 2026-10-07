"""The engine: owns the config, the store, the plan, the finished store, the sources and the
plugins, and builds each frame."""

from __future__ import annotations

import math
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.constrain import Constrain
from rich.protocol import is_renderable
from rich.table import Table
from rich.text import Text

import lsw_mission_control
from lsw_mission_control import theme
from lsw_mission_control.agents import FinishedStore, final_merge_labels, label_names, latest_by_label, scan_agents
from lsw_mission_control.config import Config
from lsw_mission_control.connectivity import UNKNOWN, Conn, connection
from lsw_mission_control.net import OWN_LABELS, NetRules, NetState, network_critical
from lsw_mission_control.notes import Notes, NotesLoader
from lsw_mission_control.plan import Plan, PlanLoader
from lsw_mission_control.plugin import Flags, LiveJob, Plugin, SideCard, load_plugins
from lsw_mission_control.progress import Calibration, calibrate
from lsw_mission_control.render.agents import agents_panel
from lsw_mission_control.render.frame import bottom_line, title_line
from lsw_mission_control.render.guard import Guarded
from lsw_mission_control.render.logo import LogoAnimator
from lsw_mission_control.render.next_release import later_panel, next_panel
from lsw_mission_control.render.notes import notes_panel
from lsw_mission_control.render.other import other_panel
from lsw_mission_control.render.release import release_panel
from lsw_mission_control.render.side import error_card, repo_grid, side_columns, side_panels
from lsw_mission_control.render.usage import usage_row
from lsw_mission_control.render.widgets import panel
from lsw_mission_control.sources.connectivity import ConnectivitySource
from lsw_mission_control.sources.git import GitSource
from lsw_mission_control.sources.github import GitHubSource
from lsw_mission_control.sources.testlogs import test_logs
from lsw_mission_control.sources.tokens import TokenCounter
from lsw_mission_control.sources.usage_probe import UsageProbe
from lsw_mission_control.store import Store
from lsw_mission_control.theme import C
from lsw_mission_control.util import now

PACKAGE_DIR = Path(lsw_mission_control.__file__).resolve().parent


@dataclass
class Frame:
    """Everything one frame's panels read (plugins get it too)."""

    now: float
    width: int
    cfg: Config
    plan: Plan
    plan_note: str
    reload_note: str
    notes: Notes
    agents: list[dict]
    labels: dict[str, dict]
    names: dict[str, str]
    cal: Calibration
    store: dict
    flags: Flags
    live_job: LiveJob | None = None
    live_job_error: str | None = None  # the live job's plugin failed this frame ("Type: message")
    plugin_errors: dict[str, list[str]] = field(default_factory=dict)  # plugin name -> ["Type: message"]
    notes_note: str = ""  # why the notes file did not load ("" when it did); `notes` are then the last good
    # What the frame's panels work out once and share: the release's rows, which the planned releases'
    # rows can wait on (render/release.py release_rows), and the planned releases' rows, worked out
    # together (render/next_release.py planned_rows).
    memo: dict = field(default_factory=dict)


def _overrides(plugin: Plugin, method: str) -> bool:
    return getattr(type(plugin), method) is not getattr(Plugin, method)


# What a plugin's methods must return: checked inside the plugin's own try, so a wrong type is that
# plugin's error (in its card, loud in --once and the reload gate), never the whole frame's.
def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def check_side_card(card) -> None:
    if card is not None and not (isinstance(card, SideCard) and isinstance(card.grid, Table)):
        what = type(card.grid).__name__ + " grid" if isinstance(card, SideCard) else type(card).__name__
        raise TypeError(f"side_card() must return None or a SideCard whose grid is a rich Table, not a {what}")


def check_panel(body) -> None:
    if body is not None and not is_renderable(body):
        raise TypeError(f"panel() must return None or a rich renderable, not a {type(body).__name__}")


def check_live_job(job) -> None:
    if job is None:
        return
    if not isinstance(job, LiveJob):
        raise TypeError(f"live_job() must return None or a LiveJob, not a {type(job).__name__}")
    bad = [k for k in ("left", "frac") if not _finite(getattr(job, k))]
    bad += [k for k in ("eta", "eta_life") if getattr(job, k) is not None and not _finite(getattr(job, k))]
    if bad:
        raise TypeError(f"live_job(): {', '.join(bad)} must be a finite number (eta and eta_life may be None)")
    if not isinstance(job.stalled_label, str):
        raise TypeError("live_job(): stalled_label must be text")


class Engine:
    def __init__(self, cfg: Config, flags: Flags | None = None, *, readonly: bool = False,
                 plugins: list[Plugin] | None = None, load_errors: list[tuple[str, str]] | None = None,
                 no_plugins: bool = False) -> None:
        theme.use(cfg.theme)
        self.cfg = cfg
        self.flags = flags or Flags()
        self.readonly = readonly
        self.store = Store()
        if plugins is None:
            plugins, load_errors = ([], []) if no_plugins else load_plugins(cfg, self.flags)
        self.plugins: list[Plugin] = plugins
        self.load_errors: list[tuple[str, str]] = list(load_errors or [])
        self.plans = PlanLoader(cfg.plan_file, self.plugins)
        self.plans.refresh()
        self.notes = NotesLoader(cfg.notes_file)
        self.finished = FinishedStore(cfg.cache_dir / "finished.json", readonly=readonly)
        self.cal = Calibration(cfg.release.fix_share)
        self.logo = LogoAnimator(cfg.logo) if cfg.logo.enabled else None
        self.net_rules = NetRules.with_extras(cfg.network.tools, cfg.network.subcommands)
        own = set(OWN_LABELS)
        for p in self.plugins:
            try:
                own.update(p.net_labels)
            except Exception:  # noqa: BLE001 — a plugin fault must not stop the view
                pass
        self.own_labels = frozenset(own)
        self.ps_text: str | None = None  # tests and parity inject a process table
        # The indicator's "not connected" state: a background check writes the store's `online`.
        self.internet_check = cfg.network.check_internet
        self.reload_note = ""
        self.last_error = ""  # the traceback of the last frame that failed ("" when it worked)
        self.plugin_tracebacks: list[str] = []  # the last frame's caught plugin errors
        # The last frame's panels that failed, building or drawing (each drew an error in its own
        # place): name -> traceback. --once and the reload gate fail on any.
        self.panel_errors: dict[str, str] = {}
        self.sources: list = []

    # ── sources ───────────────────────────────────────────────────────────────────────────
    def start_sources(self, probe: bool = True) -> None:
        cfg = self.cfg
        self.sources = [GitSource(cfg.root, cfg.git.main_branch, cfg.git.remote, self.store, cfg.git.poll_s)]
        if self.internet_check:
            self.sources.append(ConnectivitySource(self.store))
        if cfg.github.repo:
            self.sources.append(GitHubSource(cfg.github, cfg.release, cfg.git.main_branch, self.store,
                                             lambda: self.plans.plan.release))
        self.sources.append(TokenCounter(cfg.claude_projects_root, cfg.usage_dir / "tokens.json", self.store))
        if probe and cfg.usage.probe:
            self.sources.append(UsageProbe(cfg.usage_dir, cfg.usage.probe_every_min * 60, cfg.usage.probe_fresh_min * 60,
                                           cfg.usage.probe_model))
        for s in self.sources:
            s.start()
        for p in self.plugins:
            try:
                p.start()
            except Exception as e:  # noqa: BLE001 — shown in its card
                self.load_errors.append((p.name, f"{type(e).__name__}: {e}"))

    def ready(self) -> bool:
        """--once waits for this: git, the internet check's first answer (if on; it takes at most
        2.5 s: the request's 2 s, and a hung name lookup is given up 0.5 s after that), GitHub's
        first round (if configured; answered or not) and every plugin."""
        if not self.store.get("git"):
            return False
        if self.internet_check and self.store.get("online") is None:
            return False
        if self.cfg.github.repo and not self.store.get("gh_polled"):
            return False
        for p in self.plugins:
            try:
                if not p.ready():
                    return False
            except Exception:  # noqa: BLE001 — a failing plugin shows its error; it must not hold --once
                pass
        return True

    # ── frames ────────────────────────────────────────────────────────────────────────────
    def _live_job_plugin(self) -> Plugin | None:
        if self.cfg.after_server:
            return next((p for p in self.plugins if p.name == self.cfg.after_server), None)
        providers = [p for p in self.plugins if _overrides(p, "live_job")]
        return providers[0] if len(providers) == 1 else None

    def _live_job_load_error(self) -> str | None:
        """Why the live job's plugin is not running (it failed to load or to start), if it is not:
        the rows that wait on it must say "plugin error", never read as "not reached yet"."""
        errors = dict(self.load_errors)
        if self.cfg.after_server:
            msg = errors.get(self.cfg.after_server)
        elif not any(_overrides(p, "live_job") for p in self.plugins) and errors:
            msg = next(iter(errors.values()))  # no plugin offers a live job: the one that failed might have
        else:
            msg = None
        return f"not loaded: {msg}" if msg else None

    def _plugin_call(self, f: Frame, plugin: Plugin, method: str, *args, check=None):
        try:
            out = getattr(plugin, method)(*args)
            if check is not None:
                check(out)
            return out, None
        except Exception as e:  # noqa: BLE001 — one plugin's fault stays in its card
            msg = f"{type(e).__name__}: {e}"[:300]
            f.plugin_errors.setdefault(plugin.name, []).append(msg)
            self.plugin_tracebacks.append(f"plugin {plugin.name} {method}(): {traceback.format_exc()}")
            return None, msg

    def build_frame(self, width: int) -> Frame:
        cfg = self.cfg
        plan = self.plans.refresh()
        t = now()
        names = label_names(plan, cfg.release.final_merge)
        # The final merge is one release's own: the shipped release's never reads as the next one's.
        agents = self.finished.merge(scan_agents(cfg.projects_dir, t, cfg.agents.scan_window_h * 3600), plan, names,
                                     loaded=self.plans.loaded, release_bound=final_merge_labels(cfg.release.final_merge))
        labels = latest_by_label(agents, t, cfg.agents.silent_stopped_min * 60)
        calibrate(self.cal, plan.items, labels)
        f = Frame(now=t, width=width, cfg=cfg, plan=plan, plan_note=self.plans.note, reload_note=self.reload_note,
                  notes=self.notes.refresh(), agents=agents, labels=labels, names=names, cal=self.cal,
                  store=self.store.snapshot(), flags=self.flags, notes_note=self.notes.note)
        src = self._live_job_plugin()
        if src is not None:
            job, err = self._plugin_call(f, src, "live_job", f, check=check_live_job)
            f.live_job, f.live_job_error = job, err
        if f.live_job is None and f.live_job_error is None:
            f.live_job_error = self._live_job_load_error()
        return f

    def side_cards(self, f: Frame, width: int) -> list[tuple[str, SideCard]]:
        """[(whose card: a plugin's name, or "Repository"), the card]."""
        carded = [p for p in self.plugins if _overrides(p, "side_card")]
        n = len(carded) + len(self.load_errors) + 1
        col_w = side_columns(n, width)
        cards: list[tuple[str, SideCard]] = []
        for p in carded:
            card, err = self._plugin_call(f, p, "side_card", col_w, f, check=check_side_card)
            if err:
                cards.append((p.name, error_card(p.name, err)))
                continue
            if card is None:
                continue
            for msg in f.plugin_errors.get(p.name, []):
                card.grid.add_row("error", Text(msg, style=C.RED_SOFT))
            cards.append((p.name, card))
        for name, msg in self.load_errors:
            cards.append((name, error_card(name, f"not loaded: {msg}")))
        cards.append(("Repository", SideCard("Repository", repo_grid(f.store, self.cfg.git.main_branch, self.cfg.git.remote))))
        return cards

    def release_row(self, f: Frame, width: int):
        return release_panel(f, width)[0]

    def agents_row(self, f: Frame, width: int):
        cfg = self.cfg
        logs = test_logs([g.format(scratch=cfg.scratch_dir, root=cfg.root) for g in cfg.test_logs.globs], f.now,
                         cfg.test_logs.max_age_h * 3600, cfg.test_logs.limit)
        return agents_panel(f, width, logs)

    def side_row(self, f: Frame, width: int):
        owned = self.side_cards(f, width)
        owners = [o for o, _c in owned]

        def wrap(i: int, card: SideCard, body):
            def stand_in(e: BaseException):
                msg = self._record(f"card {owners[i]}", e, f"drawing the {card.title} card")
                ec = error_card(card.title, msg)
                while ec.grid.row_count < card.grid.row_count:
                    ec.grid.add_row("", "")
                return panel(ec.grid, ec.title)
            return Guarded(body, stand_in)
        return side_panels([c for _o, c in owned], wrap)

    # ── containment ───────────────────────────────────────────────────────────────────────
    def _where(self, e: BaseException, drawing: str = "") -> str:
        # A drawing error surfaces in the guard (rich raised it): name what was being drawn instead.
        where = error_where(e, [s.file for s in self.cfg.plugins if s.file is not None])
        return drawing if drawing and (not where or where.startswith("render/guard.py")) else where

    def _record(self, name: str, e: BaseException, drawing: str = "") -> str:
        """Called inside an `except`: keep the traceback for --once and the gate; the short message."""
        self.panel_errors[name] = traceback.format_exc()
        where = self._where(e, drawing)
        return f"{type(e).__name__}: {e}"[:300] + (f"   {where}" if where else "")

    def _stand_in(self, name: str, e: BaseException, drawing: str = ""):
        """The error panel that takes a failed panel's place (the others still draw)."""
        self.panel_errors[name] = traceback.format_exc()
        msg = Text(f"{type(e).__name__}: {e}"[:300], style=f"bold {C.RED_SOFT}")
        where = self._where(e, drawing)
        if where:
            msg.append(f"   {where}", style=C.FAINT)
        return panel(msg, "Mission control error")

    def _build(self, name: str, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 — this panel shows the error; the rest of the frame draws
            return self._stand_in(name, e)

    def frame(self, console: Console) -> tuple[RenderableType, NetState, int]:
        """(the scrollable body, the network indicator's state now, the dashboard's width)."""
        cfg = self.cfg
        self.plugin_tracebacks = []
        self.panel_errors = {}
        width = min(console.size.width, cfg.layout.max_width)
        if self.logo is not None:
            self.logo.geom = None  # set again by this frame's logo panel, if it has one
        f = self.build_frame(width)
        panels = cfg.layout.panels
        # The release and next panels are built first, as they always were. Each panel is built,
        # and later drawn, in its own containment: a failure shows in its place, the rest draws.
        built: dict = {}
        later: list = []  # (name, panel) of each release after the next: drawn right after it
        if "release" in panels and cfg.release.enabled:
            built["release"] = self._build("release", self.release_row, f, width)
        if "next" in panels:
            built["next"] = self._build("next", next_panel, f, width)
            later = [(f"later[{i}]", self._build(f"later[{i}]", later_panel, f, width, i)) for i in range(len(f.plan.later))]
        parts: list = [title_line(cfg.title, cfg.subtitle, self.reload_note, self.plans.note, self.notes.note), Text("")]
        for name in panels:
            body = None
            if name in built:
                body = built[name]
            elif name == "notes":
                body = self._build(name, notes_panel, f.notes, f.notes_note)
            elif name == "other":
                body = self._build(name, other_panel, f, width)
            elif name == "side":
                body = self._build(name, self.side_row, f, width)
            elif name == "agents":
                body = self._build(name, self.agents_row, f, width)
            elif name == "usage":
                body = self._build(name, usage_row, console, f, width, self.logo)
            elif name.startswith("plugin:"):
                p = next((x for x in self.plugins if x.name == name[7:]), None)
                if p is not None:
                    body, err = self._plugin_call(f, p, "panel", width, f, check=check_panel)
                    if err:
                        body = panel(Text(err, style=f"bold {C.RED_SOFT}"), p.name)
            bodies = [(name, body)] + (later if name == "next" else [])
            for name_, body_ in bodies:
                if body_ is not None:
                    parts.append(Guarded(body_, lambda e, n=name_: self._stand_in(n, e, f"drawing the {n} panel")))
        return Group(*parts), NetState(self.network_critical(), self.safe_connection()), width

    def connection(self) -> Conn | None:
        """The internet check's view now (None when the check is off): its last answer, or
        "unknown" when that is too old."""
        return connection(self.store.get("online"), now()) if self.internet_check else None

    def safe_connection(self) -> Conn | None:
        """connection(), except that a view that cannot be read is "unknown", never a guess. A store
        read and the clock, no I/O: the live view calls it on every tick of its loop."""
        try:
            return self.connection()
        except Exception:  # noqa: BLE001
            return Conn(UNKNOWN, "not read") if self.internet_check else None

    def network_critical(self) -> list[str] | None:
        return network_critical(self.net_rules, self.cfg.network.dashboard_markers, self.own_labels, ps_text=self.ps_text,
                                self_pid=os.getpid() if self.ps_text is None else None)

    def safe_frame(self, console: Console) -> tuple[RenderableType, NetState, int]:
        """frame(), except that an error in it shows as a panel instead of killing the owner's view
        (the next refresh tries again). The network row is still checked, and never says safe
        when that check fails too."""
        try:
            out = self.frame(console)
            self.last_error = ""
            return out
        except Exception as e:  # noqa: BLE001 — anything at all: the view must stay up
            self.last_error = traceback.format_exc()
            where = error_where(e, [s.file for s in self.cfg.plugins if s.file is not None])
            msg = Text(f"{type(e).__name__}: {e}"[:300], style=f"bold {C.RED_SOFT}")
            if where:
                msg.append(f"   {where}", style=C.FAINT)
            try:
                crit = self.network_critical()
            except Exception:  # noqa: BLE001
                crit = None
            conn = self.safe_connection()
            width = min(console.size.width, self.cfg.layout.max_width)
            return (Group(title_line(self.cfg.title, self.cfg.subtitle, self.reload_note, self.plans.note, self.notes.note),
                          Text(""),
                          panel(msg, "Mission control error")), NetState(crit, conn), width)

    def render(self, console: Console) -> RenderableType:
        """The whole dashboard as one renderable (--once): the body, then the Key + network row."""
        body, net, width = self.safe_frame(console)
        return Constrain(Group(body, bottom_line(width, net.crit, conn=net.conn)), width)

    def errors_for_once(self) -> str:
        """What --once reports on stderr (and exits 1 for): a failed frame, a failed panel (built or
        drawn), a failed plugin. Call it after the frame was drawn."""
        out = [self.last_error] if self.last_error else []
        out += self.plugin_tracebacks
        out += [f"{name} failed: {tb}" for name, tb in self.panel_errors.items()]
        out += [f"plugin {n} not loaded: {m}\n" for n, m in self.load_errors]
        return "".join(out)

    def watched_files(self) -> list[Path]:
        """What a live dashboard reloads itself for: the engine's code, plugins, the config."""
        # Only files that can be modules: an editor's lock link (.#x.py) or a sync tool's conflict copy
        # ("x (1).py") is not engine code, and would fail the gate until the next edit.
        files = sorted(p for p in PACKAGE_DIR.rglob("*.py")
                       if all(part.isidentifier() for part in p.relative_to(PACKAGE_DIR).with_suffix("").parts))
        files += [s.file for s in self.cfg.plugins if s.file is not None]
        # a plugin loaded by module name: the file it was loaded from
        files += [Path(m.__file__) for p in self.plugins
                  if (m := sys.modules.get(type(p).__module__)) is not None and getattr(m, "__file__", None)
                  and Path(m.__file__) not in files]
        files.append(self.cfg.path)
        return files


def error_where(e: BaseException, plugin_files=()) -> str:
    """Where in the engine (or a plugin) an error happened: 'render/release.py line 12 in release_panel'."""
    plugin_files = {Path(x).resolve() for x in plugin_files}
    ours = []
    for fr in traceback.extract_tb(e.__traceback__):
        path = Path(fr.filename).resolve()
        if path in plugin_files:
            ours.append((path.name, fr))
            continue
        try:
            ours.append((str(path.relative_to(PACKAGE_DIR)), fr))
        except ValueError:
            pass
    if not ours:
        return ""
    rel, fr = ours[-1]
    return f"{rel} line {fr.lineno} in {fr.name}"

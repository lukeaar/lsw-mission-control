"""A project's config: `<project>/.claude/mission-control.toml` (reference: docs/config.md).

Paths are relative to the config file's directory unless absolute, and `~` is expanded. An
unknown key is an error, so a typo never silently falls back to a default. A config holds no
secrets: authentication comes from ~/.ssh/config, the ssh agent and gh's own keyring.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from lsw_mission_control.theme import Theme, theme_from

SCHEMA = 1
CONFIG_NAME = "mission-control.toml"
DEFAULT_PANELS = ("notes", "release", "next", "other", "side", "agents", "usage")
KNOWN_PANELS = frozenset(DEFAULT_PANELS)
# The LSW logo, in its own 150x150 units: a stroke up, then three arches.
DEFAULT_LOGO_PATH = "M17 105 L57 25 Q71 65 85 105 Q97 145 109 105 Q121 65 133 105"
DEFAULT_CAPTION = "λ∿ 2026"  # the logo panel's caption, at its bottom right


class ConfigError(Exception):
    pass


def claude_slug(path: Path | str) -> str:
    """Claude Code's project folder name for a path: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def usage_dir() -> Path:
    """Account-wide (plan usage is per account): the status line and every dashboard share it."""
    env = os.environ.get("LSW_MC_USAGE_DIR")
    return Path(env).expanduser() if env else Path.home() / ".cache" / "lsw-mission-control"


@dataclass(frozen=True)
class FinalMergeCfg:
    key: str = "final-merge"
    name: str = "Final merge"
    stages: tuple[tuple[str, int], ...] = (("build", 120), ("review", 30), ("fix", 60))

    @property
    def minutes(self) -> int:
        return sum(m for _s, m in self.stages)


@dataclass(frozen=True)
class ReleaseCfg:
    enabled: bool = True
    tag_prefix: str = "v"
    title: str = "Release {release}"
    next_title: str = "Next release {release}"
    tag_row: str = "Tag {release}"
    tag_todo_text: str = "CI, tag, release"
    words: dict = field(default_factory=lambda: dict(DEFAULT_WORDS))
    fallback_minutes: int = 150  # until GitHub timings load: push, CI, tag, release run, hands-on steps
    hands_minutes: int = 20  # the hands-on steps between CI and the release run
    release_run_minutes: int = 15  # until timings load: the release workflow's run
    fix_share: float = 0.7  # until calibrated: the share of reviews that lead to a fix
    final_merge: FinalMergeCfg = field(default_factory=FinalMergeCfg)


DEFAULT_WORDS = {"done_share": "of the release done", "out": "release out", "released": "released", "ready": "items ready"}


@dataclass(frozen=True)
class GitCfg:
    main_branch: str = "main"
    remote: str = "origin"
    poll_s: int = 20


@dataclass(frozen=True)
class GitHubCfg:
    repo: str | None = None
    ci_workflow: str = "CI"
    release_workflow: str = "Release"
    poll_s: int = 120
    timing_poll_s: int = 600


@dataclass(frozen=True)
class AgentsCfg:
    scan_window_h: float = 48
    silent_stopped_min: float = 25
    idle_amber_min: float = 20
    idle_red_min: float = 55
    rows: int = 14


@dataclass(frozen=True)
class TestLogsCfg:
    __test__ = False  # not a pytest class
    globs: tuple[str, ...] = ("{scratch}/*/scratchpad/*.log",)
    max_age_h: float = 2
    limit: int = 4


@dataclass(frozen=True)
class UsageCfg:
    probe: bool = True
    probe_every_min: float = 20
    probe_fresh_min: float = 15
    probe_model: str = "haiku"


@dataclass(frozen=True)
class NetworkCfg:
    tools: tuple[str, ...] = ()
    subcommands: dict = field(default_factory=dict)
    cases: tuple[tuple[str, str | None], ...] = ()
    dashboard_markers: tuple[str, ...] = ()
    check_internet: bool = True  # the indicator's "not connected" state (connectivity.py)


@dataclass(frozen=True)
class LayoutCfg:
    max_width: int = 150
    panels: tuple[str, ...] = DEFAULT_PANELS


@dataclass(frozen=True)
class LogoCfg:
    enabled: bool = True
    path: str = DEFAULT_LOGO_PATH
    viewbox: float = 150
    split_x: float = 85.0  # left of this is the first colour, right of it the second
    colours: tuple[str, str] = ("#A221D9", "#D96D21")
    caption: str = DEFAULT_CAPTION
    caption_style: str | None = None  # None: the theme's faint
    segments: tuple = ()  # parsed from `path`


@dataclass(frozen=True)
class CompatCfg:
    legacy_scroll_env: str | None = None  # an older dashboard's scroll variable, read and set too


@dataclass(frozen=True)
class PluginSpec:
    name: str
    cls: str
    file: Path | None = None
    module: str | None = None
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    path: Path
    dir: Path
    name: str
    title: str
    subtitle: str
    root: Path
    claude_slug: str
    projects_dir: Path  # this project's Claude Code sessions (workflow journals)
    claude_projects_root: Path  # every project's sessions (token counts are account-wide)
    scratch_dir: Path
    plan_file: Path
    notes_file: Path
    cache_dir: Path
    usage_dir: Path
    git: GitCfg
    github: GitHubCfg
    release: ReleaseCfg
    after_server: str | None
    agents: AgentsCfg
    test_logs: TestLogsCfg
    usage: UsageCfg
    network: NetworkCfg
    layout: LayoutCfg
    logo: LogoCfg
    theme: Theme
    compat: CompatCfg
    plugins: tuple[PluginSpec, ...]


# ── validation helpers ────────────────────────────────────────────────────────────────────
def _table(raw: dict, section: str, allowed: set[str]) -> dict:
    v = raw.get(section, {})
    if not isinstance(v, dict):
        raise ConfigError(f"[{section}] must be a table")
    for key in v:
        if key not in allowed:
            raise ConfigError(f"unknown key [{section}].{key}")
    return v


def _get(t: dict, where: str, key: str, kind, default):
    if key not in t:
        return default
    v = t[key]
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if isinstance(v, bool) and bool not in kinds:
        raise ConfigError(f"{where}.{key} must be {_kind_name(kinds)}")
    if not isinstance(v, kinds):
        raise ConfigError(f"{where}.{key} must be {_kind_name(kinds)}")
    return v


def _kind_name(kinds) -> str:
    names = {str: "text", int: "a whole number", float: "a number", bool: "true or false", list: "a list", dict: "a table"}
    return " or ".join(names.get(k, k.__name__) for k in kinds)


def _strs(t: dict, where: str, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    v = _get(t, where, key, list, None)
    if v is None:
        return default
    if not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{where}.{key} must be a list of text")
    return tuple(v)


def _path(base: Path, value: str) -> Path:
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else (base / p).resolve()


def parse_logo_path(path: str) -> tuple:
    """Absolute M/L/Q commands only: [("L", p0, p1) | ("Q", p0, ctrl, p1), ...]."""
    tokens = re.findall(r"[MLQ]|-?\d+(?:\.\d+)?", path)
    if re.sub(r"[MLQ\s,]|-?\d+(?:\.\d+)?", "", path):
        raise ConfigError("[logo].path: only absolute M, L and Q commands are supported")

    def number(tok: str):
        return int(tok) if re.fullmatch(r"-?\d+", tok) else float(tok)

    segs = []
    cur = None
    i = 0
    try:
        while i < len(tokens):
            cmd = tokens[i]
            if cmd == "M":
                cur = (number(tokens[i + 1]), number(tokens[i + 2]))
                i += 3
            elif cmd == "L":
                end = (number(tokens[i + 1]), number(tokens[i + 2]))
                if cur is None:
                    raise ConfigError("[logo].path must start with M")
                segs.append(("L", cur, end))
                cur, i = end, i + 3
            elif cmd == "Q":
                ctrl = (number(tokens[i + 1]), number(tokens[i + 2]))
                end = (number(tokens[i + 3]), number(tokens[i + 4]))
                if cur is None:
                    raise ConfigError("[logo].path must start with M")
                segs.append(("Q", cur, ctrl, end))
                cur, i = end, i + 5
            else:
                raise ConfigError(f"[logo].path: unexpected {cmd!r}")
    except (IndexError, ValueError):
        raise ConfigError("[logo].path: a command is missing its coordinates") from None
    if not segs:
        raise ConfigError("[logo].path draws nothing")
    return tuple(segs)


# ── loading ───────────────────────────────────────────────────────────────────────────────
_TOP = {"schema", "project", "files", "cache", "git", "github", "release", "plan", "agents", "test_logs", "usage",
        "network", "layout", "logo", "theme", "compat", "plugins"}


def load_config(path: Path | str, cache_dir: Path | str | None = None) -> Config:
    """The config at `path`, or a ConfigError. Its message never starts with the file's name: the
    caller that prints it names the file once."""
    path = Path(path).expanduser().resolve()
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ConfigError(f"not found at {path} (`lsw-mc init` writes a starter config)") from None
    except OSError as e:
        raise ConfigError(f"cannot be read ({type(e).__name__})") from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(str(e)) from None
    return config_from(raw, path, cache_dir=cache_dir)


def _at_least(value, where: str, low, what: str = ""):
    if value < low:
        raise ConfigError(f"{where} must be at least {low}{what}")
    return value


def config_from(raw: dict, path: Path, cache_dir: Path | str | None = None) -> Config:
    base = path.parent
    for key in raw:
        if key not in _TOP:
            raise ConfigError(f"unknown key {key}")
    if raw.get("schema") != SCHEMA:
        raise ConfigError(f"schema must be {SCHEMA}")

    p = _table(raw, "project", {"name", "title", "subtitle", "root", "claude_slug", "projects_dir", "scratch_dir"})
    root = _path(base, _get(p, "[project]", "root", str, ".."))
    name = _get(p, "[project]", "name", str, root.name)
    slug = _get(p, "[project]", "claude_slug", str, claude_slug(root))
    projects_root = Path.home() / ".claude" / "projects"
    pdir = _get(p, "[project]", "projects_dir", str, None)
    projects_dir = _path(base, pdir) if pdir is not None else projects_root / slug
    scratch_default = Path("/tmp").resolve() / f"claude-{os.getuid()}" / slug
    scratch = _path(base, _get(p, "[project]", "scratch_dir", str, str(scratch_default)))

    f = _table(raw, "files", {"plan", "notes"})
    c = _table(raw, "cache", {"dir"})
    env_cache = os.environ.get("LSW_MC_CACHE_DIR")
    if cache_dir is not None:
        cache = Path(cache_dir).expanduser().resolve()
    elif env_cache:
        cache = Path(env_cache).expanduser().resolve()
    else:
        cache = _path(base, _get(c, "[cache]", "dir", str, f"~/.cache/lsw-mission-control/projects/{name}"))

    g = _table(raw, "git", {"main_branch", "remote", "poll_s"})
    # Polling periods have floors: 0 would run git or gh in a busy loop (and GitHub rate-limits).
    git = GitCfg(_get(g, "[git]", "main_branch", str, "main"), _get(g, "[git]", "remote", str, "origin"),
                 _at_least(_get(g, "[git]", "poll_s", int, 20), "[git].poll_s", 2, " (seconds)"))

    gh = _table(raw, "github", {"repo", "ci_workflow", "release_workflow", "poll_s", "timing_poll_s"})
    repo = _get(gh, "[github]", "repo", str, None)
    if repo is not None and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ConfigError("[github].repo must be OWNER/NAME")
    github = GitHubCfg(repo, _get(gh, "[github]", "ci_workflow", str, "CI"),
                       _get(gh, "[github]", "release_workflow", str, "Release"),
                       _at_least(_get(gh, "[github]", "poll_s", int, 120), "[github].poll_s", 30, " (seconds)"),
                       _at_least(_get(gh, "[github]", "timing_poll_s", int, 600), "[github].timing_poll_s", 60, " (seconds)"))

    r = _table(raw, "release", {"enabled", "tag_prefix", "title", "next_title", "tag_row", "tag_todo_text", "words",
                                "fallback_minutes", "hands_minutes", "release_run_minutes", "fix_share", "final_merge"})
    words = dict(DEFAULT_WORDS)
    w = _get(r, "[release]", "words", dict, {})
    for k, v in w.items():
        if k not in DEFAULT_WORDS:
            raise ConfigError(f"unknown key [release.words].{k}")
        if not isinstance(v, str):
            raise ConfigError(f"[release.words].{k} must be text")
        words[k] = v
    fm_raw = _get(r, "[release]", "final_merge", dict, {})
    for k in fm_raw:
        if k not in ("key", "name", "stages"):
            raise ConfigError(f"unknown key [release.final_merge].{k}")
    fm_default = FinalMergeCfg()
    stages = fm_default.stages
    if "stages" in fm_raw:
        st = fm_raw["stages"]
        if not (isinstance(st, list) and st and all(isinstance(x, list) and len(x) == 2 and isinstance(x[0], str)
                                                    and isinstance(x[1], int) and not isinstance(x[1], bool) for x in st)):
            raise ConfigError("[release.final_merge].stages must be a list of [name, minutes]")
        if any(x[1] < 0 for x in st):
            raise ConfigError("[release.final_merge].stages: minutes must be at least 0")
        stages = tuple((x[0], x[1]) for x in st)
    final_merge = FinalMergeCfg(_get(fm_raw, "[release.final_merge]", "key", str, fm_default.key),
                                _get(fm_raw, "[release.final_merge]", "name", str, fm_default.name), stages)
    release = ReleaseCfg(
        enabled=_get(r, "[release]", "enabled", bool, True),
        tag_prefix=_get(r, "[release]", "tag_prefix", str, "v"),
        title=_get(r, "[release]", "title", str, "Release {release}"),
        next_title=_get(r, "[release]", "next_title", str, "Next release {release}"),
        tag_row=_get(r, "[release]", "tag_row", str, "Tag {release}"),
        tag_todo_text=_get(r, "[release]", "tag_todo_text", str, "CI, tag, release"),
        words=words,
        fallback_minutes=_at_least(_get(r, "[release]", "fallback_minutes", int, 150), "[release].fallback_minutes", 0),
        hands_minutes=_at_least(_get(r, "[release]", "hands_minutes", int, 20), "[release].hands_minutes", 0),
        release_run_minutes=_at_least(_get(r, "[release]", "release_run_minutes", int, 15), "[release].release_run_minutes", 0),
        fix_share=float(_get(r, "[release]", "fix_share", (int, float), 0.7)),
        final_merge=final_merge)
    if not 0 <= release.fix_share <= 1:
        raise ConfigError("[release].fix_share must be between 0 and 1")
    if release.fallback_minutes < release.hands_minutes + release.release_run_minutes:
        # the fallback covers CI too: what is left of it once the other two are taken out
        raise ConfigError("[release].fallback_minutes must be at least hands_minutes + release_run_minutes")
    for key in ("title", "next_title", "tag_row"):
        try:
            getattr(release, key).format(release="x")
        except Exception:  # noqa: BLE001 — "{release.upper}", "{0}", "{x}", "{": all the same mistake
            raise ConfigError(f"[release].{key} may use only {{release}}") from None

    pl = _table(raw, "plan", {"after_server"})
    after_server = _get(pl, "[plan]", "after_server", str, None)  # checked against the plugins below

    a = _table(raw, "agents", {"scan_window_h", "silent_stopped_min", "idle_amber_min", "idle_red_min", "rows"})
    agents = AgentsCfg(*(float(_get(a, "[agents]", k, (int, float), d)) for k, d in
                         (("scan_window_h", 48), ("silent_stopped_min", 25), ("idle_amber_min", 20), ("idle_red_min", 55))),
                       rows=_at_least(_get(a, "[agents]", "rows", int, 14), "[agents].rows", 1))
    for key in ("scan_window_h", "silent_stopped_min"):
        if getattr(agents, key) <= 0:
            raise ConfigError(f"[agents].{key} must be more than 0")
    for key in ("idle_amber_min", "idle_red_min"):
        _at_least(getattr(agents, key), f"[agents].{key}", 0)

    tl = _table(raw, "test_logs", {"globs", "max_age_h", "limit"})
    test_logs = TestLogsCfg(_strs(tl, "[test_logs]", "globs", TestLogsCfg.globs),
                            float(_get(tl, "[test_logs]", "max_age_h", (int, float), 2)),
                            _at_least(_get(tl, "[test_logs]", "limit", int, 4), "[test_logs].limit", 0))
    for pattern in test_logs.globs:
        try:
            pattern.format(scratch="scratch", root="root")
        except Exception:  # noqa: BLE001 — every frame would fail on it
            raise ConfigError(f"[test_logs].globs: {pattern!r} may use only {{scratch}} and {{root}}") from None
    if test_logs.max_age_h <= 0:
        raise ConfigError("[test_logs].max_age_h must be more than 0")

    u = _table(raw, "usage", {"probe", "probe_every_min", "probe_fresh_min", "probe_model"})
    usage = UsageCfg(_get(u, "[usage]", "probe", bool, True), float(_get(u, "[usage]", "probe_every_min", (int, float), 20)),
                     float(_get(u, "[usage]", "probe_fresh_min", (int, float), 15)), _get(u, "[usage]", "probe_model", str, "haiku"))
    _at_least(usage.probe_every_min, "[usage].probe_every_min", 5, " (each probe starts a Claude CLI)")
    _at_least(usage.probe_fresh_min, "[usage].probe_fresh_min", 0)

    n = _table(raw, "network", {"tools", "subcommands", "cases", "dashboard_markers", "check_internet"})
    subs = _get(n, "[network]", "subcommands", dict, {})
    for tool, words_ in subs.items():
        if not (isinstance(words_, list) and all(isinstance(x, str) for x in words_)):
            raise ConfigError(f"[network.subcommands].{tool} must be a list of text")
    cases = []
    for i, case in enumerate(_get(n, "[network]", "cases", list, [])):
        if not isinstance(case, dict) or set(case) != {"command", "label"} or not all(isinstance(v, str) for v in case.values()):
            raise ConfigError(f"[[network.cases]] #{i + 1} must be {{command = '...', label = '...'}} ('' = not network)")
        cases.append((case["command"], case["label"] or None))
    network = NetworkCfg(_strs(n, "[network]", "tools", ()), {k: tuple(v) for k, v in subs.items()}, tuple(cases),
                         _strs(n, "[network]", "dashboard_markers", ()), _get(n, "[network]", "check_internet", bool, True))

    lay = _table(raw, "layout", {"max_width", "panels"})
    panels = _strs(lay, "[layout]", "panels", DEFAULT_PANELS)
    for pan in panels:
        if pan not in KNOWN_PANELS and not pan.startswith("plugin:"):
            raise ConfigError(f"[layout].panels: unknown panel {pan!r}")
    layout = LayoutCfg(_at_least(_get(lay, "[layout]", "max_width", int, 150), "[layout].max_width", 60), panels)

    lg = _table(raw, "logo", {"enabled", "path", "viewbox", "split_x", "colours", "caption", "caption_style"})
    colours = _strs(lg, "[logo]", "colours", LogoCfg.colours)
    if len(colours) != 2 or not all(re.fullmatch(r"#[0-9a-fA-F]{6}", x) for x in colours):
        raise ConfigError("[logo].colours must be two colours like '#A221D9'")
    logo_path = _get(lg, "[logo]", "path", str, DEFAULT_LOGO_PATH)
    viewbox = float(_get(lg, "[logo]", "viewbox", (int, float), 150))
    if viewbox <= 0:
        raise ConfigError("[logo].viewbox must be more than 0")
    segs = parse_logo_path(logo_path)
    if any(not (0 <= v <= viewbox) for seg in segs for pt in seg[1:] for v in pt):
        raise ConfigError(f"[logo].path leaves its {viewbox:g}x{viewbox:g} viewbox")
    logo = LogoCfg(_get(lg, "[logo]", "enabled", bool, True), logo_path, viewbox,
                   float(_get(lg, "[logo]", "split_x", (int, float), 85.0)), (colours[0], colours[1]),
                   _get(lg, "[logo]", "caption", str, DEFAULT_CAPTION), _get(lg, "[logo]", "caption_style", str, None), segs)
    if logo.enabled and "usage" in panels and panels[-1] != "usage":
        # The live view finds the turning logo by its place at the very end of the body.
        raise ConfigError("[layout].panels: 'usage' must come last while [logo] is enabled")

    th = _table(raw, "theme", set(Theme.__dataclass_fields__))
    try:
        theme = theme_from(th)
    except ValueError as e:
        raise ConfigError(str(e)) from None

    cp = _table(raw, "compat", {"legacy_scroll_env"})
    compat = CompatCfg(_get(cp, "[compat]", "legacy_scroll_env", str, None))

    plugins = []
    raw_plugins = raw.get("plugins", [])
    if not isinstance(raw_plugins, list):
        raise ConfigError("plugins must be [[plugins]] tables")
    for i, spec in enumerate(raw_plugins):
        where = f"[[plugins]] #{i + 1}"
        if not isinstance(spec, dict):
            raise ConfigError(f"{where} must be a table")
        for k in spec:
            if k not in ("name", "file", "module", "class", "options"):
                raise ConfigError(f"unknown key {where}.{k}")
        pname = _get(spec, where, "name", str, None)
        cls = _get(spec, where, "class", str, None)
        pfile = _get(spec, where, "file", str, None)
        pmod = _get(spec, where, "module", str, None)
        if not pname or not cls or (pfile is None) == (pmod is None):
            raise ConfigError(f"{where} needs a name, a class, and either file or module")
        if any(x.name == pname for x in plugins):
            raise ConfigError(f"{where}: two plugins are named {pname!r}")
        plugins.append(PluginSpec(pname, cls, _path(base, pfile) if pfile else None, pmod,
                                  dict(_get(spec, where, "options", dict, {}))))
    # A name that matches no plugin would load and silently do nothing.
    names = {x.name for x in plugins}
    if after_server is not None and after_server not in names:
        raise ConfigError(f"[plan].after_server: no [[plugins]] entry is named {after_server!r}")
    for pan in panels:
        if pan.startswith("plugin:") and pan[7:] not in names:
            raise ConfigError(f"[layout].panels: {pan!r} names no [[plugins]] entry")

    return Config(
        path=path, dir=base, name=name, title=_get(p, "[project]", "title", str, name.upper()),
        subtitle=_get(p, "[project]", "subtitle", str, "mission control"), root=root, claude_slug=slug,
        projects_dir=projects_dir, claude_projects_root=projects_root, scratch_dir=scratch,
        plan_file=_path(base, _get(f, "[files]", "plan", str, "status_plan.json")),
        notes_file=_path(base, _get(f, "[files]", "notes", str, "status_notes.json")),
        cache_dir=cache, usage_dir=usage_dir(), git=git, github=github, release=release, after_server=after_server,
        agents=agents, test_logs=test_logs, usage=usage, network=network, layout=layout, logo=logo, theme=theme,
        compat=compat, plugins=tuple(plugins))


def find_config(*, config: str | None = None, launcher: str | None = None, project: str | None = None,
                cwd: Path | None = None) -> Path:
    """Where the config is: --config, else beside --launcher, else <--project or cwd>/.claude/."""
    if config:
        return Path(config).expanduser().resolve()
    if launcher:
        return Path(launcher).expanduser().resolve().parent / CONFIG_NAME
    base = Path(project).expanduser().resolve() if project else (cwd or Path.cwd())
    return base / ".claude" / CONFIG_NAME

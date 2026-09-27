# lsw-mission-control: architecture

How the engine is put together, for anyone changing it. Using it is covered by the README,
[config.md](config.md), [plan-schema.md](plan-schema.md) and [plugins.md](plugins.md); the rules for
changing it are in [CLAUDE.md](../CLAUDE.md).

## 1. Repository and package layout

```
lsw-mission-control/
  pyproject.toml          hatchling; requires-python >=3.11; rich>=15,<16; [dev] pytest, ruff, pyte;
                          script lsw-mc; pytest pythonpath=src, -p no:cacheprovider; ruff cache in ~/.cache
  constraints.txt         the rich release the golden renders were made with
  README.md CLAUDE.md CHANGELOG.md
  docs/                   architecture.md config.md plan-schema.md plugins.md claude-md-snippet.md
  src/lsw_mission_control/
    __init__.py           __version__; sets sys.pycache_prefix when unset
    __main__.py cli.py    the command line
    tools.py              init / validate / doctor
    config.py             Config and its sections, load_config, find_config, claude_slug, usage_dir, ConfigError
    theme.py              Theme, LSW_DARK, the palette C, use(), theme_from()
    util.py               now/set_clock/local_now, iso, human, ago, clock, run, num, write_json, fit, count
    store.py              Store (thread-safe; get/set/update/snapshot, data under lock)
    plan.py               parse_stage, parse_plan, Item/OtherItem/NextItem/NextRelease/Plan, PlanLoader
    notes.py              Notes, read_notes, NotesLoader
    agents.py             transcript_facts, scan_agents, final_merge_labels, label_names, findings_of, stored_ok,
                          store_since, FinishedStore, latest_by_label, review_needs_fix
    progress.py           Prog, Calibration, calibrate, progress_of, eta_from_json, stages_progress,
                          stages_started, wait_for, in_wait_order, item_progress, item_started, item_minutes,
                          short_name
    net.py                NetRules, net_label, NET_CASES, FLAG_CASES, is_dashboard, etime_seconds,
                          network_critical, fit_list, network_flag, flag_verdict, check_net_cases
    plugin.py             Plugin, PluginContext, CliFlag, LiveJob, SideCard, Flags, validate_options, load_plugins
    engine.py             Frame, Engine (sources, frames, safe_frame, render), error_where
    app.py                Scroll, apply_keys, keys_loop, run_once, run_live
    reload.py             Reloader, user_args
    statusline.py         the Claude Code status line (stdlib, Python 3.9, runnable by path)
    testing.py            freeze, record_console, render_text, render_engine
    sources/              Source base; git.py GitSource; github.py GitHubSource; tokens.py TokenCounter,
                          model_family; usage_probe.py probe_usage, UsageProbe; testlogs.py test_logs, suite_colour
    render/               widgets.py frame.py notes.py release.py next_release.py other.py side.py agents.py
                          usage.py logo.py guard.py
    templates/            launcher.py statusline_shim.py mission-control.toml status_plan.json status_notes.json
  tests/                  conftest.py scenarios.py golden_util.py plugins/stub_plugin.py test_*.py golden/…
```

Import rule: `render/*` imports `util`, `theme`, `plan`, `progress`, `widgets`, and the pure
`net.network_flag` / `sources.testlogs.suite_colour`; it never starts or polls a source. Plugins
import only `lsw_mission_control.plugin` (its `__all__`), `render.widgets`, `render.side`, `util`
and `theme`.

### The launcher and the status line

`templates/launcher.py` becomes a project's `.claude/status.py`. It is standard-library only and
Python 3.9-compatible, and never imports the package. `ENGINE_SRC` is written by `lsw-mc init`;
`$LSW_MC_SRC` and `$LSW_MC_VENV` override it. The venv is ready when its `.lsw-mc-installed`
marker exists and its interpreter resolves; otherwise the launcher rebuilds it (`venv --clear`,
`pip install -c constraints.txt -e <engine>`, the pycache `.pth`, the marker last). With
`--check-net` in argv it never installs: it says the venv is missing and exits 1. Then it execs
`python -P -m lsw_mission_control --launcher <its own path> <args>` with
`PYTHONPYCACHEPREFIX=~/.cache/lsw-mission-control/pycache`. `--launcher` is load-bearing: the
config is found beside it, and the engine reloads through it (§6).

`statusline.py` writes `{"at", "rate_limits"}` to `$LSW_MC_USAGE_DIR` or
`~/.cache/lsw-mission-control/usage.json`, prints `Model · 5h N% · wk N%`, and never fails.
`templates/statusline_shim.py` runs it by path with `runpy`.

---

## 2. Engine core

```python
util.now() / set_clock(fn) / local_now()            # LSW_MC_NOW or set_clock, else time.time()
Theme(bg, surface, border, text, muted, faint, accent, accent_soft, green, amber, red, red_soft)
C.GREEN …                                             # the palette in use; theme.use(theme) at start-up
Store: lock, get, set, update, snapshot, data         # keys: git, release_gh, gh_timing, tokens, tokens_by_model
Plan(release, items, other, next, plugin_data, pre); Item(name, key, build, review, fix, flags, before)
OtherItem(name, stages, paused, after, after_server); NextItem(name, key, group, stages, flags)
PlanLoader(path, plugins).refresh() -> Plan           # mtime-gated; a bad parse keeps the last good plan; .note
Notes(waiting_on_owner, in_progress_elsewhere, mtime); NotesLoader(path).refresh() -> Notes
FinishedStore(path, readonly).merge(agents, plan, names, loaded, release_bound)   # release_bound: the final
                                                      # merge's labels; an agent of one begun before the
                                                      # store's `since` comes back marked earlier_release
Calibration(fix_share): factors{build,review,fix}, fix_share, n; get(stage) matches only those three names
calibrate(cal, items, labels)                         # in place: a factor moves only at >= 3 samples,
                                                      # the fix share only at >= 3 reviews
stages_progress(stages, labels, *, now, cal, default_fix_share, wait_before, after, done_before, paused)
wait_for([(ref, Prog)], rerun) -> (seconds | None, ref) | None   # what a row still waits for; None:
                                                      # nothing; a failed target has no time unless rerun
in_wait_order(n, targets_of, compute, rerun) -> [Prog]   # each row after its targets (release, other, next)
Frame(now, width, cfg, plan, plan_note, reload_note, notes, agents, labels, names, cal, store, flags,
      live_job, live_job_error, plugin_errors, notes_note)
Engine(cfg, flags, *, readonly, plugins, load_errors, no_plugins)
  .start_sources(probe) .ready() .build_frame(width) .frame(console) .safe_frame(console) .render(console)
  .errors_for_once() .watched_files() .network_critical(); .ps_text (injectable)
```

`Engine.frame` runs in this order: refresh the plan, fix the width (`min(console width,
max_width)`), scan the transcripts and merge the finished store, `latest_by_label`, `calibrate`,
build the release and next-release panels, then the title and the panels in `[layout] panels`
order, then `network_critical()`. A panel that fails to build or to draw is replaced by an error
panel in its own place; the rest of the frame draws.

---

## 3. Sources (background pollers)

| Source | Interval | Store keys | Notes |
|---|---|---|---|
| `GitSource` | `[git] poll_s` 20 | `git` | HEAD, unpushed vs `<remote>/<main>`, branches, worktrees − 1, dirty |
| `GitHubSource` | `[github] poll_s` 120; timing every 600 | `release_gh`, `gh_timing` | the current plan's release via a callable; a failed call keeps the last answer for the same release |
| `TokenCounter` | 30 s | `tokens`, `tokens_by_model` | all `~/.claude/projects/**/*.jsonl` of 8 days; `<usage dir>/tokens.json` |
| `UsageProbe` | 20 min, skipped while usage.json < 15 min old | writes `<usage dir>/usage.json` | `claude -p --model haiku … ok` in `<usage dir>/probe-cwd`, killed at the first `rate_limit_event` or 60 s; never under `--once` |
| test logs | at render time | — | `test_logs(globs, now, max_age_s, limit)` |

Each runs `poll_once()` in a daemon thread and never dies (an unexpected exception waits for the
next round). Without `[github]` there is no GitHub polling, and the tag row keeps its planned timing.

---

## 4. Plugin API (`lsw_mission_control.plugin`)

```python
CliFlag(flag, help)
LiveJob(left, frac, eta, eta_life, live, stalled_label="stalled")
SideCard(title, grid, subtitle="", subtitle_style=None)
Flags(argv).has(flag)
PluginContext(name, options, cfg, flags, state=Store()): root, cache_dir, run(), spawn(), write_json(), now()
class Plugin:
    name; cli_flags
    net_labels -> tuple[str, ...]        # property: its own children's labels ("ssh → <host>")
    parse_plan(raw) -> object            # raise to reject the plan
    start(); ready() -> bool
    side_card(width, frame) -> SideCard | None
    panel(width, frame) -> RenderableType | None
    live_job(frame) -> LiveJob | None
validate_options(plugin, options, required, optional) -> dict    # unknown or missing key: ConfigError
load_plugins(cfg, flags) -> (plugins, [(name, "Type: msg")])     # file: spec_from_file_location as
                                                                 # lsw_mc_plugin_<name>, registered in sys.modules
```

Engine wiring:

- **Side row**: `n = plugins that draw a card + plugins that failed to load + 1` (Repository);
  each card is asked for `(width − (n − 1)) // n` columns, grids are padded to one height, and laid
  out as n `ratio=1` columns.
- **after_server**: the live job of `[plan] after_server`'s plugin (or of the only plugin that
  implements `live_job`). `None`: the row has no job to wait on; `left == 0`: done; otherwise the
  row is weighted by the job's progress and shows `stalled_label` when the job stalls.
- **Errors**: `side_card`/`panel`/`live_job` exceptions, and results of the wrong type, are caught
  per plugin and recorded for the frame: the plugin's card shows `error  Type: message` (wrapped),
  a failed `live_job` also turns the waiting rows red (`✕ … plugin error`), and `--once` writes the
  tracebacks to stderr and exits 1. A plugin that fails to load (import error, bad class,
  `validate_options`) shows an error card in live mode and fails `--check-net`/`--self-check`.
- **Plan ownership**: `parse_plan(raw)` runs inside `PlanLoader.refresh()`; an exception rejects the
  whole plan exactly as a malformed engine key does.

---

## 5. Plan, notes, caches

The plan and notes schemas are in [plan-schema.md](plan-schema.md). `parse_plan` ignores unknown
keys, coerces `bool(paused)`, `bool(after_server)` and `str(flag)`, and refuses values that could
only be typos (the last good plan stays, and the title says why). `after_live` is accepted as an
alias of `after_server`. A notes file that does not load keeps the last good notes the same way.

| What | Where | Scope | Writer(s) |
|---|---|---|---|
| `usage.json`, `tokens.json`, `probe-cwd/` | `$LSW_MC_USAGE_DIR` or `~/.cache/lsw-mission-control/` | account | status line, probe, every TokenCounter (atomic writes) |
| `pycache/`, `ruff/` | `~/.cache/lsw-mission-control/` | machine | Python, ruff |
| `finished.json` | `[cache] dir` (default `~/.cache/lsw-mission-control/projects/<name>`) | project | FinishedStore (never in read-only mode): `{"release", "since", "agents", "previous"}` (`previous`: the release left at the last change, its `since` and the records the change dropped); `since` absent, or later than now, reads as 0 |
| a plugin's own files | its `ctx.cache_dir` (the same folder) | project | the plugin |

Tests and rehearsals point `--cache-dir`/`LSW_MC_CACHE_DIR` and `LSW_MC_USAGE_DIR` at copies:
two writers of one real `finished.json` must never race.

---

## 6. Live reload (`reload.py`)

- **Watch**: every `*.py` under the package (rescanned, so a new or deleted module counts), every
  plugin file, the config, and the launcher. Not the plan or notes (re-read on every refresh).
  Only files that can be modules count (an editor's lock file or a sync tool's copy does not).
- **Settle**: act only once the newest changed file is ≥ 2 s old.
- **Gate**: compile every changed `.py` (`SyntaxError, line N`), then run
  `[sys.executable, <launcher>, "--check-net", "--self-check"]` (no launcher: `-m lsw_mission_control
  <user args> --check-net --self-check`), 30 s. Pass = the output contains `network cases correct`
  **and** exit 0. Otherwise the title says `edit to <label> not loaded: <reason>` (the reason is the
  last stderr line, else the first `MISMATCH` line, else "it did not start" / "its self-check
  failed"). The mtimes are recorded, so only the next edit retries; the note stays until a reload
  succeeds.
- **Exec**: stop Live, restore the terminal, set `LSW_MC_SCROLL` (and `[compat] legacy_scroll_env`
  when set), `os.execv(sys.executable, [sys.executable, <launcher>, *user args])`: the same window
  keeps its scroll position, and replacing the launcher file reloads the window into whatever the
  file now holds.
- `--check-net` on its own also imports every module and loads every plugin, so a gate that runs
  only it still refuses a broken edit. `--self-check` renders one frame through the live `Scroll`
  at 150×50 into a null console, turns the logo once and feeds the key parser.

---

## 7. Network indicator (`net.py`)

- `NET_TOOLS`, `NET_SUBCOMMANDS`, `GIT_VALUE_OPTS`, `COMPOSE_VERBS` and `net_label` decide which
  command lines are network work; `fit_list`, `network_flag` and `flag_verdict` draw the flag.
- `NET_CASES` and `FLAG_CASES` are real `ps` lines, with placeholder hosts, repositories, images and
  paths (`hostname`, `example/project`, `/Users/x/…`), each with the label it must get.
  `check_net_cases` checks them and a project's `[[network.cases]]` (`N/N network cases correct`,
  `N/N indicator cases correct`).
- `NetRules.with_extras(tools, subcommands)` merges a project's `[network]`.
- `is_dashboard(cmd, markers)`: a launcher's `.claude/status.py`, `-m lsw_mission_control` as argv
  tokens after the interpreter's options, `lsw-mc` as argv[0] or as the script of a Python argv[0],
  or a config marker.
- `network_critical(rules, markers, own_labels, ps_text=None, run=None, self_pid=None)`: skips
  dashboards, processes younger than 8 s, and a dashboard's child **only when its label is one of
  `own_labels`** (`gh` and every plugin's `net_labels`), so a launcher's first-run `pip install` is
  listed. It returns `None` (drawn as `network check failed`, never "safe") when `ps` gives nothing
  or, given `self_pid`, a table without the dashboard's own process.

---

## 8. Behaviour inventory

Launch and modes:

- `--once`, `--no-usage-probe`, `--check-net`, `--self-check` and plugin flags are matched as
  exact tokens; unknown flags are ignored; `--check-net` prints its two lines and exits 1 on a
  mismatch.
- A first run builds the venv with a notice on stderr.
- `--once` waits ≤ 25 s (git, GitHub's first round if configured, every plugin ready), prints one
  frame, and exits 1 with the traceback on any frame or plugin error.
- Live: the first frame after 1.5 s, built before `Live` starts; data every 5 s;
  `Live(screen=True, auto_refresh=False)`; the logo turns at 8 fps by rewriting only its cells, on
  the main loop (a frozen dashboard stops turning), and stops for good on an error.
- Keys ↑/k ↓/j PgUp/b PgDn/space Home/g End/G q/Q Ctrl+C, cbreak without echo, partial escapes
  buffered, a key redraws at once; no key thread and no cbreak when stdin is not a TTY; the
  terminal restored on exit.
- The scroll window: the bottom row pinned with `▲N ▼N`, a page is the view − 2, the offset kept
  across a reload; a failing body shows the `Mission control error` panel from `Scroll` itself.
- Self-reload only after the gate (§6); the plan re-read on mtime with the last good plan kept
  (`status_plan.json not loaded (…)` / `… is missing`); a frame error becomes an error panel with
  the network row still checked; the width is capped at `[layout] max_width` (150).

Panels:

- The title: the project chip, `  mission control`, `%a %d %b · %H:%M:%S`, and any notes.
- Waiting on you.
- Release: the head meter (≤ 99% until released), `done/n items ready`, failed and overrun counts,
  `release out HH:MM (in X)` / `released HH:MM`, live rows, `● N finished`, the final merge and the
  tag row (`[release] tag_row`), `after:` / `after_all` / `owner_ok`, a `null` key is done; stage
  dots; each row's state; ETA colours and the Key; calibration (release items only, ≥ 3 samples,
  0.2–2.0, sticky, the fix share from ≥ 3 reviews); stage timing; the finished store (pruned on a
  release change and saved at once with `since`, the final merge's records dropped with the shipped
  release's and given back if the release moves straight back, a final-merge agent begun before
  `since` counted for no row, malformed records dropped,
  a running record rewritten at most every 10 min); waits (plan-schema.md, "Waits": every `after:`
  flag, a target listed later, a begun row, a target with no finish time or failed);
  silence over 25 min means stopped; the tag row's phases and timing (the median of successful
  runs, else `[release] fallback_minutes`); milestone wording.
- Next release.
- Other work in progress, with `after` (the same waits), `after_server` (unknown, done, running, not
  live, stalled, plugin error), paused rows, the head and `also in motion`.
- The side row: plugin cards, then Repository.
- Agents at work and the recent test suites.
- Model usage: meters, stale data as a faint meter with a muted % and an amber `· as of HH:MM`,
  resets (and a passed reset), status (`warning: close to a limit`, `limit reached`, others),
  `using usage credits`, tokens and tokens by model, the subtitle
  `plan data as of HH:MM · X ago · probe|terminal`, placeholders while there is no data.
- The logo beside Model usage from 100 columns when usage is ≥ 5 rows: square, two colours,
  turning, with the `[logo] caption` at its bottom right in faint.
- The network row; several dashboards at once; the palette (`[theme]`).

---

## 9. Tests

`tests/test_*.py` cover each module; `tests/golden/` holds whole frames at 80/100/120/150 columns
(plain and styled), the tag row's phases, the usage states, the `after_server` states, the bottom
row, a broken plan and notes, the frame-error panel and logo frames. All their data is synthetic
(`tests/scenarios.py` and a stub plugin), generated at fixed offsets from a frozen clock.
`pytest --update-golden` rewrites the goldens; every changed line is reviewed before a commit.

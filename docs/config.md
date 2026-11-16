# Config reference: `<project>/.claude/mission-control.toml`

TOML, hand-edited, with comments. The engine finds it beside the launcher (`--launcher`), at
`--config FILE`, or at `<--project DIR or the current dir>/.claude/mission-control.toml`.

- Paths are relative to **the config file's directory** unless absolute; `~` is expanded.
- **An unknown key is an error** (`ConfigError: unknown key [release].tag_prefx`), so a typo never
  falls back to a default in silence. So is a value out of its range, or a name that matches no plugin. A config that does not load fails `--check-net`, so a running
  dashboard keeps its old code and says why in its title.
- **No secrets.** A config file must be safe to commit. Authentication comes only from
  `~/.ssh/config` and the ssh agent, and from `gh`'s own keyring; a plugin that needs a host names
  the host, never a password or token.

## Keys

| Key | Type | Default | What it does |
|---|---|---|---|
| `schema` | int | required | must be `1` |
| `[project] name` | str | the project dir's name | used in the default cache dir |
| `[project] title` | str | `name.upper()` | the chip at the top left |
| `[project] subtitle` | str | `"mission control"` | beside the chip |
| `[project] root` | path | `..` (the config dir's parent) | the git repository |
| `[project] claude_slug` | str | the root with every non-alphanumeric character turned into `-` | Claude Code's folder name for the project |
| `[project] projects_dir` | path | `~/.claude/projects/<slug>` | where this project's workflow journals are |
| `[project] scratch_dir` | path | `/tmp/claude-<uid>/<slug>` (resolved) | the session scratchpads (test logs) |
| `[files] plan` | path | `status_plan.json` | the plan ([plan-schema.md](plan-schema.md)) |
| `[files] notes` | path | `status_notes.json` | the notes |
| `[cache] dir` | path | `~/.cache/lsw-mission-control/projects/<name>` | `finished.json` (every tracked stage this release) and plugin caches; `--cache-dir` / `LSW_MC_CACHE_DIR` override it |
| `[git] main_branch` | str | `"main"` | |
| `[git] remote` | str | `"origin"` | unpushed = `<remote>/<main>..<main>` |
| `[git] poll_s` | int | `20` | seconds, at least 2 |
| `[github] repo` | str | absent: no GitHub polling | `OWNER/NAME` |
| `[github] ci_workflow` | str | `"CI"` | the workflow whose push runs on main are "CI" |
| `[github] release_workflow` | str | `"Release"` | the workflow run on the release tag |
| `[github] poll_s` / `timing_poll_s` | int | `120` / `600` | signals / run-length medians (seconds, at least 30 / 60: GitHub rate-limits) |
| `[release] enabled` | bool | `true` | draw the release panel |
| `[release] tag_prefix` | str | `"v"` | the tag is `<prefix><release>` |
| `[release] title` | str | `"Release {release}"` | the panel's title |
| `[release] next_title` | str | `"Next release {release}"` | |
| `[release] later_title` | str | `"Later release {release}"` | each release after the next (the plan's `later`), drawn right after the next release wherever `next` sits in `[layout] panels` (a list without `next` draws neither) |
| `[release] later_wait` | str | `"after {release}"` | the stage of a later release's item not yet begun (one whose first stage is the owner's, `your …`, reads that); `{release}` is the release before it (`"{release} installed"`) |
| `[release] tag_row` | str | `"Tag {release}"` | the last row's name |
| `[release] tag_todo_text` | str | `"CI, tag, release"` | the last row's stages before it starts |
| `[release.words]` | table | `done_share = "of the release done"`, `ready = "items ready"`, `out = "release out"`, `released = "released"` | the head line's wording |
| `[release] fallback_minutes` | int | `150` | CI + release run + hands-on steps, until GitHub timings load (at least the other two) |
| `[release] hands_minutes` | int | `20` | the hands-on steps between CI and the release run |
| `[release] release_run_minutes` | int | `15` | the release run, until timings load |
| `[release] fix_share` | float | `0.7` | the share of reviews that lead to a fix, until 3 reviews calibrate it (0 to 1) |
| `[release.final_merge] key` / `name` | str | `"final-merge"` / `"Final merge"` | agents `build:<key>`, `review:<key>`, `fix:<key>` |
| `[release.final_merge] stages` | `[[name, minutes]]` | `[["build",120],["review",30],["fix",60]]` | its planned stages |
| `[plan] after_server` | str | the only plugin with a live job | the plugin whose live job `after_server` rows follow (a `[[plugins]]` name) |
| `[agents] scan_window_h` | num | `48` | journals older than this are left to the finished store; the transcript of an agent a session launched itself, last written before it, is not read |
| `[agents] silent_stopped_min` | num | `25` | a running agent silent longer than this has stopped (one whose run the Workflow runtime recorded as ended, stopped then, reads stopped at once) |
| `[agents] idle_amber_min` / `idle_red_min` | num | `20` / `55` | the "active" column's colours |
| `[agents] rows` | int | `14` | more running agents than this: the quiet ones first, then `+N more` (at least 1) |
| `[agents] extra_dirs` | [str] | `[]` | more folders laid out as a projects folder (`<run>/subagents/agent-<id>.jsonl` beside `agent-<id>.meta.json`, `{"agentType", "description"}`): agents a job launches outside Claude Code, such as Agent SDK passes billed to the API, listed in Agents at work with a session's own |
| `[test_logs] globs` | [str] | `["{scratch}/*/scratchpad/*.log"]` | `{scratch}` and `{root}` are filled in (no other `{…}`) |
| `[test_logs] max_age_h` / `limit` | num / int | `2` / `4` | |
| `[compute] enabled` | bool | `true` | the Compute panel: the heavy jobs here and on the plugins' remotes (below) |
| `[compute] descriptors` | str | none | the folder long jobs write their descriptors (`*.json`, below) into; `~` and relative paths as `[files]` |
| `[compute] min_cores` | num | `1` | a process group (or VM) no descriptor names is listed from this much CPU |
| `[compute] exclude` | [str] | `[]` | more command substrings that are never a job (beside the system's programs, desktop apps, editors, `claude` and every mission-control window) |
| `[compute] poll_s` / `ended_min` | int / num | `10` / `10` | how often `ps` is sampled (at least 2); how long a descriptor's job that ended reads `ended HH:MM` |
| `[compute] local_name` | str | `"this computer"` | what this computer's rows say they run on (`"this Mac"`, its host name) |
| `[usage] probe` | bool | `true` | the plan-limit probe (the CLI flag `--no-usage-probe` also stops it) |
| `[usage] probe_every_min` / `probe_fresh_min` | num | `20` / `15` | its period (at least 5), and the age below which a round is skipped; data older than `every + 5` min shows as stale |
| `[usage] probe_model` | str | `"haiku"` | |
| `[usage.credits] usd` | num | none (no row) | a prepaid balance shown under the plan limits, e.g. the Console's API credits, which the plan limits never see: its row ("API") reads the share of `usd` the spend files record |
| `[usage.credits] spend` | [str] | `[]` | globs of the spend files (`~` expanded, relative ones from the config's folder): each a JSON object whose `total_cost_usd` counts (the Agent SDK's result, `claude -p --output-format json`) |
| `[usage.credits] until` / `label` | str | `""` / `"API"` | the day the credits expire (`2026-11-27`: the row reads `until 27 Nov`, then `expired`); the row's label (up to 12 characters) |
| `[network] tools` | [str] | `[]` | added to the engine's network tools |
| `[network.subcommands]` | {tool: [str]} | `{}` | merged into the engine's (`hg = ["pull", "push"]`) |
| `[[network.cases]]` | `{command, label}` | none | real process lines and their labels (`label = ""`: not network); `--check-net` checks them too |
| `[network] dashboard_markers` | [str] | `[]` | more command-line substrings that mark a dashboard process |
| `[network] check_internet` | bool | `true` | the indicator's "not connected" state: a request to `http://captive.apple.com/hotspot-detect.html` every 5 s from the dashboard's own process (README, "The network indicator"); `false`: none |
| `[layout] max_width` | int | `150` | every panel shares one right edge at most this far (at least 60) |
| `[layout] panels` | [str] | `["notes","release","next","other","compute","side","agents","usage"]` | order and choice; `plugin:<name>` places a plugin's full-width panel (a `[[plugins]]` name); `usage` must stay last while the logo is on; a list that names `other` but not `compute` draws Compute right after Other work in progress |
| `[logo] enabled` | bool | `true` | the logo beside Model usage, in the room Model usage leaves (it never clips it) |
| `[logo] path` | str | the LSW logo | an SVG path with absolute `M`, `L` and `Q` commands only |
| `[logo] viewbox` | num | `150` | the path's square box (its points must lie inside) |
| `[logo] split_x` | num | `85.0` | left of this x the first colour, right of it the second |
| `[logo] colours` | [str, str] | `["#A221D9", "#D96D21"]` | |
| `[logo] caption` | str | `"λ∿ 2026"` | at the panel's bottom right; `""` for none |
| `[logo] caption_style` | str | the theme's faint | a rich style |
| `[theme] <name>` | `#rrggbb` | the LSW dark palette | `bg surface border text muted faint accent accent_soft green amber red red_soft` |
| `[compat] legacy_scroll_env` | str | none | an older dashboard's scroll variable: read at start and set on reload, so a swap or a rollback keeps the scroll position |
| `[[plugins]] name` / `class` | str | | the plugin ([plugins.md](plugins.md)) |
| `[[plugins]] file` or `module` | str | | a file (relative to the config) or an importable module |
| `[plugins.options]` | table | `{}` | the plugin's own options; it validates them (an unknown key fails the load) |

The usage dir is not a config key: it is account-wide and must match the status line's, so it is
`$LSW_MC_USAGE_DIR` or `~/.cache/lsw-mission-control`.

## Example

```toml
# lsw-mission-control config for acme (safe to commit: no secrets)
schema = 1

[project]
name = "acme"
title = "ACME"

[github]
repo = "example/acme"

[release]
tag_row = "Tag {release} + packages"

[release.final_merge]
name = "Final merge + full CI"
stages = [["build", 90], ["review", 30], ["fix", 45]]

[plan]
after_server = "queue"

[[plugins]]
name = "queue"
file = "mc_queue.py"
class = "QueuePlugin"

[plugins.options]
host = "build-box"
poll_s = 60
```

## Compute jobs

Every `[compute] poll_s` the dashboard samples `ps`, and the Compute panel lists, one row each, where
each heavy job runs (`machine`, named once for its rows), what it is (`job`: its label, then its
detail dimmed where there is room, cut at a word), its `state` (`running`; `waiting`; a state its
source names, such as `frozen (battery)` or `waiting (memory)`, in amber; `ended HH:MM`; `read Nm
ago` once a remote's answer is 5 min old), how long it has run, its CPU (`cores`, from its CPU
time's growth) and `memory`, and, when its progress is known, `done/total` (headed by what it counts
when every row counts the same thing), the release panel's bar with the share done, and a finish
from the rate it shows (over the last 3 h of this session, else since it started); a job that is
not running has no finish (`—`). The progress and eta columns are the release table's, at the same
width and place. In a narrow window the detail goes first, then memory, running and cores. The
panel's foot counts the rows by state. Nothing heavy running draws no panel. The rows:

- each **descriptor** in `[compute] descriptors`: a JSON file a long job writes when it starts (and
  removes when it is done; one whose job has gone stops showing, after `ended HH:MM` when this
  session saw it run):

  ```json
  {"label": "Nightly data import",       "machine": "local",
   "match": {"pgid": 4242},               "vm": "work",
   "progress": {"file": "~/jobs/nightly/state.log", "regex": "units=(\\d+)/(\\d+)", "fresh_min": 15},
   "total": 174, "unit": "files", "started": "2026-09-21T10:00:00Z", "detail": "pass 2",
   "state": {"file": "~/jobs/nightly/pause.log", "when": {"froze": "frozen (battery)", "thawed": ""}}}
  ```

  `label` is required. `machine` is `local` (the default) or a remote a plugin reports. `match`, on
  this computer: `pid`, `pgid` or `pidfile` (a number, a list, or a file holding the pid): the job
  shows while one runs, and their processes (a pid's descendants, a group's members) are its CPU;
  on a remote: `container` (names) or `container_prefix`, matched against the names the plugin's
  rows carry, plus optionally a local `pid`/`pgid` of the job's driver, so that it reads `waiting`
  while the driver runs and nothing of it does there. `vm`: a colima profile (or `lima <name>`)
  whose VM's CPU and memory are this job's (it then has no row of its own). `progress`, from a
  local file: `{"file", "count": "lines", "match"}` (lines, those holding `match`), `{"glob"}`
  (files), `{"file", "field": "a.b", "total_field"}` (JSON numbers) or `{"file", "regex"}` (the
  last match in its end: group 1 done, group 2 the total), each with `fresh_min` (an older file is
  no progress, unless its `state` says it is not running) and `total`. `unit`: what its progress
  counts. `state`: the last line in the end of `file` that holds one of `when`'s substrings gives
  that substring's state (`""`: running); no such line, or no file, is running. A file that does
  not load shows as `not read: <file>: <why>`.
- every other **process group** on this computer at `min_cores` or more, by its heaviest command,
  and each **VM** (macOS's Virtualization framework: colima and lima, named by their instance);
- each **plugin's remote jobs** (`Plugin.compute_jobs`, [plugins.md](plugins.md)), with a remote
  descriptor's label when one names them (and its `unit` and `state`, before the plugin's).

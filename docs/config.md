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
| `[agents] scan_window_h` | num | `48` | journals older than this are left to the finished store |
| `[agents] silent_stopped_min` | num | `25` | a running agent silent longer than this has stopped |
| `[agents] idle_amber_min` / `idle_red_min` | num | `20` / `55` | the "active" column's colours |
| `[agents] rows` | int | `14` | more running agents than this: the quiet ones first, then `+N more` (at least 1) |
| `[test_logs] globs` | [str] | `["{scratch}/*/scratchpad/*.log"]` | `{scratch}` and `{root}` are filled in (no other `{…}`) |
| `[test_logs] max_age_h` / `limit` | num / int | `2` / `4` | |
| `[usage] probe` | bool | `true` | the plan-limit probe (the CLI flag `--no-usage-probe` also stops it) |
| `[usage] probe_every_min` / `probe_fresh_min` | num | `20` / `15` | its period (at least 5), and the age below which a round is skipped; data older than `every + 5` min shows as stale |
| `[usage] probe_model` | str | `"haiku"` | |
| `[network] tools` | [str] | `[]` | added to the engine's network tools |
| `[network.subcommands]` | {tool: [str]} | `{}` | merged into the engine's (`hg = ["pull", "push"]`) |
| `[[network.cases]]` | `{command, label}` | none | real process lines and their labels (`label = ""`: not network); `--check-net` checks them too |
| `[network] dashboard_markers` | [str] | `[]` | more command-line substrings that mark a dashboard process |
| `[network] check_internet` | bool | `true` | the indicator's "not connected" state: a request to `http://captive.apple.com/hotspot-detect.html` every 5 s from the dashboard's own process (README, "The network indicator"); `false`: none |
| `[layout] max_width` | int | `150` | every panel shares one right edge at most this far (at least 60) |
| `[layout] panels` | [str] | `["notes","release","next","other","side","agents","usage"]` | order and choice; `plugin:<name>` places a plugin's full-width panel (a `[[plugins]]` name); `usage` must stay last while the logo is on |
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

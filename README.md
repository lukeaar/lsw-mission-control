# lsw-mission-control

A live terminal dashboard of everything Claude Code is doing on a project: the release being
built and every item it waits for, other work in progress, the agents at work right now, the
repository and its CI, the plan limits and tokens used, and whether this computer is on the
internet and it is safe to switch networks this second. It reads what is already there — Claude Code's workflow journals and
transcripts under `~/.claude/projects`, git, GitHub through `gh`, the session scratchpads' test
logs — plus two small files a project keeps by hand: a **plan** and **notes**.

It is a generic engine plus, per project, a small config and optional **plugins** (a plugin
adds a panel of its own, for example a live server's job queue, and can own plan keys).

```
 DEMO   mission control   Mon 21 Sep · 14:13:20
╭─  Waiting on you · 2  ─────────────────────────────────────────────────────────── updated 14:08 · 5m ago ─╮
╭─  Release 1.4.0  ──────────────────────────────────────────────────────────────────────────────────────────╮
│ ━━━━━━━━━━━━━━━━━━━━━━━━ 29% of the release done  ·  1/6 items ready  ·  1 failed  ·  release out 23:22    │
│ Export to CSV                         ●─●─●─●─◉      fix              ━━━━━━━━━━━━━  93%        ~10m · 14:23 │
│ Faster startup                          ◉─○─○        build +1h00      ━━━━━━━━━━━━━  64%       ≥1h23 · 15:36 │
│ …                                                                                                          │
Key: ━━ done   ━━ < 1 h   ━━ < 4 h   ━━ > 4 h   ━━ failed                         ● safe to switch networks
```

(Whole frames at 80–150 columns are in `tests/golden/`.)

## Install

Python 3.11 or newer for the engine (a venv of its own); the launcher and the status line run
under any Python 3.9+, including macOS's `/usr/bin/python3`.

```sh
git clone <this repo> ~/code/lsw-mission-control          # anywhere; a project points at it
python3.14 -m venv ~/.venvs/lsw-mission-control
~/.venvs/lsw-mission-control/bin/pip install -c ~/code/lsw-mission-control/constraints.txt \
    -e ~/code/lsw-mission-control'[dev]'
```

`constraints.txt` pins `rich` to the release the golden renders were made with. The install is
editable: the checkout IS the running code, and a running dashboard reloads itself when it
changes (see "Live reload"). A project's launcher builds this venv by itself on its first run if
it is missing, and adopts one you installed by hand (as above) without rebuilding it.

If the checkout lives in a synced folder (Drive, Dropbox), keep bytecode out of it: the launcher
sets `PYTHONPYCACHEPREFIX=~/.cache/lsw-mission-control/pycache`, and the venv gets a
`lsw_mc_pycache.pth` that does the same for every other command (the launcher writes it; after a
manual install, write it yourself):

```sh
echo "import os, sys; sys.pycache_prefix = sys.pycache_prefix or os.path.expanduser('~/.cache/lsw-mission-control/pycache')" \
  > "$(~/.venvs/lsw-mission-control/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/lsw_mc_pycache.pth"
```

## Adopt it in a project

```sh
~/.venvs/lsw-mission-control/bin/lsw-mc init --project ~/code/myproject --github owner/myproject
python3 ~/code/myproject/.claude/status.py
```

`init` writes four files into `<project>/.claude/` (and never overwrites one without `--force`):

| File | What it is |
|---|---|
| `mission-control.toml` | the config ([docs/config.md](docs/config.md)); safe to commit, it holds no secrets |
| `status_plan.json` | the plan: the release, its items, other work ([docs/plan-schema.md](docs/plan-schema.md)) |
| `status_notes.json` | what is waiting on the owner, and work in motion no plan row covers |
| `status.py` | the launcher: stdlib only, 3.9-compatible; it runs the engine from its venv |

Then paste [docs/claude-md-snippet.md](docs/claude-md-snippet.md) into the project's CLAUDE.md, so
the agents keep the plan and notes current and name their workflow agents so the dashboard can
track them (`build:<key>`, `review:<key>`, `fix:<key>`).

`lsw-mc validate --project DIR` parses the config, plugins, plan and notes and says what is
wrong; `lsw-mc doctor --project DIR` also checks the session folder, `gh`, the plan-limit data,
the status-line hook and the venv.

## Run

```
python3 .claude/status.py                  live: refreshes every 5 s; wheel, ↑↓/j k, PgUp/PgDn/b space,
                                           g/G (top/end), q or Ctrl+C to quit
python3 .claude/status.py --once           one snapshot (exit 1, traceback on stderr, if anything failed)
python3 .claude/status.py --no-usage-probe never ask the CLI for plan limits
python3 .claude/status.py --check-net      check the network indicator; exit 1 on any mismatch
python3 .claude/status.py -h               every flag, a plugin's included
```

The same through the engine directly: `lsw-mc --project DIR [flags]` (or `--config FILE`).
Flags are matched exactly: `--check` is not `--check-net`, and an unknown flag is ignored.

For tests and rehearsals: `--cache-dir DIR` (or `LSW_MC_CACHE_DIR`) and `LSW_MC_USAGE_DIR` keep a
run away from the real caches, `--width N` fixes the width of `--once`, and `LSW_MC_NOW=<epoch>`
freezes the clock.

## What it shows

- **Waiting on you**: the notes' `waiting_on_owner`, numbered, with an amber title while any
  wait; the notes file's age in the corner.
- **Release**: each item's stages as dots (`●` done, `◉` running, `○` queued, `✕` failed, `–` a
  fix not needed), the running stage, a bar that fills as work is done and is coloured by the
  time left, and a finish time. ETAs are each stage's planned minutes, calibrated against how long
  this release's finished stages really took, minus the time already run. A held item reads
  `paused`; once a hold with an end is over it reads its stage again, or `hold ended` while nothing
  has resumed it. An item whose next stage is yours (`your …`) reads it, and the header counts what
  waits on you. The finished items are one row below the rest, `● N finished`. Then the final
  merge and the tag row (CI on the remote main, the tag, the release workflow run, from GitHub).
- **Next release** (optional): planned items, grouped, with no finish time until work starts; the
  finished ones are one row below the rest (`● N finished`), as in the release panel, and a held
  item reads `paused`, as there.
  Then each **later release** (optional, the plan's `later`), in order, a panel of its own: an
  item not yet begun waits on the release before its own (`after 1.5.0`), unless its first stage
  is yours (`your …`): it reads that, and the header counts it as waiting on you.
- **Other work in progress**: everything the release does not wait for, including rows that wait
  on a plugin's live job.
- **The side row**: each plugin's card, then **Repository** (HEAD, unpushed commits, branches,
  worktrees, pull requests, the last CI run and release).
- **Agents at work**: running workflow agents, how long they have run, how long since they last
  did anything (green, amber, red), and their latest tool call; then the recent test suites in
  the session scratchpads, each named after the agent whose command writes its log.
- **Model usage**: the 5-hour and weekly plan limits with their resets, tokens used (last 5 h,
  today, 7 days) and by model; beside it the logo, turning slowly — a frozen dashboard stops.
- **The last row**: the Key, and at the very right whether this computer is on the internet and
  switching networks is safe now.

## The network indicator

The bottom-right flag has three states:

| Flag | Means |
|---|---|
| `● safe to switch networks` (green) | on the internet, and nothing in flight a drop would kill |
| ` NETWORK-CRITICAL ` (a red chip) and what is at risk | network work in flight: do not switch now |
| ` ⊘ NOT CONNECTED ` (an amber chip), why, and what is in flight | no working internet; it outranks the other two, and lists the network work in flight (it will fail) |

**Not connected** comes from a background check in the dashboard's own process, never on the
render path. Every second it looks up the route to the internet in the kernel (a UDP connect: no
packet is sent); no route (Wi-Fi off, cable out) reads `no network` at once, with no request.
With a route it asks Apple's captive-portal check (`http://captive.apple.com/hotspot-detect.html`,
the page macOS itself asks) every 5 s, every 2 s for three requests after the state or the route
changes, and at once when the route changes. A request has 2 s from its start, name lookup
included, and tries every address the name resolves to, a new one every 0.25 s while the earlier
ones go on (Happy Eyeballs), so a network whose IPv6 is routed but broken does not read as
silent. Only the page's `Success` body counts: a login page or a redirect reads `captive portal`,
and silence `no answer in 2s`. A request that fails with no answer, or with a server error
(`HTTP 503`), while the last answer was "online" is asked again 2 s later before the flag
changes, so a single lost packet or one bad answer from Apple's server does not flash it. A name
lookup still hung on the network just left never holds up the check on the new one, and a clock
set back does not stop it (its cadence runs on the monotonic clock). The live view re-reads the
answer on every tick of its loop (8 a second), so a change reaches the screen at once, not at
the next 5 s data refresh. An answer older than 15 s, or stamped in the future (the clock was set
back), is never shown as either: the flag starts with `?` in place of `●` (its first cells, so it
reads at any width) and adds `connection unknown` when there is room. The check starts no
process, so it is never listed as network work. `[network] check_internet = false` turns it off (the flag then
shows only whether switching is safe).

The flag lists every running process a network drop would kill: ssh/scp/sftp,
remote rsync, git push/fetch/pull/clone, gh, curl/wget, pip and npm installs, pip-audit, docker
push/pull/build and compose pull/build, brew, playwright installs. Processes younger than 8 s
are ignored, and so is every dashboard's own polling (its `gh` calls and the ssh hosts its plugins
declare). Anything else a dashboard starts — a first-run `pip install` — is listed. A check that
fails shows `network check failed`, never "safe": `ps` that cannot run, times out, or answers with
a table that does not even list the dashboard itself.

The rules are `NET_TOOLS` / `NET_SUBCOMMANDS` in `src/lsw_mission_control/net.py`, and each is
proven by a real process line in `NET_CASES`. A tool only one project uses goes in that project's
config instead:

```toml
[network]
tools = ["mytool"]
subcommands = { hg = ["pull", "push", "clone"] }

[[network.cases]]
command = "hg pull -u"
label = "hg pull"
```

`--check-net` prints `N/N network cases correct`, `N/N indicator cases correct` and `N/N
connection cases correct` (the internet check's verdicts, its staleness rule and scripted runs
of its state machine); the reload gate looks for the first phrase: it must never change.

## The status line and the plan limits

Plan limits (the 5-hour and weekly windows) reach the dashboard two ways, both writing
`~/.cache/lsw-mission-control/usage.json` (or `$LSW_MC_USAGE_DIR/usage.json`). It is account-wide,
shared by every project's dashboard.

1. **The status line.** Claude Code passes `rate_limits` to a status-line command after each
   reply. Point the hook at the engine's status line (or at a project's copy of
   `templates/statusline_shim.py`, which runs it):

   ```json
   "statusLine": {"type": "command", "command": "python3 /absolute/path/to/lsw-mission-control/src/lsw_mission_control/statusline.py", "refreshInterval": 60}
   ```

   It prints `Model · 5h N% · wk N%`, writes the file atomically, is stdlib-only and never fails.
   `lsw-mc statusline` runs the same code.

2. **The usage probe.** The status line runs only in the terminal CLI, so the data goes stale
   while you work elsewhere. Every 20 minutes (skipped while the data is younger than 15), a
   dashboard runs `claude -p --model haiku … ok` in an empty folder and reads its first
   `rate_limit_event`, then stops it. `--no-usage-probe` or `[usage] probe = false` turns it off,
   and `--once` never probes (it would exit before the answer, leaving a probe that spends a turn).

Tokens are counted from every session log on the machine (the last 8 days, incrementally, one
count per message id), cached in `tokens.json` beside `usage.json`.

## Writing a plugin

A plugin is a Python file (or module) named in the config:

```toml
[[plugins]]
name = "queue"
file = "mc_queue.py"        # relative to the config file
class = "QueuePlugin"

[plugins.options]
host = "build-box"
```

It polls in the background, draws a card in the side row (and/or a full-width panel placed by
`plugin:<name>` in `[layout] panels`), may own plan keys, may expose a live job that a plan row
with `after_server: true` waits on, and may add CLI flags. The API, with a worked example, is
[docs/plugins.md](docs/plugins.md). A plugin's error stays in its own card (and fails `--once`
and the reload gate); the rest of the dashboard keeps drawing. So does an engine panel's: a panel
that fails to build or to draw shows the error in its own place.

## Live reload

A live dashboard reloads itself when the engine's code, a plugin, the config or the launcher
changes — but only once the change compiles AND passes `--check-net --self-check` (every module
imports, every plugin loads, the dashboard is built and drawn with no error in any panel or plugin
at three window sizes and at the tightest width the logo fits in, before and after token counts
come in, the logo is drawn wherever Model usage leaves it room, never cuts Model usage short, and
is found and turns where it is drawn, the keys parse). A broken edit is not loaded: the title says
`edit to <file> not loaded: <why>`, the running code stays, and the next edit is tried again.
Edits settle for 2 s first. The plan and notes are re-read on every refresh and never restart
anything.

It restarts *through the launcher path*, keeping its scroll position, so replacing the launcher
file reloads a running window into whatever the file now holds.

## Development

```sh
~/.venvs/lsw-mission-control/bin/python -m pytest          # unit tests and golden renders
~/.venvs/lsw-mission-control/bin/ruff check src tests
~/.venvs/lsw-mission-control/bin/python -m pytest --update-golden   # then review: git diff tests/golden
```

Rules for changing the engine are in [CLAUDE.md](CLAUDE.md); how it is built is
[docs/architecture.md](docs/architecture.md).

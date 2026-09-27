# Changelog

## [0.1.0] - unreleased

The single-file dashboard, extracted into a reusable engine with a per-project config and
plugins. Its render is identical on identical inputs; the differences are all on failure paths:

- A frame error names the engine file it happened in (`render/release.py line N in release_panel`).
- A plugin's error stays in its own card (the rest of the frame draws); `--once` still exits 1
  with the traceback, and the rows that wait on a failed plugin's live job say "plugin error".
- The reload gate also requires exit 0 (a passing phrase with a failing exit code used to load),
  waits 2 s for an edit to settle, and renders one frame (`--self-check`) before it reloads.
- The network row lists network work a dashboard starts itself (a first-run `pip install`); only
  a dashboard's own polling (`gh`, a plugin's declared ssh host) is left out.
- Claude Code's project folder name is computed for any path (every non-alphanumeric character
  becomes `-`).
- `-h` prints help.

Fixed before the first release:

- A config error names the file once, and a missing config says where it looked and that
  `lsw-mc init` writes one. Keys that loaded and then failed every frame or did nothing are refused
  with the key named (a placeholder other than `{release}` or `{scratch}`/`{root}`, a plugin name
  that matches no `[[plugins]]` entry, out-of-range numbers such as a 0 poll period).
- `lsw-mc init --name` escapes the name for TOML.
- A plan whose values could only be typos is not loaded (the last good plan stays, the title says
  why): minutes that are NaN, infinite, negative or over 1,000,000, a stage that is not
  `[name, label, minutes]`, an `after` that is not a name, an `after_server` item with no stage.
  Each used to load and then fail every frame. A time no calendar holds reads `?`. A plan moved
  away and back with its old modification time is read again.
- A panel that fails to build or to draw shows `Mission control error` in its own place; the rest
  of the frame draws (it used to replace the whole frame). A plugin's card is contained the same
  way, drawing included, and so is a plugin result of the wrong type. `--once` exits 1 on any of
  them, and the reload gate now refuses an edit whose frame fails while DRAWING (it used to see only
  errors raised while the frame was built).
- Outside text (agent labels, item names, an agent's latest tool call) is drawn as it is, never read
  as rich markup: `[/x]` no longer fails the frame, and `[b]` or `:smile:` no longer vanish.
- A dashboard that starts while the plan is broken or missing no longer prunes the finished store
  (its stand-in release `?` read as a new release, and the first write kept only the final merge's
  records: finished items fell back to "queued").
- A notes file that does not load (a trailing comma, a list instead of an object) keeps the last
  good notes, turns the Waiting-on-you panel amber (`not loaded`, or `?` when there are none) and
  says why in the title, like the plan. It used to read as a green "nothing is waiting on you".
  `lsw-mc validate` names the parse error.
- The rows that wait on a plugin's live job say "plugin error" when that plugin failed to load or to
  start (they read "`<stage>`?", not reached yet).
- `--once` never starts the usage probe (it exited before the probe's answer, and the orphaned probe
  spent a model turn), and waits for GitHub's first round whatever its answer: a repository with no
  successful CI run to time, or `gh` offline, no longer holds every `--once` for the full 25 s.
- The network row says `network check failed`, never "safe to switch networks", when `ps` gives
  nothing (it could not run, or timed out) or a table without the dashboard's own process.
- After a release change, the tag row no longer shows the previous release's tag or release run
  while GitHub's answer for the new one is missing.
- A row that waits on a plugin's live job keeps the job's time left when every later stage already
  ran (it read "~0s · now", 100%, while the job still ran).
- A detached job's progress line that is valid JSON but not an object is skipped (it failed every
  frame).

Plugin API (additions only): `ctx.run(..., cwd=)`; `Plugin.__init__` sets `self.name` to the
`[[plugins]]` name; `validate_options` names the type it expected. A plugin loaded by module name
is watched for reloads like one loaded by file. The live view reloads only for files that can be
modules (an editor's `.#x.py` lock or a sync tool's `x (1).py` copy used to fail the gate). The
launcher's first-run notice goes to stderr; a status-line shim that cannot find the engine says so
in the status line.

Added: `lsw-mc init`, `validate`, `doctor`, `statusline`; `[logo]` (path, colours, caption; the default
caption is `λ∿ 2026`) and `[theme]` settings; `[[network.cases]]` for a project's own tools.

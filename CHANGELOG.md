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

A release item can be held (`"paused": true`), as an Other item can: it reads "paused" instead of
"failed"/"needs rerun" or "queued" with a finish time, the header counts it (`N paused`), and its
agents leave Agents at work; the release's finish still counts its time left. With
`"paused_until": "<ISO time>"` the row says "from <when>" and the hold's own length counts too.

A plan can list the releases after the next one: `later`, one `{release, about, items}` per release,
in the order they ship, each shaped like `next`. Each is a panel of its own right after the next
release's (`Later release 1.6.0`; `[release] later_title`), and its items not yet begun wait on the
release before their own (`after 1.5.0`; `[release] later_wait`, e.g. `"{release} installed"`),
never on this one, an owner's first stage included. A release two out used to go in as a group at
the bottom of the next release's panel, where its first stage, a null stage typed by hand, named
the wrong release's install. A plan with one `next` draws as before (but for `1 item planned`, which
read `1 items planned`); when a release ships, `next` becomes `items` and the first of `later`
becomes `next`. Each panel sizes its stage column by its own items, as the next release's always
did, so a later release never changes the panels before it. `later` is a list (absent or `null`:
none; `{}` or any other value is refused), `lsw-mc validate` names the later releases it read
(`later 1.6.0 1.7.0`, or `later no`), and a label that a later item shares with a nearer release's
item or with other work keeps naming that one in Agents at work.

The network indicator has a third state, **NOT CONNECTED** (an amber chip, ` ⊘ NOT CONNECTED `,
with why: `no network`, `captive portal`, `no answer in 2s`, ...): this computer has no working
internet. It outranks NETWORK-CRITICAL and "safe to switch", and lists the network work in flight
beside it (that work will fail). A background check in the dashboard's own process decides it,
never on the render path: a route lookup every second (no route: not connected at once, no
request), and Apple's captive-portal page every 5 s (2 s right after a change), where only its
`Success` body counts. A request has 2 s from its start, name lookup included, and tries every
address the name resolves to, a new one every 0.25 s (Happy Eyeballs), so a network whose IPv6 is
routed but broken does not read as not connected. Silence, or a server error (`HTTP 503`), while
online is asked again once before it shows; a lookup hung on the network just left never holds up
the new one; the cadence runs on the monotonic clock, so a clock set back cannot stop it. The live
view shows a new answer within a tick of its loop (1/8 s), not at its next 5 s data refresh. An
answer older than 15 s, or stamped in the future (the clock was set back), starts the flag with
`?` in place of `●` (its first cells, so it reads at any width) and adds `connection unknown` when
there is room, rather than guess. `--once` waits for the first answer (at most 2.5 s),
`--check-net` prints a third line (`N/N connection cases correct`), `--self-check` also draws the
row's other states through the live view, and `[network] check_internet = false` turns the check
off. With the internet answering, the flag draws exactly as before.

Fixed before the first release:

- The logo shows in a window under 100 columns wide, and never cuts Model usage short. It was drawn
  only from 100 columns, whatever Model usage held: a 99-column window had no logo at all, and at 100
  the weekly row lost its end (`resets Thu 14:13 ·…`). Model usage now keeps the width its rows
  measure (77 columns with plan limits, tokens and by-model data) and the logo takes the rest of the
  row: square when there is room, narrower when there is not (the drawing shrinks, in a panel as tall
  as Model usage), left out only when not even the smallest (5 rows, 12 columns of drawing) fits.
  At 99 columns it is 17 columns of drawing beside 10 rows; beside a Model usage with no plan data
  it now fits in 80. A row that grows takes its room from the logo: stale plan data adds
  `· as of HH:MM` (Model usage 91 columns), so a 99-column window has no logo until the plan data
  is fresh again.

- `--self-check`, the reload gate, draws the dashboard at three window sizes, 150×50, 99×60 and
  80×40 (it drew 150×50 only), and at the tightest width the logo fits in, each twice: with nothing
  counted, as a window starts, and with made-up token counts held in its own memory, as a window
  runs. Model usage is then taller, and the logo beside it narrower than square where room is
  tight, which the gate never drew before. It fails on a logo that would stand still: left out
  though Model usage leaves it room (as the fixed 100-column cut-off left it at 99 columns), drawn
  where the live view does not find it (nowhere, or at other cells than its own), or a turn that
  redraws nothing while it is on screen; and on a logo that cuts Model usage short. Each of these
  went by with nothing said: the gate failed only on an error raised while turning the logo. Room
  is told by drawing Model usage narrower and narrower, not by the rule that placed the logo, and
  every frame of the check is drawn at one instant, so a countdown that narrows during the check
  (`in 1h00`, then `in 59m`) cannot fail a sound edit.

- A paused row's bar fills with its work done in muted grey. It was filled in the empty part's own
  colour, so a held row at 71% drew an empty bar beside its "71%".

- The next release's and each later release's finished items are one row, `● N finished` with its
  ✓, below the rest, as the release panel draws its own. Each was a row of its own (`done`, a full
  bar, ✓) under its group, however many there were: a release with much of its work begun early
  listed every finished item, while its header already counted them (`N done`). Where the panel
  draws group headings, a blank row sets the finished row apart, which right under the last group's
  rows would read as that group's own. A group whose items have all finished has no heading; the
  header counts as before, and the columns are sized as before, by every item, finished or not, so
  an item that finishes changes no other row's columns.

- The tag row reads a run past its median as a lower bound, and main's CI from a commit pushed after
  the tag no longer starts it. A release run still going past the median of past runs read a minute
  left (`~59s`), and the header `release out … (in 1m)`, for as long as it ran: its end was always a
  minute from now. Past its median a run (the release run, or CI on main) now has at least 10 min
  left, or a quarter of its time so far, as a stage past its estimate has, and the row reads
  `≥17m · 14:30` and the header `(in ≥17m)`; within the median nothing changes. A push to main after
  the tag started CI that the row took for the release's (`since` that push): main's CI created
  after the release run is a later commit's, so the row starts no later than the release run.

- A journal line whose result holds U+2028, U+2029 or U+0085 is read whole. The journal was split
  with `str.splitlines()`, which splits at those characters too, so the line with an agent's
  result was lost, and the finished agent read as running, then failed ("needs rerun"). A journal
  line that is valid JSON but not an object is skipped (it failed every frame).

- A stage whose agent was retried reads the agent's latest attempt. The Workflow runtime starts an
  agent again under the same label after an API error, and a resumed run starts its unfinished
  agents again; the killed attempt leaves a `failed` event, or no end at all. The finished store
  kept that attempt as running, and a label's attempts were ordered by their transcripts' first
  timestamps, which need not follow the journal when a transcript is missing or has no time yet.
  (The ✕ "needs rerun" a returned retry showed was the journal split in the entry above.) Now
  every attempt is kept, and a run's attempts are in journal order: a later result supersedes
  an earlier failed or unfinished attempt, and an attempt begun after a result runs again (a
  failure after a result no longer undoes it: the entry below). An end event ends the attempt it
  names (its `agentId`), no longer whichever attempt of its key started last, and an attempt with
  no end when its key starts again is over. A run-qualified label reads its own run; a bare label
  reads the run whose attempts of it began last. Agents at work lists a label's latest attempt in
  each run by the same order.

- An agent that died never undoes work that returned, and a stage with an agent at work reads
  running. Two network outages killed the agents of every workflow running. Some were retries of
  agents that had already returned (a resumed run starts agents again), and the label read the
  retry: a fix that returned read "fix failed · needs rerun". Others belonged to a stage a new run
  was already working on, and the stage read failed while its other agents ran. A workflow agent
  fails only when it dies (an API error the runtime's retries did not get past, or a skip) or goes
  silent past `silent_stopped_min` (its workflow stopped), never as a verdict on its work. Now a
  failed attempt never undoes an attempt of its label that returned or still runs: the label reads
  the latest of those (a later result still replaces an earlier one; a run-qualified label in its
  own run, a bare one in any run), and fails only when it has none. Two stages still read the
  attempt that died: a held item's, whose stopped re-run is held, never done; and one whose result
  is older than an earlier stage's re-run while the attempt that died began after that re-run (the
  stage ran again and died, so it needs a re-run; its stale result alone would read "queued").
  Agents at work no longer hides an agent at work behind a later attempt of its label that died. A
  stage runs while any of its labels runs, timed from this round of it: its labels at work, and
  those that returned with no attempt of them dying since. A died attempt, and a result a later
  attempt died after, are an earlier round's, so the outage since them is no overrun. It reads
  failed, "needs rerun" with its whole time again, only when none of them runs and one failed, as
  before. The release's finish and the calibration follow: a stage that returned before its retry
  died is done, in the time it took. A held row that follows a plugin's live job (`after_server`)
  reads its later stages as held, as every other held row does; a stage of it that died read ✕. A
  hold that has ended counts an attempt that died since as work resumed, as it did when the label
  read that attempt: a run that started a returned stage again after the hold and died reads the
  item's real stage, never `hold ended` · `not resumed`.

- A detached job's stage (`{"progress", "total"}`) runs from the moment its progress file exists,
  even empty: it read "queued 0%" until its first unit finished, hours into a long job. It reads
  `<stage> 0/<total>` from the file's creation time (its birth time on macOS), with its planned
  minutes as its time left until a unit finishes. The units' pace is then timed from the job's
  start (it was timed from the first unit, so one unit read as taking no time) and weighed against
  the plan as if the plan had done a tenth of the units, so that the first unit of a long job does not
  multiply its time left. A stage with no agent before a job that has begun counts as done (a
  finished job's row read "queued"), and so does a job whose progress file is gone once a later
  stage has begun (a finished job's folder cleaned up read "queued" again, with its planned
  minutes). A later stage that ran before the job began runs again, and a unit whose `t` is not a
  time in 2000-2100 (not a number, a placeholder `0`, milliseconds) has no time.

- A final merge done by hand can be recorded in the plan (`final_merge_by_hand`, docs/plan-schema.md):
  a hotfix cut straight from main read "after all above", and the tag row put the release hours late.

- The usage probe tries again 3 min after a probe that came back with nothing (then 6, 12, up to
  the 20-min interval) instead of waiting the whole interval, which let the plan data reach twice
  its age; and a Claude CLI that goes silent is killed at the probe's timeout.

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
- After a release change, the new release's final merge starts empty. The shipped release's final
  merge used to carry over: the store kept its records, and the journals, still in the scan window,
  brought it back. The new release's panel then showed it done, the tag row read main's CI, from
  an unrelated commit, as the tag's ("CI running"), and the header promised the release within
  the hour. Now:
  - The finished store records when the dashboard first saw the release (`since`, epoch seconds),
    drops the final merge's records at the change, and saves the change at once.
  - A final-merge agent begun before `since` still shows under Agents at work, but no row counts it.
  - A final merge begun after the change is tracked as before, and so is everything else that
    carries over (an item that moved from `next` to `items` keeps its finished stages).
  - A release changed and changed straight back (a typo, a move undone when the tag fails) gets
    back what the change dropped, its final merge and its `since` included. It used to lose that
    final merge for good.
  - A store from before this change (no `since`), or one whose `since` is later than now (such as
    milliseconds typed by hand), counts everything, as before. To apply the fix to a release
    already under way, set `since` to the time of the change and drop the final-merge records by
    hand.
- A row that runs after other work (other's `after`, items' `after:<key>` and `after_all`, next's
  `after:<key>`) never finishes before that work, and has no finish time (`—`) while that work has
  none. Before, when the work it waits for lost its finish time (stalled, paused, not reached, a
  plugin error), the row read "queued" with its own time and was "next to finish". In detail:
  - Until that work is done, a row shows `after <name>` whenever nothing of its own is running.
  - This now holds for a row that has begun early too. Its begun stages run on beside the wait,
    and the stages it has not begun come after it: its time left is the longer of its running
    stage and the wait, plus the stages not yet begun. Before, a begun release or next row showed
    only its own time, and a begun row of other work added the whole wait to its own. The stages
    after a plugin's live job (`after_server`) follow the same rule.
  - Work that failed ("needs rerun", no time) counts as having no finish time for the rows after
    it in Other work and the next release. Release items still count a failed item's re-run, as
    the release's own finish time does.
  - When two rows tie for "next to finish", the one that is not waiting is named.
  - Every `after:<key>` flag counts (only the first did; the one that finishes last binds), and
    a row listed before the work it runs after still waits for it.
  - A wait shows even when the target has no time left but is not done (it read "queued").
  - One rule in `progress.py` (`wait_for`, `in_wait_order`) serves every panel.
- A hold with an end ends. An item with a `paused_until` read "paused" whether that time was still
  to come or had passed (the plan is read once, and the hold was the plan's alone), so at a usage
  reset every row held until it went on reading "paused" until the time was taken out of the plan
  by hand. Now the hold ends on the clock, whatever `paused` says beside it: the row reads its real
  stage, or `hold ended` with `not resumed` (the header counts `N not resumed`) while nothing of it
  has run since: no agent of it active since that time or still at work, no job of it running. It
  keeps the time left it had while held, and its agents are back in Agents at work.
- A release item whose next stage is the owner's (`your …`, with no agent) reads that stage, as the
  next release's rows do. It read `queued`, while the same stage in the next release's panel read as
  waiting on the owner. That holds while nothing of the item runs and it waits on nothing else; it
  keeps its time left, so the release's finish still counts it. The release's header counts the rows
  that wait on the owner (`N wait on you`), these and `owner_ok`'s `your go-ahead`, as the next
  release's header does. A stage that failed before the owner's reads the failure.
- A hold is counted once along a chain of rows held to one time. A row held until a time added the
  hold to its time left on top of its wait on the row it runs after, whose time left already held the
  same hold, so holding every row of a chain of `after:` waits added the hold again at each link:
  three rows of an hour each, held to a day from now, ended three days and three hours out. The hold
  is now a wait beside that one: the row's begun stages resume after the hold, and its stages not
  begun come after both, so that chain ends a day and three hours out however many of its rows are
  held, and the release's finish moves with it.
- A row of many stages no longer pushes the names and the finish times out of its table. Its stages
  column took the room it needed and the names got what was left, down to nothing and below, which
  ran every row past the panel's edge and cut its finish time short (`~25m ·`): at 80 columns a
  15-stage row of Other work did that to the Release and Other panels, and a 15-stage next-release
  item to its own panel. Now a stages column too wide to leave the names 6 cells is cut, its dots
  ending in `…` (the stage column beside them still names the running stage), but never below 16.
  Under 80 columns, where the names get what is left, they keep at least one cell, so a row no
  longer runs past the edge (at 70 columns a finish time read `~10m · 14`, now `~10m · 14:23`).

Plugin API (additions only): `ctx.run(..., cwd=)`; `Plugin.__init__` sets `self.name` to the
`[[plugins]]` name; `validate_options` names the type it expected; `render.widgets.fit_stages`,
`STAGES_MIN` and `ITEM_MIN` (`work_table` draws its stages column at `fit_stages()`, and
`table_widths` never gives the names less than one cell). A plugin loaded by module name
is watched for reloads like one loaded by file. The live view reloads only for files that can be
modules (an editor's `.#x.py` lock or a sync tool's `x (1).py` copy used to fail the gate). The
launcher's first-run notice goes to stderr; a status-line shim that cannot find the engine says so
in the status line.

Added: `lsw-mc init`, `validate`, `doctor`, `statusline`; `[logo]` (path, colours, caption; the default
caption is `λ∿ 2026`) and `[theme]` settings; `[[network.cases]]` for a project's own tools.

# For an adopting project's CLAUDE.md

Paste this section into the project's CLAUDE.md and fill in the `<…>` parts.

---

## The owner's status dashboard (`.claude/status.py`) must never be stale

The owner follows the project on `python3 .claude/status.py` (a launcher for lsw-mission-control:
the engine is `<path to the engine checkout>`, this project's config is
`.claude/mission-control.toml`<, its plugin(s): `.claude/mc_<name>.py`>) instead of asking. It
reads workflow journals, git and GitHub by itself; the two things it cannot know are maintained BY
HAND, in the same turn as the change:

- **`.claude/status_notes.json`**: `waiting_on_owner` lists every decision or question the owner
  can answer NOW — add it when you ask, remove it the moment it is answered (the first action of
  the turn the answer arrives in). A decision that waits on results the owner has not seen yet
  belongs in `in_progress_elsewhere` until those results are in front of the owner.
  `in_progress_elsewhere` lists only work that is NOT already a row in the plan. Bump `updated`.
- **`.claude/status_plan.json`** (re-read on every refresh; its `_about` documents the shape, the
  full schema is the engine's `docs/plan-schema.md`): `items` is exactly what the next release is
  waiting for, `other` is every other piece of work in progress. Work done FOR a release item goes
  in that item's `before` stages, never in `other`. Add an item when work joins the release, drop
  it when it leaves; correct a stage's minutes when measurements prove them wrong.
- **Name workflow agents `build:<key>` / `review:<key>` / `fix:<key>`**, matching an item's key,
  or the dashboard cannot track them. The release's final merge uses the key `<final-merge key>`;
  move the plan's `release` on once a release ships, before the next final merge starts (only a
  final merge begun after the dashboard sees the change counts for the new release). A stopped
  workflow's `other` entry gets `"paused": true` until it is resumed; a release item held until a
  time gets `"paused_until"`, and its hold ends by itself at that time.
- **The network indicator (bottom right) must always be right.** Before starting any
  network-dependent operation of a kind it does not recognise, add that kind FIRST: to the engine's
  `src/lsw_mission_control/net.py` (`NET_TOOLS` / `NET_SUBCOMMANDS`, plus a real ps line in
  `NET_CASES`) for a tool any project might use, or to this project's `[network]` /
  `[[network.cases]]` in `.claude/mission-control.toml` for one only it uses; then run
  `python3 .claude/status.py --check-net` and check the indicator turns red while it runs. Never
  hide network work inside a wrapper it cannot see.

After editing either file, run `python3 .claude/status.py --once` and check the render. Edit the
plan, never the dashboard's code, for any of this: only a change to the dashboard's own behaviour
touches the engine (its own CLAUDE.md governs how) or this project's config and plugin. A running
dashboard reloads itself on any engine, plugin, config or launcher save once the edit passes
`--check-net --self-check`; for anything beyond a one-line change, build on a copy and swap it in
atomically.

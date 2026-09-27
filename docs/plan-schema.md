# The plan and the notes

Two JSON files a project keeps **by hand** (Claude keeps them current as work starts, moves and
ends). Both are re-read on every refresh: an edit shows within 5 s, and nothing restarts.

## `status_plan.json`

A plan that fails to parse is ignored — the last good plan stays on screen and the title says
why (`status_plan.json not loaded (KeyError: 'release')`). So is a plan whose values could only be
typos: minutes outside 0–1,000,000 (NaN and infinity included), a stage that is not
`[name, label, minutes]`, a list where a name belongs. Unknown keys are ignored (a `note` on any
item is common), so the plan can carry its own explanations.

```json
{
  "_about": "free text: what this file is (ignored)",
  "release": "1.4.0",
  "items": [
    {"name": "Search filters", "key": null, "build": 0, "review": 0, "fix": 0},
    {"name": "Export to CSV", "key": "export", "build": 60, "review": 20, "fix": 30,
     "before": [["design", "design:export", 30], ["your sign-off", null, 0]]},
    {"name": "Dark mode polish", "key": "dark", "build": 45, "review": 15, "fix": 20, "flags": ["after:startup"]},
    {"name": "Security review", "key": "security", "build": 60, "review": 30, "fix": 30, "flags": ["after_all", "owner_ok"]}
  ],
  "other": [
    {"name": "Docs site refresh", "stages": [["draft", "docs:draft", 60], ["review", "docs:review", 20]]},
    {"name": "Dependency upgrades", "stages": [["upgrade", "deps:upgrade", 60]], "paused": true},
    {"name": "Benchmarks", "after": "Docs site refresh", "stages": [["bench", "bench:run", 40]]},
    {"name": "Nightly import", "after_server": true, "stages": [["import", null, 0], ["verify", "verify:import", 30]]}
  ],
  "next": {"release": "1.5.0", "about": "sync first", "items": [
    {"key": "sync", "name": "Offline sync", "group": "first", "before": [["your pick", null, 0]], "build": 240, "review": 45},
    {"key": "icons", "name": "New icon set", "group": "then", "build": 30, "flags": ["after:sync"]}
  ]}
}
```

### Top level

| Key | Required | What it is |
|---|---|---|
| `_about` | no | free text, ignored |
| `release` | yes | the release being built (the tag is `<tag_prefix><release>`) |
| `items` | yes | what the release waits for |
| `other` | no | work in progress the release does not wait for |
| `next` | no | the release after this one (its panel shows only when it has items) |
| *plugin keys* | per plugin | a plugin may own keys; it validates them, and a plan it rejects is not loaded |

### `items`: `{name, key, build, review, fix, flags?, before?}`

- Agents are labelled `build:<key>`, `review:<key>` and `fix:<key>`; `build`/`review`/`fix` are
  planned minutes (scaled by the release's calibration once 3 of a kind have finished).
- `key: null` counts as done.
- `before`: stages that precede the build (measure, design), in the stage shape below.
- `flags`: `after:<key>` (one flag per item it runs after), `after_all` (after all of the rest),
  `owner_ok` (shows "your go-ahead" until it starts). See "Waits" below.
- A fix whose review found nothing of severity blocker/major/minor is skipped (`–`); until the
  review is done its time is weighted by the fix share.

### `other`: `{name, stages, paused?, after?, after_server?}`

- `paused: true`: held by the owner — no finish time, and its idle agent is not a failure.
- `after`: another other item's **name**; this one waits for it (see "Waits" below). A name the
  plan no longer holds counts as done (a finished row leaves the plan). It has no effect on an
  `after_server` item, which follows its live job.
- `after_server: true` (alias `after_live`): the FIRST stage mirrors a plugin's live job (its share
  done, its time left, "stalled", or unknown while the plugin has not answered), so such an item
  needs at least that stage.

### `next`: `{release, about, items: [{key, name, group, before, build, review, fix, flags}]}`

Planned, not scheduled: no finish time until work on an item starts. Items are grouped by
`group` (in order). A stage named `your …` with a null spec is the owner's ("wait on you").
`after:<key>` shows `after <name>` while that item is unfinished (see "Waits" below).
`build`/`review`/`fix` become stages only when their minutes are above 0.

### Waits

The same rule for `after` (other), `after:<key>` and `after_all` (items) and `after:<key>` (next),
until everything a row runs after is done:

- While nothing of its own is running, its stage reads `after <name>` (the one of them that
  finishes last).
- It never finishes before them. Its stages not yet begun come after their time left; a stage it
  began early runs on beside it. Its time left is the longer of the two, plus its stages not
  yet begun.
- If one of them has no finish time (stalled, paused, not reached, a plugin error, or, in Other
  work and the next release, failed), the row has none either (`—`) and is never "next to finish".
  A release item after a failed one counts that item's re-run, as the release's finish time does.
- A tie for "next to finish" goes to the row that is not waiting.
- A row listed before the one it waits for still waits for it. A cycle (a typo) is broken where it
  closes.

### A stage: `[name, spec, minutes]`

`spec` is one of:

- an agent label (`"docs:draft"`), run-qualified if needed (`"wf_<run>/docs:draft"`);
- a list of labels (all must finish);
- `null`: no agent — it counts as done once a later stage has started;
- `{"progress": "/path/progress.jsonl", "total": 120, "eta_json"?: "/path/eta.json", "eta_key"?: "projected_finish_utc"}`
  for a detached job that appends one JSON line per finished unit (`{"t": <epoch>, "status"?: "ok"|"done"}`);
  `eta_json` (used while under 30 min old) is the job's own projected finish, as an ISO time.

A stage that re-ran after a later one started makes the later ones count again. A running stage
past its estimate has at least 10 min left, or a quarter of its time so far.

### The final merge and the tag

The final merge is tracked by `build:<key>`, `review:<key>` and `fix:<key>` for the configured
`[release.final_merge] key` (default `final-merge`). Every release has its own under the same
labels: when `release` changes, the shipped release's final merge is dropped from the finished
store, which records when the dashboard first saw the new release (`since`), and only a final merge
begun after that counts for it (an earlier one still shows under Agents at work while it runs).
Change `release` once the old one has shipped, before the new final merge starts. A change undone
straight away (back to the release it left) gives that release back its final merge. The tag row reads
GitHub: CI on the remote main's HEAD once this release's merge is done, the tag, then the release
workflow's run on it.

## `status_notes.json`

```json
{
  "updated": "2026-09-21 14:08",
  "waiting_on_owner": [
    "Pricing: pick one of the three plans on the sheet → https://example.org/sheet/42",
    "Approve the security review scope before it starts"
  ],
  "in_progress_elsewhere": ["Log rotation on the build box (a one-off, no plan row)"]
}
```

- `waiting_on_owner`: ONLY what the owner can act on now. Text before the first `": "` is the
  subject (bold, when at most 80 characters and not a URL); URLs are muted. The panel's title turns
  amber with the count.
- `in_progress_elsewhere`: work in motion that no plan row covers; listed under Other work as
  "also in motion".
- The file's modification time drives the corner (`updated 14:08 · 5m ago`, amber after an hour).
- A notes file that does not parse keeps the last good notes on screen; the panel turns amber
  (`not loaded`) and the title says why, as for the plan.

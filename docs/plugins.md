# Writing a plugin

A plugin is how a project adds what only it has — a live server's job queue, a device farm, a
deploy pipeline — without touching the engine. It can:

- **poll in the background** (its own thread, its own state);
- **draw a card** in the side row, beside Repository, and/or **a full-width panel** (placed by
  `plugin:<name>` in `[layout] panels`);
- **own plan keys** (validate them; a plan it rejects is not loaded, and the title says why);
- **expose a live job** that plan rows with `after_server: true` wait on;
- **add CLI flags** (`--no-queue`), listed by `-h`.

Nothing else. A need outside this list is a change to the engine's API (additive only), never a
reach into engine internals. Import only from `lsw_mission_control.plugin` (see its `__all__`),
`lsw_mission_control.render.widgets`, `.render.side`, `.util` and `.theme`.

## Registering it

```toml
[[plugins]]
name = "queue"              # its name everywhere: [plan] after_server, plugin:<name>, error cards
file = "mc_queue.py"        # relative to the config file (or: module = "mypkg.queue")
class = "QueuePlugin"

[plugins.options]           # the plugin's own options: no secrets, ever
host = "build-box"
poll_s = 60
```

## The API

```python
class Plugin:
    name: str = ""
    cli_flags: tuple[CliFlag, ...] = ()        # CliFlag("--no-queue", "skip the queue panel")
    def __init__(self, ctx: PluginContext): ...  # validate ctx.options here (validate_options); self.name is set
    net_labels -> tuple[str, ...]              # property: labels its own children produce ("ssh → build-box")
    def parse_plan(self, raw: dict) -> object  # its plan keys -> frame.plan.plugin_data[name]; raise to reject
    def start(self) -> None                    # start polling (never called by --check-net / --self-check)
    def ready(self) -> bool                    # --once waits (up to 25 s) until this is true
    def side_card(self, width, frame) -> SideCard | None
    def panel(self, width, frame) -> RenderableType | None
    def live_job(self, frame) -> LiveJob | None   # None: unknown (not answered yet, or switched off)

PluginContext: name, options, cfg, flags (flags.has("--no-queue")), state (a thread-safe Store),
               root, cache_dir, run(*cmd, timeout, input_, cwd), spawn(fn, name), write_json(path, data), now()
SideCard(title, grid, subtitle="", subtitle_style=None)     # grid: render.side.card_grid() + add_row(label, value)
LiveJob(left, frac, eta, eta_life, live, stalled_label="stalled")
validate_options(plugin, options, required={key: type}, optional={key: (type, default)}) -> dict
```

- `frame` is the engine's `Frame`: `now`, `width`, `cfg`, `plan` (with `plugin_data`), `labels`,
  `names`, `agents`, `notes`, `store` (a snapshot), `flags`.
- **Run commands through `ctx.run`.** It is a direct child of the dashboard, so the network
  indicator can tell the dashboard's own polling from anyone else's — but only for the labels in
  `net_labels` (and `gh`). Declare the ssh host you poll; anything else you start that uses the
  network (an install) is listed, as it should be.
- **Keep state in `ctx.state`** (`get`, `set`, `update`, `snapshot`; for a change of several keys at
  once, `with ctx.state.lock:` and `ctx.state.data`).
- **Caches** go in `ctx.cache_dir` (the project's `[cache] dir`), written with `ctx.write_json`
  (atomic, so several dashboards can share them).
- **Errors are contained and loud.** An exception in `side_card`, `panel` or `live_job`, a result
  of the wrong type (a `side_card` that is not a `SideCard` with a rich `Table` grid, a `panel`
  that is not renderable, a `LiveJob` whose numbers are not finite), or an error while its card or
  panel is DRAWN (a markup error in a plain-string cell) shows as `Type: message` in that plugin's
  card (or panel), the rest of the frame draws, `--once` exits 1 with the traceback, and the reload
  gate refuses an edit that causes one. A `live_job` failure also turns the rows that wait on it
  red ("plugin error"), never "not reached yet". A plugin that fails to load (import error, bad
  options) shows an error card, and fails `--check-net`.
- **Put outside text in `Text(...)`.** A plain `str` cell is read as rich markup: a host's error
  message with `[/…]` in it fails the card, and `[b]` silently vanishes. `Text(s)` draws `s` as is.
- **Render methods must not block.** `side_card`, `panel` and `live_job` run on the main loop at
  every refresh (every 5 s, and on every key): read `ctx.state`, never the network or a slow file.
  Polling belongs in the thread `start()` spawns.
- A live job's `left == 0` marks the waiting row's first stage done; otherwise `frac` counts as
  work done and `eta` (or, when that is None, `eta_life`) as time left; `live = False` shows the
  stage as unknown (`stage?`), and `eta is None` while live shows `stalled_label` in red.

## A worked example

`mc_queue.py`, a card for a job queue on a build box, read over ssh once a minute:

```python
"""A job queue on a build box: a side card and the live job a plan row can wait on."""

from __future__ import annotations

import time

from rich.text import Text

from lsw_mission_control.plugin import CliFlag, LiveJob, Plugin, SideCard, validate_options
from lsw_mission_control.render.side import card_grid
from lsw_mission_control.render.widgets import METER_W, bar, eta_colour
from lsw_mission_control.theme import C
from lsw_mission_control.util import ago, human

SCRIPT = "cat /var/lib/queue/counts\n"   # prints "done|left|rate_per_hour"


class QueuePlugin(Plugin):
    name = "queue"
    cli_flags = (CliFlag("--no-queue", "skip the queue panel (no SSH)"),)

    def __init__(self, ctx):
        super().__init__(ctx)
        self.o = validate_options(ctx.name, ctx.options, {"host": str}, {"poll_s": (int, 60)})

    @property
    def net_labels(self):
        return (f"ssh → {self.o['host']}",)

    def start(self):
        if not self.ctx.flags.has("--no-queue"):
            self.ctx.spawn(self.loop, "poll")

    def ready(self):
        return self.ctx.flags.has("--no-queue") or self.ctx.state.get("at") is not None

    def loop(self):
        while True:
            out = self.ctx.run("ssh", "-o", "BatchMode=yes", self.o["host"], "bash", "-s", timeout=30, input_=SCRIPT)
            try:
                done, left, rate = (float(x) for x in out.strip().split("|"))
                self.ctx.state.update(done=done, left=left, rate=rate, at=time.time(), err=None)
            except ValueError:
                self.ctx.state.update(err="unreachable")
            time.sleep(self.o["poll_s"])

    def live_job(self, frame):
        s = self.ctx.state.snapshot()
        if s.get("at") is None:
            return None
        total = s["done"] + s["left"]
        eta = s["left"] / (s["rate"] / 3600) if s["rate"] else None
        return LiveJob(left=int(s["left"]), frac=s["done"] / total if total else 1.0, eta=eta, eta_life=None,
                       live=not s.get("err") and frame.now - s["at"] < 180, stalled_label="queue stalled")

    def side_card(self, width, frame):
        g = card_grid()
        job = self.live_job(frame)
        if job is None:
            g.add_row("queue", Text(f"connecting to {self.o['host']}…", style=C.FAINT))
            return SideCard(f"Queue · {self.o['host']}", g)
        g.add_row("progress", bar(job.frac, METER_W, eta_colour(job.eta)) + Text(f" {job.frac * 100:.0f}%"))
        g.add_row("finishes", Text(f"in {human(job.eta)}" if job.eta else "—", style=C.MUTED))
        return SideCard(f"Queue · {self.o['host']}", g, f"updated {ago(self.ctx.state.get('at'))}")
```

And a plan row that waits on it: `{"name": "Rebuild the index", "after_server": true, "stages": [["queue", null, 0], ["check", "check:index", 20]]}`.

## Testing a plugin

Keep a plugin's tests beside it. Freeze the clock (`lsw_mission_control.testing.freeze(epoch)`),
point `LSW_MC_CACHE_DIR` / `LSW_MC_USAGE_DIR` at a temp dir, build an `Engine(load_config(...),
Flags([...]), readonly=True)`, replace `plugin.ctx.run` with a fake that returns canned output, and
render with `testing.render_engine(engine, width)` or the card alone with `testing.render_text`.

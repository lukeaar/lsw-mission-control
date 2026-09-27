"""A test plugin: a side card, a live job the plan can wait on, a CLI flag, a plan key."""

from __future__ import annotations

from rich.text import Text

from lsw_mission_control.plugin import CliFlag, LiveJob, Plugin, SideCard, validate_options
from lsw_mission_control.render.side import card_grid
from lsw_mission_control.render.widgets import METER_W, bar
from lsw_mission_control.theme import C

# live job states the tests switch between through the plugin's state["job"]
JOBS = {
    "running": LiveJob(left=400, frac=0.6, eta=2 * 3600.0, eta_life=3 * 3600.0, live=True, stalled_label="import stalled"),
    "done": LiveJob(left=0, frac=1.0, eta=0.0, eta_life=0.0, live=True),
    "not-live": LiveJob(left=400, frac=0.6, eta=2 * 3600.0, eta_life=3 * 3600.0, live=False),
    "stalled": LiveJob(left=400, frac=0.6, eta=None, eta_life=3 * 3600.0, live=True, stalled_label="import stalled"),
    "unknown": None,
}


class StubPlugin(Plugin):
    name = "stub"
    cli_flags = (CliFlag("--no-stub", "skip the stub panel"),)

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.o = validate_options(ctx.name, ctx.options, {"host": str}, {"fail": (str, "")})
        ctx.state.update(job="running", fail="")

    @property
    def net_labels(self):
        return (f"ssh → {self.o['host']}",)

    def parse_plan(self, raw):
        return raw.get("stub_key", "none")

    def _fail(self, where: str) -> None:
        if self.ctx.state.get("fail") == where or self.o["fail"] == where:
            raise RuntimeError(f"stub {where} broke")

    def _mode(self) -> str:
        return self.ctx.state.get("fail") or self.o["fail"]

    def live_job(self, frame):
        self._fail("live_job")
        if self._mode() == "nan-job":
            return LiveJob(left=4, frac=float("nan"), eta=None, eta_life=None, live=True)
        if self.ctx.flags.has("--no-stub"):
            return None
        return JOBS[self.ctx.state.get("job")]

    def side_card(self, width, frame):
        self._fail("side_card")
        if self._mode() == "dict-card":
            return {"title": "not a card"}
        if self._mode() == "no-grid":
            return SideCard("Stub", None)
        g = card_grid()
        if self._mode() == "markup":
            g.add_row("note", "see [/docs] first")  # a plain str cell is markup: this one only fails when drawn
        job = JOBS[self.ctx.state.get("job")]
        g.add_row("host", Text(self.o["host"], style=C.TEXT))
        if job is not None:
            g.add_row("import", bar(job.frac, METER_W, C.ACCENT) + Text(f" {job.frac * 100:.0f}%", style=C.MUTED))
        g.add_row("plan key", Text(str(frame.plan.plugin_data.get(self.name)), style=C.MUTED))
        return SideCard(f"Stub · {self.o['host']}", g, "updated 1m ago")

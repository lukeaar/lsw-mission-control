"""Synthetic projects for the golden renders: a config, a plan, notes, workflow journals and
transcripts, scratchpad logs, caches, a store and a process table, all at the frozen time.
Nothing here is real project data."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import textwrap
from pathlib import Path

from lsw_mission_control.config import load_config
from lsw_mission_control.connectivity import Online
from lsw_mission_control.engine import Engine
from lsw_mission_control.plugin import Flags

from conftest import NOW

STUB_PLUGIN = Path(__file__).resolve().parent / "plugins" / "stub_plugin.py"
MIN = 60.0
HOUR = 3600.0


def ts(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Project:
    def __init__(self, tmp: Path, name: str = "demo") -> None:
        self.tmp = tmp
        self.root = tmp / name
        self.dot = self.root / ".claude"
        self.dot.mkdir(parents=True)
        self.projects = tmp / "projects"
        self.scratch = tmp / "scratch"
        self.cache = tmp / "cache"
        self.usage_dir = Path(os.environ["LSW_MC_USAGE_DIR"])
        self.runs: dict[str, list] = {}
        self.store: dict = {}
        self.plugin_state: dict = {}
        self.ps = ""
        self.write_config("")

    # ── files ───────────────────────────────────────────────────────────────────────────────
    def write_config(self, extra: str, name: str = "demo") -> None:
        text = textwrap.dedent(f"""\
            schema = 1

            [project]
            name = "{name}"
            projects_dir = "{self.projects}"
            scratch_dir = "{self.scratch}"

            [cache]
            dir = "{self.cache}"
            """) + textwrap.dedent(extra)
        (self.dot / "mission-control.toml").write_text(text)

    def plan(self, plan: dict) -> None:
        (self.dot / "status_plan.json").write_text(json.dumps(plan))

    def notes(self, waiting=(), elsewhere=(), age_s: float = 5 * MIN) -> None:
        p = self.dot / "status_notes.json"
        p.write_text(json.dumps({"updated": "", "waiting_on_owner": list(waiting), "in_progress_elsewhere": list(elsewhere)}))
        os.utime(p, (NOW - age_s, NOW - age_s))

    def usage(self, record: dict | None, age_s: float = 3 * MIN) -> None:
        self.usage_dir.mkdir(parents=True, exist_ok=True)
        p = self.usage_dir / "usage.json"
        if record is None:
            p.unlink(missing_ok=True)
            return
        p.write_text(json.dumps(record))
        os.utime(p, (NOW - age_s, NOW - age_s))

    def log(self, name: str, text: str, age_s: float, session: str = "sess-1") -> None:
        p = self.scratch / session / "scratchpad" / f"{name}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        os.utime(p, (NOW - age_s, NOW - age_s))

    def agent(self, label: str, *, run: str = "wf_run-a", status: str = "running", start_ago: float = 30 * MIN,
              quiet_s: float = 30.0, action: str = "Bash · Run the unit tests", phase: str = "", findings=None,
              writes_log: str | None = None, agent_id: str | None = None, session: str = "sess-1") -> None:
        """One workflow agent: its journal lines and a transcript with first/last timestamps."""
        rows = self.runs.setdefault((session, run), [])
        aid = agent_id or "a" + hashlib.md5(f"{session}/{run}/{label}/{len(rows)}".encode()).hexdigest()[:16]
        rows.append({"label": label, "id": aid, "status": status, "phase": phase or label.split(":")[0].title(),
                     "findings": findings})
        d = self.projects / session / "subagents" / "workflows" / run
        d.mkdir(parents=True, exist_ok=True)
        t0, t1 = NOW - start_ago, NOW - quiet_s
        name, _, what = action.partition(" · ")
        lines = [{"timestamp": ts(t0), "type": "user", "message": {"role": "user", "content": "go"}}]
        if writes_log:
            lines.append({"timestamp": ts(t0 + 1), "type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": f"pytest -q > {writes_log}.log 2>&1"}}]}})
        lines.append({"timestamp": ts(t1), "type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": name, "input": {"description": what}}]}})
        (d / f"agent-{aid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))

    def run_end(self, run: str, status: str = "killed", ago_s: float = 10 * MIN, session: str = "sess-1",
                record: object = None) -> None:
        """The Workflow runtime's record of a run's end (`<session>/workflows/<run>.json`): `status`
        and `timestamp` `ago_s` before now, or `record` as it is (text: written raw)."""
        p = self.projects / session / "workflows" / f"{run}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {"runId": run, "status": status, "timestamp": ts(NOW - ago_s)} if record is None else record
        p.write_text(rec if isinstance(rec, str) else json.dumps(rec))

    def finish_runs(self) -> None:
        for (session, run), rows in self.runs.items():
            d = self.projects / session / "subagents" / "workflows" / run
            out = []
            for i, a in enumerate(rows):
                out.append({"type": "started", "key": f"k{i}", "agentId": a["id"], "label": a["label"], "phase": a["phase"]})
                if a["status"] == "done":
                    out.append({"type": "result", "key": f"k{i}", "result": {"findings": a["findings"] or []}})
                elif a["status"] == "failed":
                    out.append({"type": "failed", "key": f"k{i}"})
            (d / "journal.jsonl").write_text("".join(json.dumps(x) + "\n" for x in out))
            os.utime(d, (NOW - 10 * MIN, NOW - 10 * MIN))

    # ── the engine ──────────────────────────────────────────────────────────────────────────
    def engine(self, flags=(), readonly: bool = False) -> Engine:
        self.finish_runs()
        cfg = load_config(self.dot / "mission-control.toml")
        e = Engine(cfg, Flags(flags), readonly=readonly)
        e.ps_text = self.ps
        # the internet answered 2 s ago, unless the test says otherwise (None: no answer yet)
        e.store.update(**{"online": Online(True, "", NOW - 2), **self.store})
        for p in e.plugins:
            p.ctx.state.update(**self.plugin_state)
        return e


# ── the scenarios ──────────────────────────────────────────────────────────────────────────
def gh_store(**over) -> dict:
    g = {"main_sha": "c0ffee" + "0" * 34, "tag": False, "at": NOW - 60,
         "last_ci": {"workflowName": "CI", "status": "completed", "conclusion": "success", "headSha": "c0ffee" + "0" * 34,
                     "headBranch": "main", "event": "push", "createdAt": ts(NOW - 2 * HOUR), "updatedAt": ts(NOW - 100 * MIN)},
         "prs": {"open": 3, "drafts": 1, "bots": 2},
         "last_release": {"tagName": "v1.3.0", "publishedAt": ts(NOW - 3 * 86400), "isLatest": True}}
    g.update(over)
    return g


GIT = {"head": "3f2a9c1 Wire the export button to the new endpoint", "unpushed": "2", "branches": 1, "worktrees": 2,
       "dirty": 3}

TOKENS = {"5h": [1200, 350_000, 2_400_000, 41_000_000], "today": [2100, 610_000, 4_100_000, 88_000_000],
          "7d": [52_000, 4_900_000, 120_000_000, 2_300_000_000]}
BY_MODEL = {"Opus": {"5h": 300_000, "today": 520_000, "7d": 4_100_000}, "Sonnet": {"5h": 50_000, "today": 90_000, "7d": 700_000},
            "Haiku": {"5h": 0, "today": 0, "7d": 100_000}, "other": {"5h": 0, "today": 0, "7d": 0}}

# A plan-limit record the panel cannot read a window from: the status line saves Claude Code's
# `rate_limits` as it comes whenever it is a non-empty dict, so a window that is null (or a key set the
# panel does not know) gives Model usage no plan-limit row at all. Its grid (expand=True) is then empty,
# and rich measures an empty flexible column at the whole width it is offered.
UNREADABLE = {"at": NOW - 3 * MIN, "rate_limits": {"five_hour": None, "seven_day": None}, "source": "probe"}

PS_SAFE = "    1     0  3-00:00:00 /sbin/launchd\n  400     1       10:00 /usr/bin/python3 /x/demo/.claude/status.py\n" \
          "  401   400       00:20 gh api repos/example/demo/commits/main --jq .sha\n"


def release_plan(**extra) -> dict:
    plan = {
        "_about": "synthetic",
        "release": "1.4.0",
        "items": [
            {"name": "Search filters", "key": None, "build": 0, "review": 0, "fix": 0},
            {"name": "Export to CSV", "key": "export", "build": 60, "review": 20, "fix": 30,
             "before": [["design", "design:export", 30], ["your sign-off", None, 0]]},
            {"name": "Faster startup", "key": "startup", "build": 90, "review": 25, "fix": 30},
            {"name": "Dark mode polish", "key": "dark", "build": 45, "review": 15, "fix": 20, "flags": ["after:startup"]},
            {"name": "Accessibility audit", "key": "a11y", "build": 120, "review": 30, "fix": 45},
            {"name": "Security review", "key": "security", "build": 60, "review": 30, "fix": 30,
             "flags": ["after_all", "owner_ok"]},
        ],
        "other": [
            {"name": "Nightly data import · 1,200 files", "after_server": True,
             "stages": [["import", None, 0], ["verify", "verify:import", 30]]},
            {"name": "Docs site refresh", "stages": [["draft", "docs:draft", 60], ["review", "docs:review", 20]]},
            {"name": "Dependency upgrades", "stages": [["plan", "deps:plan", 20], ["upgrade", "deps:upgrade", 60]],
             "paused": True},
            {"name": "Benchmarks after upgrades", "after": "Docs site refresh",
             "stages": [["bench", "bench:run", 40]]},
        ],
    }
    plan.update(extra)
    return plan


def midway(p: Project, job: str = "running") -> None:
    """A release half done, with every kind of row: the common case."""
    p.write_config("""
        [github]
        repo = "example/demo"

        [release]
        tag_row = "Tag {release} + packages"

        [plan]
        after_server = "stub"

        [[plugins]]
        name = "stub"
        file = "%s"
        class = "StubPlugin"

        [plugins.options]
        host = "stubhost"
        """ % STUB_PLUGIN)
    p.plan(release_plan())
    p.notes(["Pricing: pick one of the three plans on the sheet → https://example.org/sheet/42 (say 'priced')",
             "Approve the security review scope before it starts"],
            ["Log rotation on the build box (a one-off, no plan row)"])
    p.usage({"at": NOW - 3 * MIN, "rate_limits": {"five_hour": {"used_percentage": 42.0, "resets_at": NOW + 2 * HOUR},
                                                  "seven_day": {"used_percentage": 81.0, "resets_at": NOW + 3 * 86400}},
             "source": "probe", "status": "allowed", "overage": False})
    # the export item: design done, sign-off (null) passed once build started; build done, review done
    # with a minor finding, fix running
    p.agent("design:export", status="done", start_ago=6 * HOUR, quiet_s=5.5 * HOUR)
    p.agent("build:export", status="done", start_ago=5 * HOUR, quiet_s=4 * HOUR)
    p.agent("review:export", status="done", start_ago=3.9 * HOUR, quiet_s=3.5 * HOUR, findings=[{"severity": "minor"}])
    p.agent("fix:export", start_ago=20 * MIN, quiet_s=40, action="Edit · src/export/csv.py", writes_log="export-tests")
    # startup: build overrunning its plan
    p.agent("build:startup", start_ago=2.5 * HOUR, quiet_s=4 * MIN, action="Bash · Profile the cold start")
    # a11y: build failed
    p.agent("build:a11y", status="failed", start_ago=3 * HOUR, quiet_s=2 * HOUR)
    # other work
    p.agent("docs:draft", start_ago=40 * MIN, quiet_s=2 * MIN, run="wf_run-b", action="Write · docs/guide/export.md")
    p.agent("deps:plan", start_ago=5 * HOUR, quiet_s=4 * HOUR, run="wf_run-c")  # paused: its idle agent is not a failure
    p.log("export-tests", "....\n412 passed, 3 skipped in 81.2s\n", 3 * MIN)
    p.log("startup-bench", "collecting\n[ 42%] bench_cold_start\n", 1 * MIN)
    p.log("a11y-suite", "E   AssertionError\n2 failed, 88 passed in 12.1s\n", 50 * MIN)
    p.log("stale-suite", "9 passed\n", 3 * HOUR)  # too old to show
    p.store = {"git": dict(GIT), "release_gh": gh_store(), "gh_timing": (18.0, 9.0), "gh_polled": True, "tokens": TOKENS,
               "tokens_by_model": BY_MODEL}
    p.plugin_state = {"job": job}
    p.ps = PS_SAFE


def empty(p: Project) -> None:
    """A project just set up with `lsw-mc init`: nothing has happened yet."""
    import lsw_mission_control

    TEMPLATES = Path(lsw_mission_control.__file__).resolve().parent / "templates"
    (p.dot / "status_plan.json").write_text((TEMPLATES / "status_plan.json").read_text())
    (p.dot / "status_notes.json").write_text((TEMPLATES / "status_notes.json").read_text())
    os.utime(p.dot / "status_notes.json", (NOW - 30, NOW - 30))


def next_release(p: Project) -> None:
    plan = release_plan()
    plan["items"] = plan["items"][:1]
    plan["other"] = []
    plan["next"] = {"release": "1.5.0", "about": "the offline release: sync first, then the rest",
                    "items": [
                        {"key": "decide", "name": "Pick the sync engine", "group": "first",
                         "before": [["your pick", None, 0]], "build": 0},
                        {"key": "sync", "name": "Offline sync", "group": "first", "build": 240, "review": 45, "fix": 60,
                         "flags": ["after:decide"]},
                        {"key": "prefetch", "name": "Prefetch the next page of results", "group": "then",
                         "before": [["measure", "measure:prefetch", 30], ["your go-ahead", None, 0]],
                         "build": 60, "review": 20},
                        {"key": "icons", "name": "New icon set", "group": "then", "build": 30, "review": 10},
                        {"key": "done-thing", "name": "Crash reporter opt-in", "group": "then", "build": 20},
                    ]}
    p.plan(plan)
    p.notes()
    p.agent("measure:prefetch", status="done", start_ago=2 * HOUR, quiet_s=90 * MIN)
    p.agent("build:icons", start_ago=15 * MIN, quiet_s=20)
    p.agent("build:done-thing", status="done", start_ago=3 * HOUR, quiet_s=2 * HOUR)
    p.store = {"git": dict(GIT), "tokens": TOKENS}
    p.ps = PS_SAFE


def later_releases(p: Project) -> None:
    """Three releases: this one, the next, and the one after it (`later`). The later release's items
    not yet begun wait on the next release, never on this one, but for one whose first stage is the
    owner's, which waits on the owner; one runs after another of its own release; one begun early
    reads its stage and its time."""
    plan = release_plan()
    plan["items"] = plan["items"][:1]
    plan["other"] = []
    plan["next"] = {"release": "1.5.0", "about": "the offline release", "items": [
        {"key": "decide", "name": "Pick the sync engine", "group": "first", "before": [["your pick", None, 0]], "build": 0},
        {"key": "sync", "name": "Offline sync", "group": "first", "build": 240, "review": 45, "fix": 60,
         "flags": ["after:decide"]},
        {"key": "icons", "name": "New icon set", "group": "then", "build": 30, "review": 10}]}
    plan["later"] = [{"release": "1.6.0", "about": "once 1.5.0 is out", "items": [
        {"key": "rollout", "name": "First week of 1.5.0 on the early sites", "group": "after the release",
         "before": [["first-day check", "check:rollout", 60], ["a week of use", None, 0]], "review": 30},
        {"key": "plugins", "name": "Plugin API", "group": "then", "before": [["your scope", None, 0]],
         "build": 180, "review": 40, "fix": 45},
        {"key": "themes", "name": "Themes as plugins", "group": "then", "build": 60, "review": 20,
         "flags": ["after:plugins"]},
        {"key": "search", "name": "Search across workspaces", "group": "then",
         "before": [["design", "design:search", 45]], "build": 120, "review": 30}]}]
    p.plan(plan)
    p.notes()
    p.agent("build:icons", start_ago=15 * MIN, quiet_s=20)
    p.agent("design:search", start_ago=25 * MIN, quiet_s=30, run="wf_run-d", action="Write · docs/design/search.md")
    p.store = {"git": dict(GIT), "tokens": TOKENS}
    p.ps = PS_SAFE


def agents_many(p: Project) -> None:
    """More agents than rows: the quiet ones past red always get a row (silence counts as
    stopped only after 90 min here, so an agent can be idle past red and still running)."""
    p.write_config("""
        [agents]
        silent_stopped_min = 90
        rows = 10
        """)
    plan = release_plan()
    p.plan(plan)
    p.notes()
    for i in range(20):
        quiet = (60 + i) * MIN if i < 6 else (i + 1) * 20
        p.agent(f"sweep:part-{i:02d}", start_ago=3 * HOUR, quiet_s=quiet, run=f"wf_sweep-{i % 3}",
                action=f"Bash · Sweep shard {i}")
    p.store = {"git": dict(GIT)}
    p.ps = PS_SAFE


SCENARIOS = {"empty": empty, "release-midway": midway, "next-release": next_release, "agents-many": agents_many,
             "later-releases": later_releases}

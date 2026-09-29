"""Every kind of wait (other's `after`, items' `after:<key>` and `after_all`, next's `after:<key>`)
against a target that has a finish time, has none, and is finished.

The bug they pin: a row "after" a live job read "queued" with its own 20 minutes and was named
"next to finish" the moment the job's row lost its finish time (its live source read "stalled");
begun early, such a row did the same while the job still ran."""

from __future__ import annotations

import pytest

from lsw_mission_control import testing
from lsw_mission_control.render.next_release import next_panel
from lsw_mission_control.render.other import other_panel
from lsw_mission_control.render.release import release_panel

from scenarios import HOUR, MIN, Project, midway, release_plan

NIGHTLY = "Nightly data import · 1,200 files"


def row(plain: str, name: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {name}"))


def other_text(p: Project) -> str:
    return testing.render_text(other_panel(p.engine().build_frame(120), 120), 120)[0]


def with_cleanup(p: Project, first: bool = False, after: str = NIGHTLY, job: str = "running") -> None:
    """midway, plus a 20-min row after `after` (listed first: a row before its target still waits)."""
    midway(p, job=job)
    plan = release_plan()
    cleanup = {"name": "Cleanup of the import", "after": after, "stages": [["cleanup", "cleanup:import", 20]]}
    plan["other"] = [cleanup] + plan["other"] if first else plan["other"] + [cleanup]
    p.plan(plan)


# ── other: "after" ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("first", [False, True])
def test_other_after_a_target_with_a_finish_time(tmp_path, first):
    p = Project(tmp_path)
    with_cleanup(p, first=first)
    plain = other_text(p)
    assert "~2h30 · 16:43" in row(plain, NIGHTLY)
    # its own 20 min after the job's 2h30: never before its target
    assert "after nightly" in row(plain, "Cleanup") and "~2h50 · 17:03" in row(plain, "Cleanup")
    assert "next to finish: Docs site refresh" in plain


@pytest.mark.parametrize("job", ["stalled", "unknown", "not-live", "error"])
@pytest.mark.parametrize("first", [False, True])
def test_other_after_a_target_with_no_finish_time(tmp_path, job, first):
    p = Project(tmp_path)
    with_cleanup(p, first=first, job="running" if job == "error" else job)
    if job == "error":
        p.plugin_state["fail"] = "live_job"
    plain = other_text(p)
    assert row(plain, NIGHTLY).rstrip(" │").endswith("—")
    cleanup = row(plain, "Cleanup")
    # it still waits, and has no finish time either; the old code read "queued · ~20m · 14:33" here
    assert "after nightly" in cleanup and cleanup.rstrip(" │").endswith("—") and "queued" not in cleanup
    assert "next to finish: Docs site refresh" in plain


def test_other_after_a_paused_target(tmp_path):
    p = Project(tmp_path)
    with_cleanup(p, after="Dependency upgrades")
    cleanup = row(other_text(p), "Cleanup")
    assert "after dependen" in cleanup and cleanup.rstrip(" │").endswith("—")  # the stage column cuts it


def test_other_after_a_finished_target(tmp_path):
    p = Project(tmp_path)
    with_cleanup(p, job="done")
    p.agent("verify:import", status="done", start_ago=2 * HOUR, quiet_s=HOUR)
    plain = other_text(p)
    assert "done" in row(plain, NIGHTLY)
    assert "queued" in row(plain, "Cleanup") and "~20m · 14:33" in row(plain, "Cleanup")
    assert "next to finish: Cleanup of the import  14:33" in plain


def test_other_after_a_target_the_plan_no_longer_holds(tmp_path):
    """a finished row leaves the plan by hand: what waited on it no longer waits"""
    p = Project(tmp_path)
    with_cleanup(p, after="Some row that was removed")
    assert "queued" in row(other_text(p), "Cleanup")


def job_and_cleanup(p: Project, job: str = "running", stages=None, first: bool = True) -> None:
    """only the job's row (2h30 left) and a row after it: nothing else can be next to finish"""
    midway(p, job=job)
    plan = release_plan()
    cleanup = {"name": "Cleanup of the import", "after": NIGHTLY,
               "stages": stages or [["cleanup", "cleanup:import", 20]]}
    plan["other"] = [cleanup, plan["other"][0]] if first else [plan["other"][0], cleanup]
    p.plan(plan)


@pytest.mark.parametrize("first", [False, True])
def test_other_a_begun_row_never_finishes_before_its_target(tmp_path, first):
    """begun early (5 of its 20 min): it still ends no sooner than the job (16:43), and a tie for
    "next to finish" goes to the job. It read "~15m · 14:28" and was next to finish."""
    p = Project(tmp_path)
    job_and_cleanup(p, first=first)
    p.agent("cleanup:import", start_ago=5 * MIN, quiet_s=30)
    plain = other_text(p)
    cleanup = row(plain, "Cleanup")
    assert "cleanup" in cleanup and "~2h30 · 16:43" in cleanup  # its running stage shows, not the wait
    assert "next to finish: Nightly data import · 1,200 files  16:43" in plain


@pytest.mark.parametrize("job", ["stalled", "unknown", "not-live", "error"])
def test_other_a_begun_row_after_a_target_with_no_finish_time(tmp_path, job):
    """it read "cleanup · ~15m · 14:28" and was next to finish while the job it runs after had stopped"""
    p = Project(tmp_path)
    job_and_cleanup(p, job="running" if job == "error" else job)
    if job == "error":
        p.plugin_state["fail"] = "live_job"
    p.agent("cleanup:import", start_ago=5 * MIN, quiet_s=30)
    plain = other_text(p)
    cleanup = row(plain, "Cleanup")
    assert "cleanup" in cleanup and cleanup.rstrip(" │").endswith("—")
    assert "next to finish" not in plain


def test_other_a_begun_row_between_its_stages_shows_the_wait(tmp_path):
    """its first stage done early, nothing of its own runs: "after nightly", the job's 2h30, then its 15 min"""
    p = Project(tmp_path)
    job_and_cleanup(p, stages=[["sweep", "cleanup:sweep", 10], ["cleanup", "cleanup:import", 15]])
    p.agent("cleanup:sweep", status="done", start_ago=20 * MIN, quiet_s=10 * MIN)
    cleanup = row(other_text(p), "Cleanup")
    assert "after nightly" in cleanup and "~2h45 · 16:58" in cleanup


def test_other_a_begun_stage_runs_beside_the_wait(tmp_path):
    """what has begun runs on beside the wait; what has not comes after it: the longer of its
    running stage (2h40 left) and the job (2h30), then its last 15 min"""
    p = Project(tmp_path)
    job_and_cleanup(p, stages=[["sweep", "cleanup:sweep", 180], ["cleanup", "cleanup:import", 15]])
    p.agent("cleanup:sweep", start_ago=20 * MIN, quiet_s=30)
    cleanup = row(other_text(p), "Cleanup")
    assert "sweep" in cleanup and "~2h55 · 17:08" in cleanup


def test_other_a_stage_begun_beside_a_live_job(tmp_path):
    """the job has 2h00 left; its verify stage (30 min) began early, 10 min ago: it runs on beside
    the job, so the row ends with the job (it read ~2h20, the verify's 20 min after the job)"""
    p = Project(tmp_path)
    midway(p)
    p.agent("verify:import", start_ago=10 * MIN, quiet_s=30, run="wf_verify")
    nightly = row(other_text(p), NIGHTLY)
    assert "import" in nightly and "~2h00 · 16:13" in nightly


def test_other_after_a_failed_target(tmp_path):
    """its target reads "needs rerun" with no time: neither has it, and it is not next to finish
    (it read "after alpha · ~20m · 14:33", next to finish)"""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["other"] = [{"name": "Alpha job", "stages": [["a", "alpha:a", 30]]},
                     {"name": "Beta job", "after": "Alpha job", "stages": [["b", "beta:b", 20]]}]
    p.plan(plan)
    p.agent("alpha:a", status="failed", start_ago=HOUR, quiet_s=40 * MIN, run="wf_alpha")
    plain = other_text(p)
    assert "needs rerun" in row(plain, "Alpha job")
    beta = row(plain, "Beta job")
    assert "after alpha" in beta and beta.rstrip(" │").endswith("—") and "next to finish" not in plain


# ── release items: "after:<key>" and "after_all" ───────────────────────────────────────────────
def release_text(p: Project) -> str:
    return testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)[0]


def test_release_after_a_target_with_a_finish_time(tmp_path):
    """Dark mode polish runs after Faster startup (ends 15:36): 1h14 of its own after that."""
    p = Project(tmp_path)
    midway(p)
    plain = release_text(p)
    assert "≥1h23 · 15:36" in row(plain, "Faster startup")
    assert "after faster" in row(plain, "Dark mode polish") and "~2h37 · 16:50" in row(plain, "Dark mode polish")


def test_release_every_after_flag_counts_and_the_last_to_finish_binds(tmp_path):
    """it read only the first flag: after Export (14:23), never after Faster startup (15:36)."""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][3]["flags"] = ["after:export", "after:startup"]
    p.plan(plan)
    dark = row(release_text(p), "Dark mode polish")
    assert "after faster" in dark and "~2h37 · 16:50" in dark


def test_release_an_item_listed_before_its_target_still_waits(tmp_path):
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    items = plan["items"]
    items[1], items[3] = items[3], items[1]  # Dark mode polish (after:startup) before Faster startup
    items[3], items[2] = items[2], items[3]
    p.plan(plan)
    dark = row(release_text(p), "Dark mode polish")
    assert "after faster" in dark and "~2h37 · 16:50" in dark


def test_release_after_a_finished_target(tmp_path):
    p = Project(tmp_path)
    midway(p)
    p.agent("build:startup", status="done", start_ago=2.4 * HOUR, quiet_s=2 * HOUR, run="wf_startup-2")
    p.agent("review:startup", status="done", start_ago=2 * HOUR, quiet_s=1.5 * HOUR, run="wf_startup-2")
    plain = release_text(p)
    dark = row(plain, "Dark mode polish")
    assert "queued" in dark and "after" not in dark and "~1h14 · 15:27" in dark  # 45 + 15 + 20 x 0.7: its own


def test_release_after_all_waits_for_the_worst_of_the_rest(tmp_path):
    """Security review (after_all, owner_ok) starts after Dark mode polish (16:50): 2h15 of its own."""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][5]["flags"] = ["after_all"]
    p.plan(plan)
    sec = row(release_text(p), "Security review")
    assert "after all" in sec and "~4h52 · 19:05" in sec


def test_release_after_all_once_the_rest_is_done(tmp_path):
    p = Project(tmp_path)
    p.plan({"release": "1.4.0", "items": [
        {"name": "Search filters", "key": None},
        {"name": "Security review", "key": "security", "build": 60, "review": 30, "fix": 30, "flags": ["after_all"]}]})
    p.notes()
    sec = row(release_text(p), "Security review")
    assert "queued" in sec and "~1h51 · 16:04" in sec  # 60 + 30 + 30 x the fix share 0.7


def test_release_a_begun_item_never_finishes_before_its_target(tmp_path):
    """Dark mode polish begun early (build 10 of 45 min) while Faster startup runs to 15:36: it ends
    no sooner, then its review and fix (15 + 20 x 0.7 min). It read "~1h09 · 15:22"."""
    p = Project(tmp_path)
    midway(p)
    p.agent("build:dark", start_ago=10 * MIN, quiet_s=30, run="wf_dark")
    plain = release_text(p)
    assert "≥1h23 · 15:36" in row(plain, "Faster startup")
    dark = row(plain, "Dark mode polish")
    assert "build" in dark and "~1h52 · 16:05" in dark


def test_release_after_a_failed_item_counts_its_rerun(tmp_path):
    """the release's finish counts a failed item's re-run, so what runs after it waits that long
    (build 120 + review 30 + fix 45 x 0.7 min, then its own 45 + 15 + 20 x 0.7: 255.5 min)"""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][3]["flags"] = ["after:a11y"]
    p.plan(plan)
    plain = release_text(p)
    assert "needs rerun" in row(plain, "Accessibility audit")
    dark = row(plain, "Dark mode polish")
    assert "after access" in dark and "~4h15 · 18:28" in dark


def test_release_a_paused_item_is_held_not_failed(tmp_path):
    """an item the owner holds ("paused": true) reads "paused" with no finish of its own, whether its
    agent failed (Accessibility audit) or nothing has begun (Security review); the header counts it
    as paused, never failed, and what runs after it still waits for its time left."""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][4]["paused"] = True
    plan["items"][5]["paused"] = True
    plan["items"][3]["flags"] = ["after:a11y"]
    p.plan(plan)
    plain = release_text(p)
    audit, security = row(plain, "Accessibility audit"), row(plain, "Security review")
    for r in (audit, security):
        assert "paused" in r and "needs rerun" not in r and "queued" not in r and "·" not in r.split("━")[-1]
    assert "2 paused" in plain and "failed" not in plain
    assert "after access" in row(plain, "Dark mode polish")


def test_release_items_always_have_a_finish_time(tmp_path):
    """a release item's wait can lack a finish time only through a target that lacks one, and none
    does: a cycle (a typo) is broken, and every row still gets a time."""
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][2]["flags"] = ["after:dark"]  # Faster startup after Dark mode polish, which is after it
    p.plan(plan)
    plain = release_text(p)
    for name in ("Faster startup", "Dark mode polish", "Security review"):
        assert "·" in row(plain, name).split("━")[-1]


# ── next release: "after:<key>" ────────────────────────────────────────────────────────────────
def next_text(p: Project) -> str:
    return testing.render_text(next_panel(p.engine().build_frame(120), 120), 120)[0]


def next_plan(p: Project, target_state: str, first: bool = False) -> None:
    """a next release where Offline sync runs after Pick the sync engine, in some state"""
    plan = release_plan()
    plan["items"] = plan["items"][:1]
    plan["other"] = []
    decide = {"key": "decide", "name": "Pick the sync engine", "group": "first", "build": 30}
    sync = {"key": "sync", "name": "Offline sync", "group": "first", "build": 240, "flags": ["after:decide"]}
    plan["next"] = {"release": "1.5.0", "items": [sync, decide] if first else [decide, sync]}
    p.plan(plan)
    p.notes()
    if target_state == "running":
        p.agent("build:decide", start_ago=10 * MIN, quiet_s=20)
    elif target_state == "done":
        p.agent("build:decide", status="done", start_ago=HOUR, quiet_s=30 * MIN)


@pytest.mark.parametrize("first", [False, True])
@pytest.mark.parametrize("target", ["planned", "running"])
def test_next_after_an_unfinished_target(tmp_path, target, first):
    p = Project(tmp_path)
    next_plan(p, target, first)
    plain = next_text(p)
    sync = row(plain, "Offline sync")
    assert "after pick" in sync and sync.rstrip(" │").endswith("—")
    if target == "running":
        assert "~20m · 14:33" in row(plain, "Pick the sync")


@pytest.mark.parametrize("target", ["planned", "running", "your-pick"])
def test_next_a_begun_item_after_an_unfinished_target(tmp_path, target):
    """Offline sync (15 min) begun early, 10 min in: it never ends before Pick the sync engine, and has
    no finish time while that has none (planned, or waiting on the owner's pick)"""
    plan = release_plan()
    plan["items"] = plan["items"][:1]
    plan["other"] = []
    decide = {"key": "decide", "name": "Pick the sync engine", "group": "first", "build": 30}
    if target == "your-pick":
        decide["before"] = [["measure", "measure:decide", 30], ["your pick", None, 0]]
    sync = {"key": "sync", "name": "Offline sync", "group": "first", "build": 15, "flags": ["after:decide"]}
    plan["next"] = {"release": "1.5.0", "items": [sync, decide]}
    p = Project(tmp_path)
    p.plan(plan)
    p.notes()
    if target == "running":
        p.agent("build:decide", start_ago=10 * MIN, quiet_s=20)
    elif target == "your-pick":
        p.agent("measure:decide", status="done", start_ago=HOUR, quiet_s=30 * MIN)
    p.agent("build:sync", start_ago=10 * MIN, quiet_s=20, run="wf_sync")
    plain = next_text(p)
    sync_row = row(plain, "Offline sync")
    assert "build" in sync_row
    if target == "running":
        assert "~20m · 14:33" in row(plain, "Pick the sync") and "~20m · 14:33" in sync_row  # not ~5m · 14:18
    else:
        assert sync_row.rstrip(" │").endswith("—")
    if target == "your-pick":
        assert "your pick" in row(plain, "Pick the sync")


def test_next_after_a_finished_target(tmp_path):
    p = Project(tmp_path)
    next_plan(p, "done")
    sync = row(next_text(p), "Offline sync")
    assert "planned" in sync and "after" not in sync



def test_a_paused_release_items_agent_is_not_at_work(tmp_path):
    """Agents at work leaves out the agents of a release item the owner holds, as it does an other
    item's: a held item's run was stopped, whatever its journal last said (export's fix runs midway)."""
    from lsw_mission_control.render.agents import agents_panel
    p = Project(tmp_path)
    midway(p)

    def agents_text() -> str:
        return testing.render_text(agents_panel(p.engine().build_frame(120), 120, []), 120)[0]

    assert "fix:export" in agents_text()
    plan = release_plan()
    plan["items"][1]["paused"] = True
    p.plan(plan)
    assert "fix:export" not in agents_text()


def test_release_a_hold_with_an_end_counts_the_hold(tmp_path):
    """"paused_until" holds an item until a time: the row says when it resumes, and what runs after
    it waits for the hold AND its re-run (a day, then 120 + 30 + 45 x 0.7 min), not its re-run alone."""
    import datetime as dt

    from conftest import NOW
    p = Project(tmp_path)
    midway(p)
    plan = release_plan()
    plan["items"][4]["paused_until"] = dt.datetime.fromtimestamp(NOW + 86400, tz=dt.timezone.utc).isoformat()
    plan["items"][3]["flags"] = ["after:a11y"]
    p.plan(plan)
    plain = release_text(p)
    audit = row(plain, "Accessibility audit")
    assert "paused" in audit and "from " in audit and "failed" not in plain
    dark = row(plain, "Dark mode polish")
    assert "after access" in dark and "~1d" in dark


def test_parse_paused_until():
    from lsw_mission_control.plan import parse_plan
    plan = parse_plan({"release": "1", "items": [
        {"name": "A", "key": "a", "paused_until": "2026-10-04T13:00:00+10:00"}, {"name": "B", "key": "b"}]})
    a, b = plan.items
    assert a.paused is True and a.paused_until == 1791082800.0 and b.paused is False and b.paused_until is None
    with pytest.raises(ValueError, match="paused_until of 'A'"):
        parse_plan({"release": "1", "items": [{"name": "A", "key": "a", "paused_until": "Sunday"}]})

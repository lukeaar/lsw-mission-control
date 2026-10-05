"""Golden renders of whole frames and of each panel's states, plain and styled, at several widths."""

from __future__ import annotations

import json
import re

import pytest

from lsw_mission_control import testing
from lsw_mission_control.connectivity import CAPTIVE, NO_ANSWER, NO_NETWORK, OFFLINE, ONLINE, UNKNOWN, Conn
from lsw_mission_control.render.frame import bottom_line
from lsw_mission_control.render.release import release_panel
from lsw_mission_control.render.usage import usage_panel

from conftest import NOW
from golden_util import check
from scenarios import HOUR, MIN, SCENARIOS, Project, gh_store, midway, release_plan, ts

WIDTHS = (80, 100, 120, 150)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("width", WIDTHS)
def test_whole_frame(tmp_path, update_golden, name, width):
    p = Project(tmp_path)
    SCENARIOS[name](p)
    engine = p.engine()
    plain, styled = testing.render_engine(engine, width)
    assert not engine.last_error, engine.last_error
    check(f"{name}/{width}.txt", plain, update_golden)
    check(f"{name}/{width}.ansi", styled, update_golden)


TAG_PHASES = {
    "todo": {},
    "ci-running": {"ci": {"status": "in_progress", "createdAt": ts(NOW - 5 * MIN)}},
    "ci-queued": {"ci": {"status": "queued", "createdAt": ts(NOW - 1 * MIN)}},
    "ci-passed": {"ci": {"status": "completed", "conclusion": "success", "createdAt": ts(NOW - 20 * MIN)}},
    "ci-failed": {"ci": {"status": "completed", "conclusion": "failure", "createdAt": ts(NOW - 20 * MIN)}},
    "tagged": {"tag": True},
    "release-running": {"tag": True, "rel": {"status": "in_progress", "createdAt": ts(NOW - 4 * MIN)}},
    "release-queued": {"tag": True, "rel": {"status": "queued", "createdAt": ts(NOW - 1 * MIN)}},
    "released": {"tag": True, "rel": {"status": "completed", "conclusion": "success", "createdAt": ts(NOW - 30 * MIN),
                                      "updatedAt": ts(NOW - 12 * MIN)}},
    "release-failed": {"tag": True, "rel": {"status": "completed", "conclusion": "failure", "createdAt": ts(NOW - 30 * MIN),
                                            "updatedAt": ts(NOW - 12 * MIN)}},
    # past the median of past runs; main's CI began after the release run (a push after the tag)
    "release-overdue": {"tag": True, "ci": {"status": "in_progress", "createdAt": ts(NOW - 30 * MIN)},
                        "rel": {"status": "in_progress", "createdAt": ts(NOW - 70 * MIN)}},
    "ci-overdue": {"ci": {"status": "in_progress", "createdAt": ts(NOW - 40 * MIN)}},
}


@pytest.mark.parametrize("phase", sorted(TAG_PHASES))
def test_release_tag_phases(tmp_path, update_golden, phase):
    """The final merge done, then every state of the tag row (CI, tag, release run)."""
    p = Project(tmp_path)
    midway(p)
    for kind in ("build", "review", "fix"):
        p.agent(f"{kind}:final-merge", status="done", run="wf_merge", start_ago={"build": 3, "review": 2, "fix": 1.5}[kind] * HOUR,
                quiet_s={"build": 2, "review": 1.6, "fix": 1}[kind] * HOUR)
    p.store["release_gh"] = gh_store(**TAG_PHASES[phase])
    engine = p.engine()
    f = engine.build_frame(120)
    plain, styled = testing.render_text(release_panel(f, 120)[0], 120)
    check(f"tag-phases/{phase}.txt", plain, update_golden)
    check(f"tag-phases/{phase}.ansi", styled, update_golden)


def _row(plain: str, start: str) -> str:
    return next(line for line in plain.splitlines() if line.startswith(f"│ {start}"))


def test_a_release_change_starts_the_final_merge_and_the_tag_row_afresh(tmp_path):
    """The bug: after 1.4.0 -> 1.5.0 the new release's panel showed 1.4.0's final merge as done
    ("took 2h00"), so the tag row took main's CI (an unrelated commit) for the tag's and the header
    promised the release within the hour. The journals still held the shipped final merge."""
    p = Project(tmp_path)
    midway(p)
    for kind in ("build", "review", "fix"):
        p.agent(f"{kind}:final-merge", status="done", run="wf_merge", start_ago={"build": 3, "review": 2, "fix": 1.5}[kind] * HOUR,
                quiet_s={"build": 2, "review": 1.6, "fix": 1}[kind] * HOUR)
    # main's CI, started after that final merge ended: not the new release's tag CI
    p.store["release_gh"] = gh_store(**TAG_PHASES["ci-running"])
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    assert "took 2h00" in _row(plain, "Final merge") and "CI running" in _row(plain, "Tag 1.4.0")

    p.plan(release_plan(release="1.5.0"))
    # a new process: the store and the journals are all it has
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    merge_row, tag_row = _row(plain, "Final merge"), _row(plain, "Tag 1.5.0")
    # the last item ends 19:05; then the final merge (3h30), then CI, tag and release (18 + 20 + 9 min)
    assert "after all above" in merge_row and "from 19:05" in merge_row and "done 22:35" in merge_row
    assert "CI, tag, release" in tag_row and "from 22:35" in tag_row and "out 23:22" in tag_row
    assert "release out 23:22 (in 9h09)" in plain and "CI running" not in plain
    assert json.loads((p.cache / "finished.json").read_text())["since"] == NOW

    # a final merge of the new release, begun after the change: tracked as always
    testing.freeze(NOW + 2 * HOUR)
    p.agent("build:final-merge", run="wf_merge2", start_ago=-1 * HOUR, quiet_s=-2 * HOUR + 30)
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    merge_row, tag_row = _row(plain, "Final merge"), _row(plain, "Tag 1.5.0")
    assert "◉─○─○" in merge_row and "since 15:13" in merge_row
    assert "CI, tag, release" in tag_row  # the merge is not done: main's CI is still not the tag's


def test_a_release_change_undone_keeps_the_final_merge(tmp_path):
    """the plan's release moved to 1.5.0 and back a minute later (a typo, a move undone): 1.4.0's
    final merge is done again, and the tag row reads its CI again. It read "after all above"."""
    p = Project(tmp_path)
    midway(p)
    for kind in ("build", "review", "fix"):
        p.agent(f"{kind}:final-merge", status="done", run="wf_merge", start_ago={"build": 3, "review": 2, "fix": 1.5}[kind] * HOUR,
                quiet_s={"build": 2, "review": 1.6, "fix": 1}[kind] * HOUR)
    p.store["release_gh"] = gh_store(**TAG_PHASES["ci-running"])
    p.engine().build_frame(120)
    p.plan(release_plan(release="1.5.0"))
    p.engine().build_frame(120)
    testing.freeze(NOW + MIN)
    p.plan(release_plan())
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    assert "took 2h00" in _row(plain, "Final merge") and "CI running" in _row(plain, "Tag 1.4.0")


def test_a_final_merge_done_by_hand(tmp_path):
    """A hotfix released by hand, with no final-merge workflow: the plan records when the merge was
    done. The row reads "done by hand" and the tag row takes main's CI begun after it; a mark
    naming another release is ignored, and a final merge a workflow runs wins over the mark."""
    p = Project(tmp_path)
    midway(p)
    p.store["release_gh"] = gh_store(**TAG_PHASES["ci-running"])  # main's CI began 5 min ago
    p.plan(release_plan(final_merge_by_hand={"release": "1.4.0", "at": ts(NOW - 10 * MIN)}))
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    assert "done by hand" in _row(plain, "Final merge") and "CI running" in _row(plain, "Tag 1.4.0")

    p.plan(release_plan(final_merge_by_hand={"release": "1.3.0", "at": ts(NOW - 10 * MIN)}))
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    assert "after all above" in _row(plain, "Final merge") and "CI running" not in plain

    p.plan(release_plan(final_merge_by_hand={"release": "1.4.0", "at": ts(NOW - 10 * MIN)}))
    p.agent("build:final-merge", run="wf_merge", start_ago=20 * MIN, quiet_s=30)
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    assert "done by hand" not in plain and "◉" in _row(plain, "Final merge")


@pytest.mark.parametrize("merge_done", [False, True])
def test_the_tag_row_reads_mains_ci_only_after_the_final_merge(merge_done):
    from lsw_mission_control.render.release import tag_milestone

    ci = {"status": "in_progress", "createdAt": ts(NOW - 5 * MIN)}
    m = tag_milestone({"ci": ci}, NOW - HOUR, merge_done, 18.0, 9.0, 20.0)
    assert m["status"] == ("running" if merge_done else "todo")
    assert m.get("phase", "") == ("CI running" if merge_done else "")


def test_mains_ci_begun_after_the_release_run_never_starts_the_tag_row():
    """The bug: the tag's release run was created first, then a push to main started CI on main's new
    HEAD, and the row read "since" that CI run: the start was main's CI whatever its time. A CI run
    created after the release run is a later commit's, never the release's: the row starts no later
    than the release run. The release commit's own CI, begun before the release run, still starts it."""
    from lsw_mission_control.render.release import tag_milestone

    later_ci = {"status": "in_progress", "createdAt": ts(NOW - 30 * MIN)}
    own_ci = {"status": "completed", "conclusion": "success", "createdAt": ts(NOW - 90 * MIN)}
    running = {"status": "in_progress", "createdAt": ts(NOW - 70 * MIN)}
    released = {"status": "completed", "conclusion": "success", "createdAt": ts(NOW - 70 * MIN),
                "updatedAt": ts(NOW - 5 * MIN)}
    for rel in (running, released):
        m = tag_milestone({"tag": True, "ci": later_ci, "rel": rel}, NOW - 2 * HOUR, True, 18.0, 9.0, 20.0)
        assert m["start"] == NOW - 70 * MIN
        m = tag_milestone({"tag": True, "ci": own_ci, "rel": rel}, NOW - 2 * HOUR, True, 18.0, 9.0, 20.0)
        assert m["start"] == NOW - 90 * MIN
    # no release run listed yet: main's CI still starts the row, as before
    m = tag_milestone({"tag": True, "ci": later_ci}, NOW - 2 * HOUR, True, 18.0, 9.0, 20.0)
    assert m["start"] == NOW - 30 * MIN and m["phase"] == "tagged"


def test_a_run_past_its_median_has_a_lower_bound_never_a_minute():
    """The bug: a release run far past the median of past runs (its suite alone runs longer) read
    "~59s" and the header "release out … (in 1m)" from its median on, for as long as it ran: its end
    was a minute from now. Past its median a run (the release run, or CI on main) has at least
    10 min left, or a quarter of its time so far, as a stage past its estimate has, and is
    marked `over`; within its median nothing changes."""
    from lsw_mission_control.render.release import tag_milestone

    def rel(ago: float, status: str = "in_progress") -> dict:
        return {"tag": True, "rel": {"status": status, "createdAt": ts(NOW - ago)}}

    m = tag_milestone(rel(4 * MIN), NOW - HOUR, True, 18.0, 9.0, 20.0)  # within its median: as before
    assert (m["end"], m["over"]) == (NOW + 5 * MIN, 0.0)
    m = tag_milestone(rel(8.5 * MIN), NOW - HOUR, True, 18.0, 9.0, 20.0)  # its last minute: still a minute
    assert (m["end"], m["over"]) == (NOW + MIN, 0.0)
    m = tag_milestone(rel(20 * MIN), NOW - HOUR, True, 18.0, 9.0, 20.0)  # past it: at least 10 min
    assert (m["end"], m["over"]) == (NOW + 10 * MIN, 11 * MIN)
    m = tag_milestone(rel(70 * MIN), NOW - HOUR, True, 18.0, 9.0, 20.0)  # long past it: a quarter of 70 min
    assert (m["end"], m["over"], m["phase"]) == (NOW + 17.5 * MIN, 61 * MIN, "release running")
    m = tag_milestone(rel(70 * MIN, "queued"), NOW - HOUR, True, 18.0, 9.0, 20.0)  # queued as long
    assert (m["end"], m["over"], m["phase"]) == (NOW + 17.5 * MIN, 61 * MIN, "release queued")
    # CI on main past its median: the same rule, then the hands-on steps and the release run
    ci = {"ci": {"status": "in_progress", "createdAt": ts(NOW - 40 * MIN)}}
    m = tag_milestone(ci, NOW - HOUR, True, 18.0, 9.0, 20.0)
    assert (m["end"], m["over"], m["phase"]) == (NOW + (10 + 20 + 9) * MIN, 22 * MIN, "CI running")
    ci = {"ci": {"status": "in_progress", "createdAt": ts(NOW - 5 * MIN)}}
    m = tag_milestone(ci, NOW - HOUR, True, 18.0, 9.0, 20.0)
    assert (m["end"], m["over"]) == (NOW + (13 + 20 + 9) * MIN, 0.0)


def test_an_overdue_release_run_reads_as_a_lower_bound_in_the_row_and_the_header(tmp_path):
    """Both defects as the owner saw them: main moved on after the tag (its CI began after the
    release run), and the release run is far past its median. The row starts at the release run and
    reads `≥`, as a stage past its estimate does, and so does the header; half an hour on, still
    running, the bound has grown with it (it read "~59s" and "in 1m" until the run ended)."""
    p = Project(tmp_path)
    midway(p)
    for kind in ("build", "review", "fix"):
        p.agent(f"{kind}:final-merge", status="done", run="wf_merge", start_ago={"build": 3, "review": 2, "fix": 1.5}[kind] * HOUR,
                quiet_s={"build": 2, "review": 1.6, "fix": 1}[kind] * HOUR)
    p.store["release_gh"] = gh_store(**TAG_PHASES["release-overdue"])
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    row = _row(plain, "Tag 1.4.0")
    assert "release running" in row and "since 13:03" in row and "≥17m · 14:30" in row and "~" not in row
    assert "release out 14:30 (in ≥17m)" in plain

    testing.freeze(NOW + 30 * MIN)
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    row = _row(plain, "Tag 1.4.0")
    assert "since 13:03" in row and "≥25m · 15:08" in row and "release out 15:08 (in ≥25m)" in plain

    # CI on main past its median, before the tag: the same lower bound
    testing.freeze(NOW)
    p.store["release_gh"] = gh_store(**TAG_PHASES["ci-overdue"])
    plain, _ = testing.render_text(release_panel(p.engine().build_frame(120), 120)[0], 120)
    row = _row(plain, "Tag 1.4.0")
    assert "CI running" in row and "since 13:33" in row and "≥39m · 14:52" in row
    assert "release out 14:52 (in ≥39m)" in plain


USAGE = {
    "missing": None,
    "fresh-terminal": {"at": NOW - 60, "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW + 3 * HOUR},
                                                       "seven_day": {"used_percentage": 55, "resets_at": NOW + 2 * 86400}}},
    "stale": {"at": NOW - 40 * MIN, "rate_limits": {"five_hour": {"used_percentage": 91, "resets_at": NOW + 1 * HOUR}},
              "source": "probe"},
    "reset-passed": {"at": NOW - 6 * HOUR, "rate_limits": {"five_hour": {"used_percentage": 97, "resets_at": NOW - 1 * HOUR},
                                                           "seven_day": {"used_percentage": 40, "resets_at": NOW + 86400}}},
    "warning": {"at": NOW - 60, "rate_limits": {"five_hour": {"used_percentage": 85, "resets_at": NOW + HOUR}},
                "status": "allowed_warning", "source": "probe"},
    "rejected-overage": {"at": NOW - 60, "rate_limits": {"seven_day": {"used_percentage": 100, "resets_at": NOW + HOUR}},
                         "status": "rejected", "overage": True, "source": "probe"},
    "garbage": ["not", "a", "record"],
}


@pytest.mark.parametrize("state", sorted(USAGE))
def test_usage_states(tmp_path, update_golden, state):
    p = Project(tmp_path)
    midway(p)
    rec = USAGE[state]
    if isinstance(rec, list):
        p.usage_dir.mkdir(parents=True, exist_ok=True)
        (p.usage_dir / "usage.json").write_text(json.dumps(rec))
    else:
        p.usage(rec)
    if state == "missing":
        p.store.pop("tokens")
        p.store.pop("tokens_by_model")
    engine = p.engine()
    plain, styled = testing.render_text(usage_panel(engine.build_frame(100), 100), 100)
    check(f"usage/{state}.txt", plain, update_golden)
    check(f"usage/{state}.ansi", styled, update_golden)


@pytest.mark.parametrize("job", ["running", "done", "not-live", "stalled", "unknown", "error"])
def test_other_after_server(tmp_path, update_golden, job):
    """The row that waits on a plugin's live job, in every state the job can be in."""
    from lsw_mission_control.render.other import other_panel

    p = Project(tmp_path)
    midway(p, job="running" if job == "error" else job)
    if job == "error":
        p.plugin_state["fail"] = "live_job"
    engine = p.engine()
    f = engine.build_frame(120)
    plain, styled = testing.render_text(other_panel(f, 120), 120)
    check(f"other/{job}.txt", plain, update_golden)
    check(f"other/{job}.ansi", styled, update_golden)


def test_a_running_job_keeps_its_time_when_the_stages_after_it_are_done(tmp_path):
    """D7: with every later stage done, the row read "~0s · now" (100%) while the job still ran."""
    from lsw_mission_control.render.other import other_panel

    p = Project(tmp_path)
    midway(p)
    p.agent("verify:import", status="done", start_ago=2 * HOUR, quiet_s=1 * HOUR)
    f = p.engine().build_frame(120)
    plain, _ = testing.render_text(other_panel(f, 120), 120)
    row = next(line for line in plain.splitlines() if "Nightly data import" in line and "◉" in line)
    assert "◉─●" in row and "import" in row and "~2h00 · 16:13" in row and "67%" in row
    assert "next to finish: Docs site refresh" in plain


CRIT = {
    "safe": [],
    "failed": None,
    "one": ["git push (1m)"],
    "many": ["ssh → hostname (12m)", "git push (1m)", "npm install (3m)", "rsync (remote) (40s)", "docker pull (2m)"],
}


@pytest.mark.parametrize("state", sorted(CRIT))
@pytest.mark.parametrize("width", (80, 150))
def test_bottom_line(update_golden, state, width):
    plain, styled = testing.render_text(bottom_line(width, CRIT[state], "▲3 ▼12"), width)
    check(f"bottom/{state}-{width}.txt", plain, update_golden)
    check(f"bottom/{state}-{width}.ansi", styled, update_golden)


# The three states with the internet check on: (what ps found, what the check shows).
CONN = {
    "online-safe": ([], Conn(ONLINE)),
    "online-one": (["git push (1m)"], Conn(ONLINE)),
    "offline-safe": ([], Conn(OFFLINE, CAPTIVE)),
    "offline-one": (["git push (1m)"], Conn(OFFLINE, NO_NETWORK)),
    "offline-many": (CRIT["many"], Conn(OFFLINE, NO_ANSWER)),
    "offline-failed": (None, Conn(OFFLINE, NO_ANSWER)),
    "unknown-safe": ([], Conn(UNKNOWN, "last checked 40s ago")),
    "unknown-one": (["git push (1m)"], Conn(UNKNOWN, "not checked yet")),
}


@pytest.mark.parametrize("state", sorted(CONN))
@pytest.mark.parametrize("width", (80, 100, 150))  # 100: the Key leaves room for the flag, not its note
def test_bottom_line_connection(update_golden, state, width):
    crit, conn = CONN[state]
    plain, styled = testing.render_text(bottom_line(width, crit, "▲3 ▼12", conn), width)
    check(f"bottom/conn-{state}-{width}.txt", plain, update_golden)
    check(f"bottom/conn-{state}-{width}.ansi", styled, update_golden)


def test_plan_errors_keep_the_last_good_plan(tmp_path, update_golden):
    p = Project(tmp_path)
    midway(p)
    engine = p.engine()
    testing.render_engine(engine, 120)
    plan = p.dot / "status_plan.json"
    plan.write_text(plan.read_text()[:-5])  # a half-written save
    import os

    os.utime(plan, (NOW, NOW))
    plain, styled = testing.render_engine(engine, 120)
    check("plan-errors/broken-120.txt", plain, update_golden)
    assert "status_plan.json not loaded (JSONDecodeError" in plain
    assert "Release 1.4.0" in plain  # the last good plan stays
    plan.unlink()
    plain, _ = testing.render_engine(engine, 120)
    assert "status_plan.json is missing" in plain


@pytest.mark.parametrize("state", ["cold", "last-good"])
def test_notes_that_do_not_load(tmp_path, update_golden, state):
    """A typo in the notes never reads as a green "nothing is waiting on you": the last good notes
    stay (or a '?' when there are none), the panel turns amber, the title says why."""
    import os

    p = Project(tmp_path)
    midway(p)
    notes = p.dot / "status_notes.json"
    engine = p.engine()
    if state == "cold":
        notes.write_text('{"waiting_on_owner": [],}')
    else:
        testing.render_engine(engine, 120)
        notes.write_text(notes.read_text()[:-2] + ",}")
    os.utime(notes, (NOW - 60, NOW - 60))
    engine = engine if state == "last-good" else p.engine()
    plain, styled = testing.render_engine(engine, 120)
    top = "\n".join(plain.splitlines()[:8]) + "\n"
    check(f"notes-errors/{state}-120.txt", top, update_golden)
    assert "status_notes.json not loaded (JSONDecodeError" in plain and "nothing is waiting on you" not in plain
    assert ("Pricing: pick one" in plain) == (state == "last-good")


def test_a_failing_panel_shows_its_error_in_its_own_place(tmp_path, update_golden, monkeypatch):
    p = Project(tmp_path)
    midway(p)
    engine = p.engine()
    import lsw_mission_control.engine as eng

    def boom(f, width):
        raise KeyError("items")

    monkeypatch.setattr(eng, "release_panel", boom)
    plain, styled = testing.render_engine(engine, 120)
    # the engine line it names moves with every edit to engine.py: the golden keeps its place only
    plain, styled = (re.sub(r"(engine\.py line )(\d+)", lambda m: m[1] + "N" * len(m[2]), x) for x in (plain, styled))
    check("frame-error/120.txt", plain, update_golden)
    check("frame-error/120.ansi", styled, update_golden)
    assert "Mission control error" in plain and "KeyError: 'items'" in plain
    assert "Waiting on you" in plain and "Other work in progress" in plain and "Model usage" in plain  # the rest drew
    assert not engine.last_error and "release failed: Traceback" in engine.errors_for_once()


def test_a_frame_that_cannot_be_built_shows_one_error_panel(tmp_path, monkeypatch):
    p = Project(tmp_path)
    midway(p)
    engine = p.engine()

    def boom(width):
        raise KeyError("items")

    monkeypatch.setattr(engine, "build_frame", boom)
    plain, _ = testing.render_engine(engine, 120)
    assert "Mission control error" in plain and "KeyError: 'items'" in plain and "Release 1.4.0" not in plain
    assert "safe to switch networks" in plain  # the network row is still checked
    assert engine.last_error and engine.last_error in engine.errors_for_once()

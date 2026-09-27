from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from lsw_mission_control import theme
from lsw_mission_control.config import GitHubCfg, ReleaseCfg
from lsw_mission_control.sources.git import GitSource
from lsw_mission_control.sources.github import GitHubSource
from lsw_mission_control.sources.testlogs import suite_colour, test_logs
from lsw_mission_control.sources.tokens import TokenCounter, model_family
from lsw_mission_control.sources.usage_probe import UsageProbe, probe_usage
from lsw_mission_control.store import Store

from conftest import NOW
from scenarios import HOUR, MIN, ts

C = theme.C


def log(dirpath: Path, name: str, text: str, age: float) -> None:
    p = dirpath / "s1" / "scratchpad" / f"{name}.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    os.utime(p, (NOW - age, NOW - age))


def test_test_logs(tmp_path):
    log(tmp_path, "a", "x\n12 passed, 1 skipped in 3s ==\n", 60)
    log(tmp_path, "b", "[ 10%]\n[ 42%]\n", 30)
    log(tmp_path, "c", "done EXIT=0\n", 90)
    log(tmp_path, "d", "no summary here\n", 20)
    log(tmp_path, "e", "3 passed\n", 3 * HOUR)
    log(tmp_path, "f", "exit=-9\n", 10)
    rows = test_logs([f"{tmp_path}/*/scratchpad/*.log"], NOW)
    assert rows == [("f", "exit=-9", NOW - 10), ("b", "42%", NOW - 30), ("a", "12 passed, 1 skipped in 3s", NOW - 60),
                    ("c", "EXIT=0", NOW - 90)]
    assert len(test_logs([f"{tmp_path}/*/scratchpad/*.log"], NOW, limit=2)) == 2


@pytest.mark.parametrize("label, colour", [
    ("12 passed", "GREEN"), ("1 failed, 12 passed", "RED_SOFT"), ("2 errors in 1s", "RED_SOFT"), ("exit=0", "GREEN"),
    ("exit=1", "RED_SOFT"), ("exit=-9", "RED_SOFT"), ("0 failed", "MUTED"), ("42%", "MUTED"),
])
def test_suite_colour(label, colour):
    assert suite_colour(label) == getattr(C, colour)


def test_git_source_with_an_injected_run():
    answers = {"log": "abc123 Do a thing\n", "rev-list": "3\n", "branch": "main\nfeature\nfix\n",
               "worktree": "/a\n/b\n", "status": " M a\n?? b\n"}
    store = Store()
    GitSource("/repo", "main", "origin", store, run=lambda *cmd, **kw: answers[cmd[3]]).poll_once()
    assert store.get("git") == {"head": "abc123 Do a thing", "unpushed": "3", "branches": 2, "worktrees": 1, "dirty": 2}
    GitSource("/repo", "main", "origin", store, run=lambda *cmd, **kw: "").poll_once()
    assert store.get("git")["unpushed"] == "?"


def fake_gh(answers: dict):
    calls = []

    def run(*cmd, timeout=30, input_=None):
        calls.append(cmd)
        for key, value in answers.items():
            if key in " ".join(cmd):
                return value if isinstance(value, str) else json.dumps(value)
        return ""
    return run, calls


def test_github_source():
    sha = "a" * 40
    runs = [{"workflowName": "CI", "event": "push", "headBranch": "main", "headSha": sha, "status": "completed",
             "conclusion": "success", "createdAt": ts(NOW - HOUR), "updatedAt": ts(NOW - 40 * MIN)},
            {"workflowName": "CI", "event": "pull_request", "headBranch": "x", "headSha": "b" * 40},
            {"workflowName": "Release", "event": "push", "headBranch": "v1.4.0", "headSha": sha, "status": "in_progress",
             "createdAt": ts(NOW - 5 * MIN)}]
    run, calls = fake_gh({
        "commits/main": sha + "\n",
        "matching-refs/tags/v1.4.0": [{"ref": "refs/tags/v1.4.0"}],
        "run list -R example/demo --limit 30": runs,
        "run list -R example/demo --limit 60": [
            {"workflowName": "CI", "event": "push", "conclusion": "success", "createdAt": ts(NOW - 30 * MIN),
             "updatedAt": ts(NOW - 10 * MIN)},
            {"workflowName": "CI", "event": "push", "conclusion": "success", "createdAt": ts(NOW - 90 * MIN),
             "updatedAt": ts(NOW - 60 * MIN)},
            {"workflowName": "CI", "event": "push", "conclusion": "failure", "createdAt": ts(NOW - 9 * HOUR),
             "updatedAt": ts(NOW - 1 * HOUR)}],
        "pr list": [{"number": 1, "isDraft": True, "author": {"login": "dependabot[bot]"}}, {"number": 2, "author": {"login": "me"}}],
        "release list": [{"tagName": "v1.4.0-rc1", "isPrerelease": True}, {"tagName": "v1.3.0", "isLatest": True}],
    })
    store = Store()
    src = GitHubSource(GitHubCfg(repo="example/demo"), ReleaseCfg(), "main", store, lambda: "1.4.0", run=run)
    src.poll_once()
    g = store.get("release_gh")
    assert g["main_sha"] == sha and g["tag"] is True and g["ci"]["headSha"] == sha and g["last_ci"] is g["ci"]
    assert g["rel"]["headBranch"] == "v1.4.0" and g["prs"] == {"open": 2, "drafts": 1, "bots": 1}
    assert g["last_release"]["tagName"] == "v1.3.0"
    assert store.get("gh_timing") == (30.0, 15.0)  # the median CI run; no release runs yet: the default
    # a failed call keeps the last answer
    src.run = lambda *cmd, **kw: ""
    src.poll_github()
    assert store.get("release_gh")["tag"] is True and store.get("release_gh")["rel"] is not None


def test_a_new_release_drops_the_old_tag_and_release_run():
    """with the release changed and the calls failing, the old release's tag and run stood."""
    release = ["1.4.0"]
    run, _calls = fake_gh({
        "commits/main": "a" * 40 + "\n",
        "matching-refs/tags/v1.4.0": [{"ref": "refs/tags/v1.4.0"}],
        "run list -R example/demo --limit 30": [{"workflowName": "Release", "headBranch": "v1.4.0", "status": "completed",
                                                 "conclusion": "success", "createdAt": ts(NOW - HOUR)}],
    })
    store = Store()
    src = GitHubSource(GitHubCfg(repo="example/demo"), ReleaseCfg(), "main", store, lambda: release[0], run=run)
    src.poll_github()
    assert store.get("release_gh")["tag"] is True and store.get("release_gh")["rel"] is not None
    release[0] = "1.5.0"
    src.run = lambda *cmd, **kw: ""  # every call fails
    src.poll_github()
    g = store.get("release_gh")
    assert g["for_release"] == "1.5.0" and "tag" not in g and "rel" not in g
    assert g["main_sha"] == "a" * 40  # what is not the release's keeps its last answer


def test_github_counts_as_polled_when_it_cannot_answer():
    store = Store()
    src = GitHubSource(GitHubCfg(repo="example/demo"), ReleaseCfg(), "main", store, lambda: "1.4.0",
                       run=lambda *cmd, **kw: "")  # gh offline
    src.poll_once()
    assert store.get("gh_polled") is True and store.get("gh_timing") is None

    def broken(*cmd, **kw):
        raise RuntimeError("gh exploded")
    store2 = Store()
    src2 = GitHubSource(GitHubCfg(repo="example/demo"), ReleaseCfg(), "main", store2, lambda: "1.4.0", run=broken)
    with pytest.raises(RuntimeError):
        src2.poll_once()  # (the source loop catches it and tries again next round)
    assert store2.get("gh_polled") is True


def test_model_family():
    assert model_family("claude-opus-4-1") == "Opus" and model_family("<synthetic>") == "other" and model_family(None) == "other"


def entry(mid, t, model="claude-sonnet-4", out=10, inp=1, cw=2, cr=3):
    return json.dumps({"type": "assistant", "timestamp": ts(t), "message": {
        "id": mid, "model": model, "usage": {"input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cw,
                                             "cache_read_input_tokens": cr}}}) + "\n"


def test_token_counter(tmp_path):
    root = tmp_path / "projects"
    f = root / "p1" / "s.jsonl"
    f.parent.mkdir(parents=True)
    # the same reply logged twice (once per content block) counts once; an old one only in 7 days
    f.write_text(entry("m1", NOW - 60) + entry("m1", NOW - 60) + entry("m2", NOW - 6 * HOUR, model="claude-opus-4")
                 + entry("m3", NOW - 3 * 86400) + "{partial")
    os.utime(f, (NOW, NOW))
    store = Store()
    cache = tmp_path / "usage" / "tokens.json"
    tc = TokenCounter(root, cache, store)
    tc.poll_once()
    tok = store.get("tokens")
    assert tok["5h"] == [1, 10, 2, 3] and tok["today"] == [2, 20, 4, 6] and tok["7d"] == [3, 30, 6, 9]
    assert store.get("tokens_by_model") == {"Sonnet": {"5h": 10, "today": 10, "7d": 20}, "Opus": {"5h": 0, "today": 10, "7d": 10}}
    saved = json.loads(cache.read_text())["files"][str(f)]
    assert saved["off"] == len(f.read_bytes()) - len("{partial")  # a partial last line waits for its newline
    # incremental: a new counter reads the cache and only the new bytes
    with open(f, "a") as fh:
        fh.write("}\n" + entry("m4", NOW - 30, out=100))
    os.utime(f, (NOW, NOW))
    store2 = Store()
    TokenCounter(root, cache, store2).poll_once()
    assert store2.get("tokens")["5h"][1] == 110
    # a shrunk file (rewritten) is read again from the start
    f.write_text(entry("m9", NOW - 60, out=5))
    os.utime(f, (NOW, NOW))
    store3 = Store()
    TokenCounter(root, cache, store3).poll_once()
    assert store3.get("tokens")["5h"][1] == 5
    # an entry without per-model buckets (an older cache) is re-read from the start, once
    data = json.loads(cache.read_text())
    del data["files"][str(f)]["m"]
    cache.write_text(json.dumps(data))
    store4 = Store()
    TokenCounter(root, cache, store4).poll_once()
    assert store4.get("tokens_by_model")["Sonnet"]["5h"] == 5


RATE_EVENT = {"type": "rate_limit_event", "rate_limit_info": {
    "status": "allowed_warning", "isUsingOverage": True,
    "unifiedWindows": {"five_hour": {"utilization": 0.815, "resetsAt": 1790010000},
                       "seven_day": {"utilization": 0.2, "resetsAt": 1790500000}}}}
FAKE_CLAUDE = f"""#!/bin/sh
echo '{{"type":"system"}}'
echo '{json.dumps(RATE_EVENT)}'
exec sleep 30
"""


def test_probe_with_a_fake_claude(tmp_path, monkeypatch):
    """A fake `claude` first on PATH: the probe stops at the rate_limit_event (no usage is spent)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    usage = tmp_path / "usage"
    rec = probe_usage(usage)
    assert rec["rate_limits"] == {"five_hour": {"used_percentage": 81.5, "resets_at": 1790010000},
                                  "seven_day": {"used_percentage": 20.0, "resets_at": 1790500000}}
    assert rec["source"] == "probe" and rec["status"] == "allowed_warning" and rec["overage"] is True
    assert (usage / "probe-cwd").is_dir() and not list((usage / "probe-cwd").iterdir())
    # the loop: a stale file is probed and written; a fresh one is skipped
    probe = UsageProbe(usage)
    probe.poll_once()
    assert json.loads((usage / "usage.json").read_text())["source"] == "probe"
    os.utime(usage / "usage.json", (NOW - 60, NOW - 60))
    (usage / "usage.json").write_text("{}")
    os.utime(usage / "usage.json", (NOW - 60, NOW - 60))
    probe.poll_once()
    assert (usage / "usage.json").read_text() == "{}" and probe.next_sleep() == 20 * 60 - 60


def test_probe_without_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert probe_usage(tmp_path / "usage") is None


def test_a_silent_claude_is_killed_at_the_timeout(tmp_path, monkeypatch):
    """A CLI that prints nothing must not hold the probe past its timeout (the read waits for a line)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text("#!/bin/sh\nexec sleep 30\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    t0 = time.monotonic()
    assert probe_usage(tmp_path / "usage", timeout_s=1) is None
    assert time.monotonic() - t0 < 10


def test_a_probe_that_comes_back_empty_is_retried_soon(tmp_path, monkeypatch):
    """3 min, 6, 12, then the interval; a success or a fresh file resets the back-off."""
    from lsw_mission_control.sources import usage_probe as up
    answers = []
    monkeypatch.setattr(up, "probe_usage", lambda *_a, **_k: answers.pop(0) if answers else None)
    usage = tmp_path / "usage"
    usage.mkdir()
    probe = UsageProbe(usage)
    sleeps = []
    for _ in range(5):
        probe.poll_once()
        sleeps.append(probe.next_sleep())
    assert sleeps == [180, 360, 720, 1200, 1200]
    answers.append({"at": NOW, "rate_limits": {}, "source": "probe"})
    probe.poll_once()
    assert probe.next_sleep() == 20 * 60 and (usage / "usage.json").exists()
    os.utime(usage / "usage.json", (NOW - 60, NOW - 60))
    probe.poll_once()  # fresh: skipped, and the back-off forgotten
    os.utime(usage / "usage.json", (NOW - 3600, NOW - 3600))
    probe.poll_once()  # stale, and the probe comes back empty again: the first retry, not the fifth
    assert probe.next_sleep() == 180

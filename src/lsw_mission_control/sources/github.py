"""GitHub, through the gh CLI: the release's signals (CI on the remote main's HEAD, the release
tag, its release run), open pull requests, the last release, and how long runs really take."""

from __future__ import annotations

import json
import re
from typing import Callable

from lsw_mission_control.config import GitHubCfg, ReleaseCfg
from lsw_mission_control.sources import Source
from lsw_mission_control.store import Store
from lsw_mission_control.util import iso, now
from lsw_mission_control.util import run as _run


class GitHubSource(Source):
    name = "github"

    def __init__(self, gh: GitHubCfg, rel: ReleaseCfg, main_branch: str, store: Store,
                 release_fn: Callable[[], str], run: Callable[..., str] = _run) -> None:
        self.gh, self.rel, self.main, self.store = gh, rel, main_branch, store
        self.release_fn = release_fn
        self.run = run
        self.interval_s = gh.poll_s
        self._timed = 0.0

    def poll_once(self) -> None:
        try:
            self.poll_github()
            if now() - self._timed > self.gh.timing_poll_s:
                self.poll_timing()
                self._timed = now()
        finally:
            # GitHub has answered once, or failed to (gh offline, no successful CI run to time):
            # --once waits for this, never for an answer that may not come.
            self.store.set("gh_polled", True)

    def poll_github(self) -> None:
        """A failed call keeps the last answer rather than reading as 'no tag' or 'no run'."""
        repo, run, release = self.gh.repo, self.run, self.release_fn()
        tag = f"{self.rel.tag_prefix}{release}"
        g = dict(self.store.get("release_gh") or {})
        if g.get("for_release") != release:
            # A new release: the last one's tag and release run say nothing about this one, and a
            # failed call below must not leave them standing in for its answer.
            g.pop("tag", None)
            g.pop("rel", None)
            g["for_release"] = release
        sha = run("gh", "api", f"repos/{repo}/commits/{self.main}", "--jq", ".sha", timeout=30).strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            g["main_sha"] = sha
        try:
            refs = json.loads(run("gh", "api", f"repos/{repo}/git/matching-refs/tags/{tag}", timeout=30))
            if isinstance(refs, list):
                g["tag"] = any(r.get("ref") == f"refs/tags/{tag}" for r in refs if isinstance(r, dict))
        except json.JSONDecodeError:
            pass
        try:
            runs = json.loads(run("gh", "run", "list", "-R", repo, "--limit", "30", "--json",
                                  "workflowName,status,conclusion,headSha,headBranch,event,createdAt,updatedAt", timeout=30))
        except json.JSONDecodeError:
            runs = None
        if isinstance(runs, list):
            ci_runs = [r for r in runs if r.get("workflowName") == self.gh.ci_workflow and r.get("event") == "push"
                       and r.get("headBranch") == self.main]
            g["last_ci"] = ci_runs[0] if ci_runs else None
            g["ci"] = next((r for r in ci_runs if r.get("headSha") == g.get("main_sha")), None)
            g["rel"] = next((r for r in runs if r.get("workflowName") == self.gh.release_workflow
                             and r.get("headBranch") == tag), None)
            g["at"] = now()
        try:
            prs = json.loads(run("gh", "pr", "list", "-R", repo, "--state", "open", "--limit", "100",
                                 "--json", "number,isDraft,author", timeout=30))
            if isinstance(prs, list):
                bots = sum(1 for pr in prs if "bot" in str(((pr.get("author") or {}).get("login")) or "").lower())
                g["prs"] = {"open": len(prs), "drafts": sum(1 for pr in prs if pr.get("isDraft")), "bots": bots}
        except json.JSONDecodeError:
            pass
        try:
            rels = json.loads(run("gh", "release", "list", "-R", repo, "--limit", "5", "--json",
                                  "tagName,publishedAt,isLatest,isDraft,isPrerelease", timeout=30))
            if isinstance(rels, list):
                done = [r for r in rels if isinstance(r, dict) and not r.get("isDraft") and not r.get("isPrerelease")]
                g["last_release"] = next((r for r in done if r.get("isLatest")), done[0] if done else None)
        except json.JSONDecodeError:
            pass
        self.store.set("release_gh", g)

    def poll_timing(self) -> None:
        """How long GitHub really takes: the median of recent successful pushes of CI and the release run."""
        ci_name, rel_name = self.gh.ci_workflow, self.gh.release_workflow
        hist = self.run("gh", "run", "list", "-R", self.gh.repo, "--limit", "60", "--json",
                        "workflowName,event,conclusion,createdAt,updatedAt", timeout=30)
        try:
            spans: dict = {ci_name: [], rel_name: []}
            for r in json.loads(hist) if hist.strip() else []:
                if r.get("conclusion") == "success" and r.get("event") == "push" and r.get("workflowName") in spans:
                    spans[r["workflowName"]].append((iso(r["updatedAt"]) - iso(r["createdAt"])) / 60)
            if spans[ci_name]:
                ci = sorted(spans[ci_name])[len(spans[ci_name]) // 2]
                rel = (sorted(spans[rel_name])[len(spans[rel_name]) // 2] if spans[rel_name]
                       else float(self.rel.release_run_minutes))
                self.store.set("gh_timing", (ci, rel))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

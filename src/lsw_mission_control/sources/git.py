"""The local repository: HEAD, unpushed commits, branches, worktrees, uncommitted files."""

from __future__ import annotations

from typing import Callable

from lsw_mission_control.sources import Source
from lsw_mission_control.store import Store
from lsw_mission_control.util import run as _run


class GitSource(Source):
    name = "git"

    def __init__(self, root, main_branch: str, remote: str, store: Store, interval_s: float = 20,
                 run: Callable[..., str] = _run) -> None:
        self.root, self.main, self.remote, self.store = str(root), main_branch, remote, store
        self.interval_s = interval_s
        self.run = run

    def poll_once(self) -> None:
        run, root, main = self.run, self.root, self.main
        g = {}
        g["head"] = run("git", "-C", root, "log", "-1", "--format=%h %s").strip()
        g["unpushed"] = run("git", "-C", root, "rev-list", "--count", f"{self.remote}/{main}..{main}").strip() or "?"
        g["branches"] = len([b for b in run("git", "-C", root, "branch", "--format=%(refname:short)").split() if b != main])
        g["worktrees"] = max(0, len(run("git", "-C", root, "worktree", "list").splitlines()) - 1)
        g["dirty"] = len(run("git", "-C", root, "status", "--porcelain").splitlines())
        self.store.set("git", g)

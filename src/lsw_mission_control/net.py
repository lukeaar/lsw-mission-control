"""The network indicator: whether switching networks right now would kill anything.

Processes a network drop would kill or corrupt: remote shells and copies, git/gh traffic,
downloads and installs, registry pushes and pulls. Claude's own agents are NOT listed: their
API calls retry through a short drop. A dashboard's own polling (its gh calls, and the ssh
hosts its plugins declare) is excluded; any other network work a dashboard starts (a first-run
pip install) is listed like anyone else's.

This must ALWAYS be right. Add every new kind of network work here first (or, for a tool only
one project uses, to that project's [network] config), add a real ps line to NET_CASES (or
[[network.cases]]), and run `--check-net`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from rich.text import Text

from lsw_mission_control.theme import C
from lsw_mission_control.util import human
from lsw_mission_control.util import run as _run

NET_TOOLS = frozenset({"ssh", "scp", "sftp", "curl", "wget", "gh", "pip-audit", "pip-compile", "pip-sync"})
NET_SUBCOMMANDS: dict[str, frozenset] = {
    "git": frozenset({"push", "fetch", "pull", "clone", "ls-remote", "submodule"}),
    "docker": frozenset({"push", "pull", "login", "build", "buildx"}),  # a build pulls base images and packages
    "npm": frozenset({"install", "ci", "audit", "i", "update"}),
    "pip": frozenset({"install", "download"}),
    "pip3": frozenset({"install", "download"}),
    "brew": frozenset({"install", "upgrade", "update", "fetch"}),
}
GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
COMPOSE_VERBS = frozenset({"pull", "build", "up", "down", "run", "exec", "ps", "logs", "config", "start", "stop",
                           "restart", "rm", "create", "push", "images", "top", "cp", "kill", "pause", "unpause"})


@dataclass(frozen=True)
class NetRules:
    tools: frozenset = NET_TOOLS
    subcommands: Mapping[str, frozenset] = field(default_factory=lambda: dict(NET_SUBCOMMANDS))

    @classmethod
    def with_extras(cls, tools: Iterable[str] = (), subcommands: Mapping[str, Iterable[str]] | None = None) -> NetRules:
        subs = {k: frozenset(v) for k, v in NET_SUBCOMMANDS.items()}
        for tool, words in (subcommands or {}).items():
            subs[tool] = subs.get(tool, frozenset()) | frozenset(words)
        return cls(NET_TOOLS | frozenset(tools), subs)


DEFAULT = NetRules()


def net_label(command: str, rules: NetRules = DEFAULT) -> str | None:
    """The short label for one process command line, or None if a drop would not hurt it."""
    argv = command.split()
    if not argv:
        return None
    tool = os.path.basename(argv[0])
    args = argv[1:]
    # macOS shows every Python as ".../Python.app/Contents/MacOS/Python": match any case, then
    # name the tool from what it runs (`-m pip`, `-m playwright`, or a script such as a venv's pip).
    low = tool.lower()
    if low in ("python", "python3") or low.startswith("python3."):
        while args and args[0].startswith("-") and args[0] != "-m":
            args = args[1:]
        if args[:1] == ["-m"] and len(args) > 1:
            tool, args = args[1], args[2:]
        elif args:
            tool, args = os.path.basename(args[0]), args[1:]
        else:
            return None
    if tool in ("npx", "node") or (tool == "npm" and args[:1] == ["exec"]):
        if "install" in args and any("playwright" in a for a in args):
            return "playwright install"
        if tool == "npx" and "install" in args:
            return "npx install"
        if tool != "npm":
            return None
    if tool == "playwright" and args[:1] == ["install"]:
        return "playwright install"
    if tool in rules.tools:
        if tool == "ssh":
            hosts = [a for a in args if not a.startswith("-") and "=" not in a]
            return f"ssh → {hosts[0]}" if hosts else "ssh"
        return tool
    if tool == "rsync":
        return "rsync (remote)" if any(":" in a and not a.startswith("/") for a in args) else None
    if tool in rules.subcommands:
        words, skip = [], False
        for a in args:
            if skip:
                skip = False
                continue
            if tool == "git" and a in GIT_VALUE_OPTS:
                skip = True
                continue
            if not a.startswith("-"):
                words.append(a)
        if tool == "docker" and words[:1] == ["compose"]:
            # The verb is the first compose command word; -f/-p values come before it.
            verb = next((w for w in words[1:] if w in COMPOSE_VERBS), None)
            return f"docker compose {verb}" if verb in ("pull", "build") else None
        if words and words[0] in rules.subcommands[tool]:
            return f"{tool} {words[0]}"
    return None


# Real process lines (as ps prints them on macOS) and the label each must get. Host, repository
# and image names are placeholders.
NET_CASES = [
    ("/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/"
     "Contents/MacOS/Python /Users/x/.venvs/ui-review/bin/pip install playwright", "pip install"),
    ("/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/"
     "Contents/MacOS/Python -m pip install rich", "pip install"),
    ("/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m pip download x", "pip download"),
    ("/opt/homebrew/opt/python@3.14/bin/python3.14 /Users/x/.venvs/a/bin/pip-audit --no-deps -r r.txt", "pip-audit"),
    ("/usr/bin/python3 /Users/x/.venvs/a/bin/pip-compile pyproject.toml", "pip-compile"),
    (".../Python.app/Contents/MacOS/Python -m playwright install chromium", "playwright install"),
    ("node /Users/x/proj/node_modules/.bin/playwright install", "playwright install"),
    ("npm exec playwright install", "playwright install"),
    ("npx playwright install", "playwright install"),
    ("git -C /Users/x/repo push origin main", "git push"),
    ("git -c http.sslVerify=true fetch --all", "git fetch"),
    ("git status", None),
    ("ssh -o ControlMaster=no -o ControlPath=none hostname bash -s", "ssh → hostname"),
    ("rsync -a /a/ /b/", None),
    ("rsync -a /a/ host:/b/", "rsync (remote)"),
    ("docker compose pull", "docker compose pull"),
    ("docker build -t app-backend:rc231 -f backend/Dockerfile backend", "docker build"),
    ("docker buildx build --load -t app-backend:rc231 backend", "docker buildx"),
    ("docker compose -f docker-compose.release.yml build backend", "docker compose build"),
    ("docker images", None),
    ("npm install", "npm install"),
    ("npm run test", None),
    (".../Python.app/Contents/MacOS/Python -m pytest -q", None),
    (".../Python.app/Contents/MacOS/Python /Users/x/project/.claude/status.py", None),
    ("gh run list", "gh"),
    # A dashboard's own GitHub polling runs these (as its children, so they are excluded there);
    # anyone else running them is on the network.
    ("gh api repos/example/project/commits/main --jq .sha", "gh"),
    ("gh api repos/example/project/git/matching-refs/tags/v2.3.1", "gh"),
    ("curl -sSL https://example.org", "curl"),
]


# The indicator as drawn: (what ps found — None when the check itself failed —, the room, and
# what the flag must show: "safe", "unknown", or how many names are at risk in total).
FLAG_CASES = [
    ([], 60, "safe"),
    (None, 60, "unknown"),
    (["git push (1m)"], 60, 1),
    (["ssh → hostname (12m)", "git push (1m)", "npm install (3m)", "rsync (remote) (40s)", "docker pull (2m)"], 40, 5),
    (["ssh → a-very-long-host-name.example.internal.network (1h02)", "git push (1m)", "curl (9s)"], 45, 3),
    (["pip install (2m)"] * 30, 30, 30),
    (["git push (1m)"], 0, 1),
]

NET_CHIP = " NETWORK-CRITICAL "
# Every dashboard's own polling makes these; a plugin adds its own (its ssh hosts).
OWN_LABELS = frozenset({"gh"})
LEGACY_MARKER = ".claude/status.py"


def _python_module(argv: list[str]) -> str | None:
    """The module a Python command line runs with -m (after the interpreter's own options)."""
    if not argv:
        return None
    low = os.path.basename(argv[0]).lower()
    if not (low in ("python", "python3") or low.startswith("python3.")):
        return None
    args = argv[1:]
    while args and args[0].startswith("-") and args[0] != "-m":
        args = args[1:]
    return args[1] if args[:1] == ["-m"] and len(args) > 1 else None


def is_dashboard(cmd: str, markers: Iterable[str] = ()) -> bool:
    """A mission-control process: an old single-file dashboard or a launcher (its script path
    ends in .claude/status.py), the engine (`python -m lsw_mission_control`), the `lsw-mc`
    console script, or anything matching a config marker."""
    if LEGACY_MARKER in cmd or any(m and m in cmd for m in markers):
        return True
    argv = cmd.split()
    if _python_module(argv) == "lsw_mission_control":
        return True
    if argv and os.path.basename(argv[0]) == "lsw-mc":
        return True
    # a console script runs as `<python> <venv>/bin/lsw-mc ...`
    return len(argv) > 1 and _python_module(argv) is None and os.path.basename(argv[0]).lower().startswith("python") \
        and os.path.basename(argv[1]) == "lsw-mc"


def etime_seconds(etime: str) -> int:
    """ps etime, [[dd-]hh:]mm:ss, in seconds."""
    days, _, rest = etime.strip().rpartition("-")
    total = 0
    for part in rest.split(":"):
        total = total * 60 + int(part or 0)
    return total + (int(days) * 86400 if days else 0)


def network_critical(rules: NetRules = DEFAULT, markers: Iterable[str] = (), own_labels: Iterable[str] = OWN_LABELS,
                     ps_text: str | None = None, run=None, self_pid: int | None = None) -> list[str] | None:
    """What is running right now that a network drop would kill, as short labels with ages; None
    when the check itself failed, so the flag never says safe on no evidence: ps gave nothing (it
    could not run, or timed out: run() answers ""), or, given `self_pid` (the dashboard's own pid),
    a table without that process in it (cut short)."""
    real = ps_text is None
    out = (run or _run)("ps", "-axo", "pid=,ppid=,etime=,command=", timeout=5) if real else ps_text
    markers = tuple(markers)
    own = frozenset(own_labels)
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
            try:
                age = etime_seconds(parts[2])
            except ValueError:
                age = 10**6  # an unreadable age: count the process rather than skip it
            procs.append((int(parts[0]), int(parts[1]), age, parts[3]))
    if real and not procs:
        return None
    if self_pid is not None and not any(pid == self_pid for pid, _pp, _age, _cmd in procs):
        return None
    # Any mission-control window's own polling (its quick ssh/gh calls) is not a risk.
    dashboards = {pid for pid, _pp, _age, cmd in procs if is_dashboard(cmd, markers)}
    found = []
    for pid, ppid, age, command in procs:
        if pid in dashboards or age < 8:
            continue
        label = net_label(command, rules)
        if not label or (ppid in dashboards and label in own):
            continue
        found.append(f"{label} ({human(age)})")
    return found


def fit_list(names: list[str], room: int) -> tuple[int, str]:
    """(how many shown, the text) of the most names that fit `room`, in order, skipping any
    too long to fit and counting them in '+N more'."""
    if len(", ".join(names)) <= room:
        return len(names), ", ".join(names)
    reserve = len(f", +{len(names)} more")
    shown: list[str] = []
    for nm in names:
        if len(", ".join(shown + [nm])) + reserve <= room:
            shown.append(nm)
    more = len(names) - len(shown)
    return len(shown), ", ".join(shown + [f"+{more} more"])


def network_flag(crit: list[str] | None, room: int) -> Text:
    """Whether switching networks is safe now. When it is not, the chip always shows, and the
    list of what is at risk shortens ('+N more') to fit the room the Key leaves. `None` means
    the check itself failed: then it never says safe."""
    if crit is None:
        return Text("● network check failed", style=C.AMBER)
    if not crit:
        return Text("● safe to switch networks", style=C.GREEN)
    # The chip is a span, not the Text's base style: a base style would paint the list (and the
    # justify padding) red on red too.
    flag = Text()
    flag.append(NET_CHIP, style=f"bold {C.BG} on {C.RED}")
    bare = [re.sub(r" \([^()]*\)$", "", c) for c in crit]  # without their ages
    # What is at risk matters more than for how long: ages go before names do.
    room = max(0, room - len(NET_CHIP) - 2)
    with_ages, without = fit_list(crit, room), fit_list(bare, room)
    listed = with_ages[1] if with_ages[0] >= without[0] else without[1]
    if len(listed) <= room:
        flag.append("  " + listed, style=f"bold {C.RED_SOFT}")
    return flag


def flag_verdict(crit: list[str] | None, room: int) -> str | int:
    plain = network_flag(crit, room).plain
    if "safe to switch" in plain:
        return "safe"
    if NET_CHIP not in plain:
        return "unknown"
    listed = plain.split(NET_CHIP, 1)[1].strip()
    names = [x for x in listed.split(", ") if x] if listed else []
    more = re.fullmatch(r"\+(\d+) more", names[-1]) if names else None
    return len(names) - 1 + int(more.group(1)) if more else (len(names) or len(crit or []))


def check_net_cases(rules: NetRules = DEFAULT, extra_cases=(), out=print) -> int:
    """Prints 'N/N network cases correct' (a contract: the reload gate looks for that phrase) and
    'N/N indicator cases correct'; 1 on any mismatch."""
    cases = list(NET_CASES) + list(extra_cases)
    bad = [(c, want, net_label(c, rules)) for c, want in cases if net_label(c, rules) != want]
    for c, want, got in bad:
        out(f"MISMATCH want={want!r} got={got!r}: {c}")
    out(f"{len(cases) - len(bad)}/{len(cases)} network cases correct")
    flag_bad = [(c, room, want, flag_verdict(c, room)) for c, room, want in FLAG_CASES
                if flag_verdict(c, room) != want]
    for c, room, want, got in flag_bad:
        out(f"FLAG MISMATCH want={want!r} got={got!r}: room={room} {c}")
    out(f"{len(FLAG_CASES) - len(flag_bad)}/{len(FLAG_CASES)} indicator cases correct")
    return 1 if bad or flag_bad else 0

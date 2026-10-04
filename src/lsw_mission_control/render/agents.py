"""Agents at work (running, newest first) and the recent test suites."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rich.console import Group
from rich.table import Table
from rich.text import Text

from lsw_mission_control.agents import latest_attempts
from lsw_mission_control.render.widgets import bar, panel
from lsw_mission_control.sources.testlogs import suite_colour
from lsw_mission_control.theme import C
from lsw_mission_control.util import ago, human, now

if TYPE_CHECKING:
    from lsw_mission_control.engine import Frame


def agents_panel(f: Frame, width: int, logs: list[tuple[str, str, float]]):
    ac = f.cfg.agents
    silent_s, idle_amber, idle_red, max_rows = ac.silent_stopped_min * 60, ac.idle_amber_min * 60, ac.idle_red_min * 60, ac.rows
    agents = f.agents
    t_now = now()
    # An agent silent this long belongs to a run that was cut off (session limit, restart).
    running = [a for a in agents if a["status"] == "running" and a["t1"] and t_now - a["t1"] < silent_s]
    # A paused item's workflow was stopped: its last agent is not at work, whatever its journal says
    # (a release item only while its hold lasts: one that has ended holds nothing).
    paused = set()
    for item in f.plan.other:
        if item.paused:
            for _name, spec, _mins in item.stages:
                paused.update(spec if isinstance(spec, list) else ([spec] if isinstance(spec, str) else []))
    for item in f.plan.items:
        if item.held(t_now) and item.key is not None:
            paused.update(f"{kind}:{item.key}" for kind in ("build", "review", "fix"))
            for _name, spec, _mins in f.plan.before(item.key):
                paused.update(spec if isinstance(spec, list) else ([spec] if isinstance(spec, str) else []))
    running = [a for a in running if a["label"] not in paused and f"{a['run']}/{a['label']}" not in paused]
    # A retried agent, or a resumed run, starts a fresh attempt under the same label; the one it
    # replaced is not at work, even after its replacement has finished (so compare against every
    # agent that returned, not just running), and the latest is the latest in journal order. An
    # attempt that died replaces nothing (latest_by_label's rule): one still at work stays listed.
    live = {id(a) for a in running}
    newest = latest_attempts([a for a in agents if a["status"] == "done" or id(a) in live])
    running = [a for a in running if newest[(a["run"], a["label"])] is a]
    running.sort(key=lambda a: -(a["t1"] or 0))
    shown, hidden = running, []
    if len(running) > max_rows:
        # The quiet ones are the ones to see: every agent idle past red always gets a row.
        red = [a for a in running if t_now - a["t1"] >= idle_red][:max_rows - 1]
        rest = [a for a in running if t_now - a["t1"] < idle_red][:max_rows - 1 - len(red)]
        shown = sorted(rest + red, key=lambda a: -(a["t1"] or 0))
        hidden = [a for a in running if a not in shown]
    names = f.names
    t = Table(box=None, show_header=True, header_style=f"bold {C.FAINT}", pad_edge=False, expand=True)
    t.add_column("agent", no_wrap=True, style=C.ACCENT_SOFT)
    t.add_column("for", no_wrap=True, style=C.MUTED, max_width=36, overflow="ellipsis")
    t.add_column("running", no_wrap=True, style=C.MUTED, justify="right")
    t.add_column("active", no_wrap=True, justify="right")
    t.add_column("doing now", no_wrap=True, overflow="ellipsis", style=C.TEXT, ratio=1)
    for a in shown:
        idle = t_now - (a["t1"] or t_now)
        col = C.GREEN if idle < idle_amber else (C.AMBER if idle < idle_red else C.RED)
        what = names.get(f"{a['run']}/{a['label']}") or names.get(a["label"]) or a["phase"]
        # Outside text (labels, item names, a tool call's description) is never read as markup:
        # "[/x]" would fail the frame and "[b]" would silently vanish.
        t.add_row(Text(a["label"]), Text(what), human(t_now - (a["t0"] or t_now)), Text(ago(a["t1"]), style=col),
                  Text(a["action"] or ""))
    if hidden:
        quietest = max(t_now - a["t1"] for a in hidden)
        t.add_row(Text(f"+{len(hidden)} more · quietest {human(quietest)}", style=C.FAINT), "", "", "", "")
    if not running:
        t.add_row(Text("no agents running", style=C.FAINT), "", "", "", "")
    body = [t]
    if logs:
        lg = Table.grid(padding=(0, 2))
        whos = []
        for stem, _label, mtime in logs:
            # the agent whose command writes this log, and was at work when it was last written
            owners = [a for a in agents if stem in (a.get("logs") or ())
                      and (a["t0"] or 0) - 60 <= mtime <= (a["t1"] or t_now) + 600]
            owners.sort(key=lambda a: (a["status"] == "running", a["t1"] or 0), reverse=True)
            who = ""
            if owners:
                a = owners[0]
                who = names.get(f"{a['run']}/{a['label']}") or names.get(a["label"]) or a["label"]
            whos.append(who)
        for (stem, label, mtime), who in zip(logs, whos):
            if who and whos.count(who) > 1:
                who = f"{who} · {stem}"  # one item, several suites: say which suite each is
            pct = re.fullmatch(r"(\d+)%", label)
            if pct:
                result = bar(int(pct.group(1)) / 100, 10, C.MUTED) + Text(f" {pct.group(1)}%", style=C.MUTED)
            else:
                result = Text(label, style=suite_colour(label))
            lg.add_row(Text(who or stem, style=C.FAINT), result, Text(ago(mtime), style=C.FAINT))
        body += [Text(""), Text("test suites", style=f"bold {C.FAINT}"), lg]
    return panel(Group(*body), "Agents at work", f"{len(running)} running")

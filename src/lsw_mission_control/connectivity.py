"""Whether this computer is on the internet: the third state of the network indicator.

The background check (sources/connectivity.py) runs every second on its own thread, never on the
render path. Each tick it looks the route to the internet up in the kernel (a UDP connect: no
packet is sent). No route (Wi-Fi off, cable out) is "not connected" at once, with no request.
With a route, it asks Apple's captive-portal check (http://captive.apple.com/hotspot-detect.html,
the page macOS itself asks) every 5 s, every 2 s for the next three requests after the state or
the route changes, and at once when the route changes. Only the page's "Success" body counts:
a login page or a redirect (a captive portal) is not connected. A request that fails while the
last answer was "online" is asked again 2 s later before the indicator says not connected, so
one lost packet does not flash it; no route, and a real answer that is not Success, count at once.

The request is made inside the dashboard's own process, so `ps` never shows it and the indicator
never lists it as network work. A frame reads the last answer: one older than 15 s reads
"unknown", never a guess.

This module holds the pure parts (the record, the verdicts, the state machine and the cases
`--check-net` proves); it does no I/O and starts no thread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, NamedTuple

from lsw_mission_control.util import human

CHECK_URL = "http://captive.apple.com/hotspot-detect.html"
TICK_S = 1.0  # the route is looked up this often (no packet is sent)
EVERY_S = 5.0  # a request this often while nothing changes
FAST_S = 2.0  # ... and this often right after a change of state or of route
FAST_N = 3  # requests at the fast cadence after a change
TIMEOUT_S = 2.0  # a request with no answer by then is a failure
STALE_S = 15.0  # an answer older than this reads "unknown"

ONLINE, OFFLINE, UNKNOWN = "online", "offline", "unknown"

# Why the internet did not answer: short, as the indicator shows them.
NO_NETWORK = "no network"  # no route to the internet: no request is made
CAPTIVE = "captive portal"  # an answer that is not the Success page (a login page, a redirect)
NO_ANSWER = f"no answer in {human(TIMEOUT_S)}"
NO_DNS = "DNS failed"
UNREACHABLE = "unreachable"
BAD_ANSWER = "bad answer"
# Failures with no answer at all: while the last answer was "online", asked again before they show.
SOFT = frozenset({NO_ANSWER, NO_DNS, UNREACHABLE, BAD_ANSWER})

SUCCESS = re.compile(rb"<body>\s*success\s*</body>", re.I)


@dataclass(frozen=True)
class Online:
    """One answer of the background check, as the store holds it."""

    ok: bool  # the internet answered with the Success page
    why: str = ""  # when it did not: NO_NETWORK, CAPTIVE, NO_ANSWER, ...
    at: float = 0.0  # when


class Conn(NamedTuple):
    """What the indicator shows about the internet: ONLINE, OFFLINE (and why) or UNKNOWN (and why)."""

    state: str
    why: str = ""


def classify(status: int, body: bytes) -> tuple[bool, str]:
    """(on the internet, why not) from the check page's answer. Redirects are never followed:
    a redirect is a captive portal sending the browser to its login page."""
    if status == 200 and SUCCESS.search(body or b""):
        return True, ""
    if status in (200, 511) or 300 <= status < 400:  # 511: Network Authentication Required
        return False, CAPTIVE
    return False, f"HTTP {status}"


def connection(rec: Online | None, now: float, stale_s: float = STALE_S) -> Conn:
    """The indicator's view of the last answer at `now`: an answer older than `stale_s` is UNKNOWN."""
    if not isinstance(rec, Online):
        return Conn(UNKNOWN, "not checked yet")
    age = now - rec.at
    if age > stale_s:
        return Conn(UNKNOWN, f"last checked {human(age)} ago")
    return Conn(ONLINE) if rec.ok else Conn(OFFLINE, rec.why or UNREACHABLE)


_NO_ROUTE_YET = object()  # before the first tick: any route is a change


class Machine:
    """What the background check does on each tick, with no I/O or thread of its own: `route` is the
    kernel's route key (None: no route), `ask` makes one request and gives (ok, why), `clock` is the
    time. `tick` returns the answer to publish, or None when there is nothing new."""

    def __init__(self, every_s: float = EVERY_S, fast_s: float = FAST_S, fast_n: int = FAST_N) -> None:
        self.every_s, self.fast_s, self.fast_n = every_s, fast_s, fast_n
        self.rec: Online | None = None  # the last answer published
        self.route = _NO_ROUTE_YET  # the route at the last tick
        self.asked_at: float | None = None  # when the last request ended
        self.fast_left = 0  # requests still to make at the fast cadence
        self.held = False  # a failure while online, asked again before it shows

    def due(self, t: float, route_changed: bool) -> bool:
        if route_changed or self.asked_at is None:
            return True
        return t - self.asked_at >= (self.fast_s if self.fast_left or self.held else self.every_s)

    def tick(self, route, ask: Callable[[], tuple[bool, str]], clock: Callable[[], float]) -> Online | None:
        t = clock()
        route_changed = route != self.route
        self.route = route
        if route is None:  # no route to the internet: not connected, and nothing to ask
            return self._publish(Online(False, NO_NETWORK, t), route_changed)
        if not self.due(t, route_changed):
            return None
        ok, why = ask()
        self.asked_at = t = clock()
        if self.fast_left:
            self.fast_left -= 1
        if not ok and why in SOFT and not route_changed and not self.held and self.rec is not None and self.rec.ok:
            self.held = True  # the indicator keeps the last answer (it still ages) until the next request
            return None
        self.held = False
        return self._publish(Online(ok, why, t), route_changed)

    def _publish(self, rec: Online, route_changed: bool) -> Online:
        if route_changed or self.rec is None or rec.ok != self.rec.ok:
            self.fast_left = self.fast_n
        self.rec = rec
        return rec


# ── the cases --check-net proves ────────────────────────────────────────────────────────────
APPLE_SUCCESS = b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>\n"  # as measured

# (status, body) -> (ok, why)
CLASSIFY_CASES = [
    ((200, APPLE_SUCCESS), (True, "")),
    ((200, b"<html><body>\n  Success\n</body></html>"), (True, "")),
    ((200, b"<html><head><title>Guest Wi-Fi</title></head><body><form action='/login'>Sign in</form></body></html>"),
     (False, CAPTIVE)),
    ((200, b"<HTML><BODY>Login Success</BODY></HTML>"), (False, CAPTIVE)),  # a portal's own page, not Apple's
    ((200, b""), (False, CAPTIVE)),
    ((302, b""), (False, CAPTIVE)),
    ((307, APPLE_SUCCESS), (False, CAPTIVE)),  # a redirect is never followed, whatever its body
    ((511, b"<html>Network Authentication Required</html>"), (False, CAPTIVE)),
    ((503, b"Service Unavailable"), (False, "HTTP 503")),
]

# (the answer, its age in seconds) -> the view
STALE_CASES = [
    (None, 0.0, Conn(UNKNOWN, "not checked yet")),
    (Online(True), 0.0, Conn(ONLINE)),
    (Online(True), STALE_S, Conn(ONLINE)),
    (Online(True), STALE_S + 0.1, Conn(UNKNOWN, "last checked 15s ago")),
    (Online(False, CAPTIVE), 14.0, Conn(OFFLINE, CAPTIVE)),
    (Online(False, NO_NETWORK), 40.0, Conn(UNKNOWN, "last checked 40s ago")),
    (Online(False, ""), 1.0, Conn(OFFLINE, UNREACHABLE)),
]

OK, R1, R2 = (True, ""), "10.0.0.2", "192.168.1.5"
# Scripted runs of the state machine: each step is (seconds since the last step, the route, the
# answer a request would get, what the step must do). `want` is the view after the step, then
# "+ask" when the step must make a request (and "-" when it must not).
MACHINE_CASES = {
    "first answer online": [(0, R1, OK, "online +ask")],
    "first answer captive": [(0, R1, (False, CAPTIVE), "offline:captive portal +ask")],
    "first answer timeout": [(0, R1, (False, NO_ANSWER), f"offline:{NO_ANSWER} +ask")],
    "no route: no request": [(0, None, OK, "offline:no network -"), (1, None, OK, "offline:no network -")],
    "no default route, then one": [(0, None, OK, "offline:no network -"), (1, R1, OK, "online +ask")],
    "online, route lost": [(0, R1, OK, "online +ask"), (1, None, OK, "offline:no network -")],
    "online keeps its cadence": [(0, R1, OK, "online +ask"), (1, R1, OK, "online -"), (1, R1, OK, "online +ask"),
                                 (2, R1, OK, "online +ask"), (2, R1, OK, "online +ask"), (2, R1, OK, "online -"),
                                 (3, R1, OK, "online +ask")],
    "one timeout is asked again": [(0, R1, OK, "online +ask"), (5, R1, OK, "online +ask"), (5, R1, OK, "online +ask"),
                                   (5, R1, OK, "online +ask"), (5, R1, (False, NO_ANSWER), "online +ask"),
                                   (1, R1, OK, "online -"), (1, R1, (False, NO_ANSWER), f"offline:{NO_ANSWER} +ask")],
    "a timeout that clears": [(0, R1, OK, "online +ask"), (5, R1, (False, NO_DNS), "online +ask"),
                              (2, R1, OK, "online +ask"), (2, R1, (False, NO_DNS), "online +ask"),
                              (2, R1, OK, "online +ask")],
    "captive at once": [(0, R1, OK, "online +ask"), (5, R1, (False, CAPTIVE), "offline:captive portal +ask")],
    "offline to online": [(0, R1, (False, UNREACHABLE), f"offline:{UNREACHABLE} +ask"), (2, R1, OK, "online +ask")],
    "a new route is asked at once": [(0, R1, OK, "online +ask"), (1, R2, (False, CAPTIVE), "offline:captive portal +ask"),
                                     (1, R2, OK, "offline:captive portal -"), (1, R2, OK, "online +ask")],
    "a new route's timeout shows": [(0, R1, OK, "online +ask"), (1, R2, (False, NO_ANSWER), f"offline:{NO_ANSWER} +ask")],
    "an answer goes stale": [(0, R1, OK, "online +ask"), (16, R1, None, "unknown -")],
}


def _view(rec: Online | None, t: float) -> str:
    c = connection(rec, t)
    return f"{c.state}:{c.why}" if c.state == OFFLINE else c.state


def run_machine(steps) -> list[str]:
    """What each step of a scripted run did, in MACHINE_CASES' words; a step whose answer is None
    only reads the view (the clock moves, no tick)."""
    m, t, out = Machine(), 1_000_000.0, []
    for dt, route, answer, _want in steps:
        t += dt
        asked = []
        if answer is not None:
            m.tick(route, lambda a=answer: asked.append(1) or a, lambda: t)
        out.append(f"{_view(m.rec, t)} {'+ask' if asked else '-'}")
    return out


def check_connection_cases(out=print) -> int:
    """Prints 'N/N connection cases correct'; 1 on any mismatch."""
    bad = []
    for (status, body), want in CLASSIFY_CASES:
        got = classify(status, body)
        if got != want:
            bad.append(f"CONNECTION MISMATCH classify({status}, {body[:40]!r}) want={want!r} got={got!r}")
    for rec, age, want in STALE_CASES:
        got = connection(rec, (rec.at if rec else 0.0) + age)
        if got != want:
            bad.append(f"CONNECTION MISMATCH view({rec!r}, age {age}) want={want!r} got={got!r}")
    for name, steps in MACHINE_CASES.items():
        got = run_machine(steps)
        want = [s[3] for s in steps]
        if got != want:
            bad.append(f"CONNECTION MISMATCH {name}: want={want!r} got={got!r}")
    for line in bad:
        out(line)
    n = len(CLASSIFY_CASES) + len(STALE_CASES) + len(MACHINE_CASES)
    out(f"{n - len(bad)}/{n} connection cases correct")
    return 1 if bad else 0

"""Whether this computer is on the internet: the third state of the network indicator.

The background check (sources/connectivity.py) runs every second on its own thread, never on the
render path. Each tick it looks the route to the internet up in the kernel (a UDP connect: no
packet is sent). No route (Wi-Fi off, cable out) is "not connected" at once, with no request.
With a route, it asks Apple's captive-portal check (http://captive.apple.com/hotspot-detect.html,
the page macOS itself asks) every 5 s, every 2 s for the next three requests after the state or
the route changes, and at once when the route changes. A request has 2 s from its start (name
lookup, connect, answer); it tries every address the name resolves to, a new one every 0.25 s
without dropping the ones in flight (Happy Eyeballs), so a dead first address (a network whose
IPv6 is routed but broken) costs 0.25 s, not the whole request. Only the page's "Success" body
counts: a login page or a redirect (a captive portal) is not connected at once. A request that
fails with no answer, or with a server error (HTTP 4xx/5xx), while the last answer was "online"
is asked again 2 s later before the indicator says not connected, so one lost packet or one bad
answer from the check's server does not flash it.

The request is made inside the dashboard's own process, so `ps` never shows it and the indicator
never lists it as network work. The cadence runs on the monotonic clock, so a wall clock set back
cannot stop the check. A frame reads the last answer: one older than 15 s, or stamped more than
a second in the future (the wall clock was set back), reads "unknown", never a guess. The live
view re-reads it on every tick of its loop, so a change reaches the screen within a tick.

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
TIMEOUT_S = 2.0  # a request with no answer this long after it started (lookup included) is a failure
CAP_S = TIMEOUT_S + 0.5  # the request's thread is waited on this long at most (a lookup that hangs)
STAGGER_S = 0.25  # the next address is tried this long after the last one started (RFC 8305's delay)
STALE_S = 15.0  # an answer older than this reads "unknown"
CLOCK_SLACK_S = 1.0  # an answer stamped more than this in the future: the wall clock was set back

ONLINE, OFFLINE, UNKNOWN = "online", "offline", "unknown"

# Why the internet did not answer: short, as the indicator shows them.
NO_NETWORK = "no network"  # no route to the internet: no request is made
CAPTIVE = "captive portal"  # an answer that is not the Success page (a login page, a redirect)
NO_ANSWER = f"no answer in {human(TIMEOUT_S)}"
NO_DNS = "DNS failed"
UNREACHABLE = "unreachable"
BAD_ANSWER = "bad answer"
CLOCK_MOVED = "clock moved"
# Failures with no answer at all: while the last answer was "online", asked again before they show.
SOFT = frozenset({NO_ANSWER, NO_DNS, UNREACHABLE, BAD_ANSWER})


def soft(why: str) -> bool:
    """A failure asked again before it shows, when the last answer was "online": no answer at all,
    or a server error (HTTP 4xx/5xx: a server answered, so one of these may pass). A captive
    portal's answer (a redirect, 511, a page that is not Success) and no route show at once."""
    return why in SOFT or why.startswith("HTTP ")


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
    """The indicator's view of the last answer at `now` (the wall clock): an answer older than
    `stale_s`, or stamped in the future (the clock was set back since), is UNKNOWN."""
    if not isinstance(rec, Online):
        return Conn(UNKNOWN, "not checked yet")
    age = now - rec.at
    if age < -CLOCK_SLACK_S:
        return Conn(UNKNOWN, CLOCK_MOVED)
    if age > stale_s:
        return Conn(UNKNOWN, f"last checked {human(age)} ago")
    return Conn(ONLINE) if rec.ok else Conn(OFFLINE, rec.why or UNREACHABLE)


def drawn(conn: Conn | None) -> tuple[str, str] | None:
    """What the indicator draws of a view: its state, and why only when OFFLINE (UNKNOWN's why is
    not drawn). The live view redraws when this changes, not every second an old answer ages."""
    if conn is None:
        return None
    return conn.state, conn.why if conn.state == OFFLINE else ""


_NO_ROUTE_YET = object()  # before the first tick: any route is a change


class Machine:
    """What the background check does on each tick, with no I/O or thread of its own: `route` is the
    kernel's route key (None: no route), `ask` makes one request and gives (ok, why), `clock` times
    the cadence (the monotonic clock: a wall clock set back cannot stop the check) and `wall` stamps
    each answer (the dashboard's clock; `clock` when omitted). `tick` returns the answer to publish,
    or None when there is nothing new."""

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

    def tick(self, route, ask: Callable[[], tuple[bool, str]], clock: Callable[[], float],
             wall: Callable[[], float] | None = None) -> Online | None:
        wall = wall or clock
        t = clock()
        route_changed = route != self.route
        self.route = route
        if route is None:  # no route to the internet: not connected, and nothing to ask
            self.held = False
            return self._publish(Online(False, NO_NETWORK, wall()), route_changed)
        if not self.due(t, route_changed):
            return None
        ok, why = ask()
        self.asked_at = clock()
        if self.fast_left:
            self.fast_left -= 1
        if not ok and soft(why) and not route_changed and not self.held and self.rec is not None and self.rec.ok:
            self.held = True  # the indicator keeps the last answer (it still ages) until the next request
            return None
        self.held = False
        return self._publish(Online(ok, why, wall()), route_changed)

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
    ((403, b"Forbidden"), (False, "HTTP 403")),
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
    (Online(True), -CLOCK_SLACK_S, Conn(ONLINE)),  # a clock corrected by less than a second
    (Online(True), -CLOCK_SLACK_S - 0.5, Conn(UNKNOWN, CLOCK_MOVED)),
    (Online(False, CAPTIVE), -3600.0, Conn(UNKNOWN, CLOCK_MOVED)),
]

OK, R1, R2 = (True, ""), "10.0.0.2", "192.168.1.5"
# Scripted runs of the state machine: each step is (seconds since the last step, the route, the
# answer a request would get, what the step must do). `want` is the view after the step, then
# "+ask" when the step must make a request (and "-" when it must not). The seconds are a pair
# (monotonic, wall) when the wall clock is set: the cadence runs on the first, answers on the second.
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
    "one server error is asked again": [(0, R1, OK, "online +ask"), (2, R1, (False, "HTTP 503"), "online +ask"),
                                        (2, R1, OK, "online +ask")],
    "a server error that stays shows": [(0, R1, OK, "online +ask"), (2, R1, (False, "HTTP 503"), "online +ask"),
                                        (2, R1, (False, "HTTP 503"), "offline:HTTP 503 +ask")],
    "a first answer of 4xx shows": [(0, R1, (False, "HTTP 403"), "offline:HTTP 403 +ask")],
    "the wall clock set back": [(0, R1, OK, "online +ask"), ((1, -3600), R1, OK, "unknown -"),
                                (1, R1, OK, "online +ask")],
    "set back, then the internet dies": [(0, R1, OK, "online +ask"), ((5, -3600), R1, (False, NO_ANSWER), "unknown +ask"),
                                         (2, R1, (False, NO_ANSWER), f"offline:{NO_ANSWER} +ask")],
}


def _view(rec: Online | None, t: float) -> str:
    c = connection(rec, t)
    return f"{c.state}:{c.why}" if c.state == OFFLINE else c.state


def run_machine(steps) -> list[str]:
    """What each step of a scripted run did, in MACHINE_CASES' words; a step whose answer is None
    only reads the view (the clocks move, no tick)."""
    m, clocks, out = Machine(), [1_000_000.0, 1_000_000.0], []  # [monotonic, wall]
    for dt, route, answer, _want in steps:
        d_mono, d_wall = dt if isinstance(dt, tuple) else (dt, dt)
        clocks[0] += d_mono
        clocks[1] += d_wall
        asked = []
        if answer is not None:
            m.tick(route, lambda a=answer: asked.append(1) or a, lambda: clocks[0], lambda: clocks[1])
        out.append(f"{_view(m.rec, clocks[1])} {'+ask' if asked else '-'}")
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

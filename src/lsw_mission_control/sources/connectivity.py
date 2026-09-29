"""The background internet check (the rules are in lsw_mission_control/connectivity.py).

Everything here runs inside the dashboard's process, on its own daemon threads: the route lookup
is a UDP connect (the kernel picks a route and a source address; no packet is sent), and the
request is Python's http.client over a connection of our own (Happy Eyeballs: every resolved
address, a new one every 0.25 s, the first to connect wins). No child process is started, so
`ps` never shows the check and the indicator never lists it as network work.
"""

from __future__ import annotations

import errno
import http.client
import os
import selectors
import socket
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from lsw_mission_control.connectivity import (
    BAD_ANSWER,
    CAP_S,
    CHECK_URL,
    NO_ANSWER,
    NO_DNS,
    STAGGER_S,
    TICK_S,
    TIMEOUT_S,
    UNREACHABLE,
    Machine,
    classify,
)
from lsw_mission_control.sources import Source
from lsw_mission_control.store import Store
from lsw_mission_control.util import now

# Any public address: only the kernel's route lookup for it matters, and nothing is sent to it.
ROUTE_PROBES = ((socket.AF_INET, "1.1.1.1"), (socket.AF_INET6, "2606:4700:4700::1111"))
MAX_LIVE = 4  # request threads alive at once, at most (each hung on a lookup of its own network)
_PENDING = (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EAGAIN)


def route_key(sock=socket.socket) -> str | None:
    """The source address the kernel would send internet traffic from, per family ("4:10.0.0.2"),
    or None when neither IPv4 nor IPv6 has a route to the internet (no default route)."""
    keys = []
    for family, addr in ROUTE_PROBES:
        try:
            s = sock(family, socket.SOCK_DGRAM)
        except OSError:
            continue
        try:
            s.connect((addr, 80))  # a UDP connect only picks the route
            keys.append(f"{4 if family == socket.AF_INET else 6}:{s.getsockname()[0]}")
        except OSError:  # ENETUNREACH / EHOSTUNREACH: no route for this family
            pass
        finally:
            s.close()
    return " ".join(keys) or None


def interleave(infos: list) -> list:
    """getaddrinfo's answers with the families taking turns, each in the resolver's order, the
    resolver's first family first (RFC 8305, section 4): a family that is broken costs one stagger
    per turn, not one per address."""
    queues: dict = {}
    for info in infos:
        queues.setdefault(info[0], []).append(info)
    out = []
    while any(queues.values()):
        for q in queues.values():
            if q:
                out.append(q.pop(0))
    return out


def _start(info) -> tuple[socket.socket, int]:
    """A non-blocking connect to one getaddrinfo answer: (the socket, its errno; 0 = connected)."""
    family, kind, proto, _name, addr = info
    s = socket.socket(family, kind, proto)
    s.setblocking(False)
    return s, s.connect_ex(addr)


def connect_first(infos: list, deadline: float, stagger: float = STAGGER_S, clock: Callable[[], float] = time.monotonic,
                  start: Callable = _start) -> socket.socket:
    """A socket connected to the first of `infos` to answer (Happy Eyeballs, RFC 8305): the next
    address is tried `stagger` seconds after the last one started, or at once when one fails, and
    the attempts in flight go on. So a dead first address (a network whose IPv6 is routed but
    broken) costs `stagger`, not the whole request. TimeoutError at `deadline` (the monotonic
    clock); the last error when every address failed. The socket is blocking, and times out at
    `deadline`."""
    todo = interleave(list(infos))
    sel = selectors.DefaultSelector()
    live: list[socket.socket] = []
    last_err: OSError = OSError(errno.EHOSTUNREACH, "no address to connect to")
    next_at = clock()
    try:
        while True:
            t = clock()
            if t >= deadline:
                raise TimeoutError("timed out")
            if todo and (t >= next_at or not live):
                try:
                    s, err = start(todo.pop(0))
                except OSError as e:  # this family cannot be used here
                    last_err = e
                    continue
                if err == 0:
                    return _ready(s, deadline, clock)
                if err not in _PENDING:
                    s.close()
                    last_err = OSError(err, os.strerror(err))
                    continue
                sel.register(s, selectors.EVENT_WRITE)
                live.append(s)
                next_at = t + stagger
                continue
            if not live:
                raise last_err
            wait = deadline - t if not todo else min(deadline, next_at) - t
            for key, _events in sel.select(max(0.0, wait)):
                s = key.fileobj
                sel.unregister(s)
                live.remove(s)
                err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err == 0:
                    return _ready(s, deadline, clock)
                s.close()
                last_err = OSError(err, os.strerror(err))
                next_at = clock()  # a failed attempt starts the next one at once
    finally:
        for s in live:
            s.close()
        sel.close()


def _ready(s: socket.socket, deadline: float, clock: Callable[[], float]) -> socket.socket:
    s.setblocking(True)
    s.settimeout(max(0.001, deadline - clock()))
    return s


class EyeballsConnection(http.client.HTTPConnection):
    """http.client's connection, except that it connects through `connect_first`, and that the
    whole request (name lookup, connect, answer) has `timeout` seconds from when it was made."""

    def __init__(self, host: str, port: int | None = None, timeout: float = TIMEOUT_S) -> None:
        super().__init__(host, port, timeout=timeout)
        self.deadline = time.monotonic() + timeout

    def connect(self) -> None:
        infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        self.sock = connect_first(infos, self.deadline)


def ask_internet(timeout_s: float = TIMEOUT_S, url: str = CHECK_URL,
                 connection=EyeballsConnection) -> tuple[bool, str]:
    """One request for the check page: (ok, why). Redirects are not followed and no proxy is used."""
    u = urlsplit(url)
    conn = connection(u.hostname, u.port or 80, timeout=timeout_s)
    try:
        conn.request("GET", u.path or "/", headers={"User-Agent": "lsw-mission-control", "Cache-Control": "no-cache",
                                                    "Pragma": "no-cache", "Connection": "close"})
        resp = conn.getresponse()
        return classify(resp.status, resp.read(4096))
    except TimeoutError:  # socket.timeout
        return False, NO_ANSWER
    except socket.gaierror:
        return False, NO_DNS
    except http.client.HTTPException:
        return False, BAD_ANSWER
    except OSError:
        return False, UNREACHABLE
    finally:
        conn.close()


class Bounded:
    """Calls `fn` on a daemon thread and waits at most `timeout_s` for its answer (the request's
    own deadline is shorter: this catches a name lookup, which no socket timeout covers, hanging
    past it). Such a call is left to end on its own and the round counts as NO_ANSWER. While it
    runs, no second call starts for the same `key` (the route the call was made on), but a new
    route starts a call of its own: a lookup hung on the old network never blocks the new one.
    At most `max_live` calls are alive at once."""

    def __init__(self, fn: Callable[[], tuple[bool, str]], timeout_s: float = CAP_S, max_live: int = MAX_LIVE) -> None:
        self.fn, self.timeout_s, self.max_live = fn, timeout_s, max_live
        self.threads: dict = {}  # key -> the last call's thread

    @property
    def thread(self) -> threading.Thread | None:
        """The last call's thread."""
        return next(reversed(self.threads.values()), None)

    def __call__(self, key: object = None) -> tuple[bool, str]:
        self.threads = {k: th for k, th in self.threads.items() if th.is_alive()}
        if key in self.threads or len(self.threads) >= self.max_live:
            return False, NO_ANSWER
        box: list = []

        def run() -> None:
            try:
                box.append(self.fn())
            except Exception:  # noqa: BLE001 — a check that breaks answers "unreachable", never kills the source
                box.append((False, UNREACHABLE))
        th = threading.Thread(target=run, name="lsw-mc-internet", daemon=True)
        self.threads[key] = th
        th.start()
        th.join(self.timeout_s)
        return box[0] if box else (False, NO_ANSWER)


class ConnectivitySource(Source):
    """Publishes each new answer as the store's `online` (a connectivity.Online). `ask` takes the
    route the request is made on; `clock` times the cadence, `wall` stamps the answers."""

    name = "connectivity"
    interval_s = TICK_S

    def __init__(self, store: Store, route: Callable[[], str | None] | None = None,
                 ask: Callable[[object], tuple[bool, str]] | None = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = now) -> None:
        self.store = store
        self.route = route or route_key
        self.ask = ask or Bounded(ask_internet)
        self.clock, self.wall = clock, wall
        self.machine = Machine()

    def poll_once(self) -> None:
        route = self.route()
        rec = self.machine.tick(route, lambda: self.ask(route), self.clock, self.wall)
        if rec is not None:
            self.store.set("online", rec)

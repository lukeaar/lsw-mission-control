"""The background internet check (the rules are in lsw_mission_control/connectivity.py).

Everything here runs inside the dashboard's process, on its own daemon threads: the route lookup
is a UDP connect (the kernel picks a route and a source address; no packet is sent), and the
request is Python's http.client. No child process is started, so `ps` never shows the check and
the indicator never lists it as network work.
"""

from __future__ import annotations

import http.client
import socket
import threading
from typing import Callable
from urllib.parse import urlsplit

from lsw_mission_control.connectivity import (
    BAD_ANSWER,
    CHECK_URL,
    NO_ANSWER,
    NO_DNS,
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


def ask_internet(timeout_s: float = TIMEOUT_S, url: str = CHECK_URL,
                 connection=http.client.HTTPConnection) -> tuple[bool, str]:
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
    """Calls `fn` on a daemon thread and waits at most `timeout_s` for its answer. The socket
    timeout does not cover the DNS lookup, which can hang far longer: such a call is left to end
    on its own, the round counts as NO_ANSWER, and no second call starts while it runs."""

    def __init__(self, fn: Callable[[], tuple[bool, str]], timeout_s: float = TIMEOUT_S) -> None:
        self.fn, self.timeout_s = fn, timeout_s
        self.thread: threading.Thread | None = None

    def __call__(self) -> tuple[bool, str]:
        if self.thread is not None and self.thread.is_alive():
            return False, NO_ANSWER
        box: list = []

        def run() -> None:
            try:
                box.append(self.fn())
            except Exception:  # noqa: BLE001 — a check that breaks answers "unreachable", never kills the source
                box.append((False, UNREACHABLE))
        self.thread = threading.Thread(target=run, name="lsw-mc-internet", daemon=True)
        self.thread.start()
        self.thread.join(self.timeout_s)
        return box[0] if box else (False, NO_ANSWER)


class ConnectivitySource(Source):
    """Publishes each new answer as the store's `online` (a connectivity.Online)."""

    name = "connectivity"
    interval_s = TICK_S

    def __init__(self, store: Store, route: Callable[[], str | None] | None = None,
                 ask: Callable[[], tuple[bool, str]] | None = None, clock: Callable[[], float] = now) -> None:
        self.store = store
        self.route = route or route_key
        self.ask = ask or Bounded(ask_internet, TIMEOUT_S)
        self.clock = clock
        self.machine = Machine()

    def poll_once(self) -> None:
        rec = self.machine.tick(self.route(), self.ask, self.clock)
        if rec is not None:
            self.store.set("online", rec)

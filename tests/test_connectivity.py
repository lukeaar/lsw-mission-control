"""The internet check behind the indicator's "not connected" state: its verdicts, its staleness
rule, its state machine, its I/O (against local servers only: no test asks the internet) and its
wiring into the engine and the live view."""

from __future__ import annotations

import http.server
import io
import socket
import subprocess
import threading
import time

import pytest
from rich.console import Console

from lsw_mission_control import connectivity as cx
from lsw_mission_control.app import Scroll
from lsw_mission_control.config import ConfigError, load_config
from lsw_mission_control.connectivity import (
    CAPTIVE,
    NO_ANSWER,
    NO_DNS,
    NO_NETWORK,
    OFFLINE,
    ONLINE,
    UNKNOWN,
    UNREACHABLE,
    Conn,
    Machine,
    Online,
    classify,
    connection,
)
from lsw_mission_control.net import NET_CHIP, OFFLINE_CHIP, UNKNOWN_NOTE, check_net_cases, network_flag
from lsw_mission_control.sources import Source
from lsw_mission_control.sources.connectivity import Bounded, ConnectivitySource, ask_internet, route_key
from lsw_mission_control.store import Store

from conftest import NOW
from scenarios import Project, midway

OK = (True, "")


# ── verdicts and the staleness rule ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("answer, want", cx.CLASSIFY_CASES)
def test_classify_cases(answer, want):
    assert classify(*answer) == want


def test_a_captive_portal_body_is_not_success():
    assert classify(200, cx.APPLE_SUCCESS) == OK
    assert classify(200, b"<html><body>Welcome! Accept the terms to continue.</body></html>") == (False, CAPTIVE)
    assert classify(200, cx.APPLE_SUCCESS.replace(b"Success", b"Succes")) == (False, CAPTIVE)
    assert classify(301, cx.APPLE_SUCCESS) == (False, CAPTIVE)


@pytest.mark.parametrize("rec, age, want", cx.STALE_CASES)
def test_stale_cases(rec, age, want):
    assert connection(rec, (rec.at if rec else 0.0) + age) == want


def test_an_answer_older_than_15_s_is_unknown_whatever_it_said():
    for rec in (Online(True, "", NOW), Online(False, CAPTIVE, NOW), Online(False, NO_NETWORK, NOW)):
        assert connection(rec, NOW + 15).state != UNKNOWN
        assert connection(rec, NOW + 15.01) == Conn(UNKNOWN, "last checked 15s ago")
    assert connection(None, NOW) == Conn(UNKNOWN, "not checked yet")
    assert connection("garbage", NOW) == Conn(UNKNOWN, "not checked yet")  # a store value of the wrong kind


# ── the state machine ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(cx.MACHINE_CASES))
def test_machine_cases(name):
    steps = cx.MACHINE_CASES[name]
    assert cx.run_machine(steps) == [s[3] for s in steps]


class Rig:
    """A machine driven step by step: a clock, a route, and the answer the next request gets."""

    def __init__(self) -> None:
        self.m, self.t, self.route, self.answer, self.asked = Machine(), 1000.0, "4:10.0.0.2", OK, 0

    def ask(self):
        self.asked += 1
        return self.answer

    def tick(self, dt: float = 0.0, **kw) -> Online | None:
        self.t += dt
        for k, v in kw.items():
            setattr(self, k, v)
        return self.m.tick(self.route, self.ask, lambda: self.t)

    def view(self) -> Conn:
        return connection(self.m.rec, self.t)


def test_every_transition():
    r = Rig()
    assert r.view().state == UNKNOWN  # nothing asked yet
    # unknown -> online
    assert r.tick() == Online(True, "", 1000.0) and r.asked == 1 and r.view() == Conn(ONLINE)
    # online -> offline: no route (at once, no request)
    assert r.tick(1, route=None) == Online(False, NO_NETWORK, 1001.0) and r.asked == 1
    assert r.view() == Conn(OFFLINE, NO_NETWORK)
    # offline (no route) -> offline (captive portal): a new network with a login page
    assert r.tick(1, route="4:192.168.1.5", answer=(False, CAPTIVE)).why == CAPTIVE and r.asked == 2
    # offline -> online: logged in
    assert r.tick(2, answer=OK).ok and r.asked == 3
    # online -> offline (captive): a real answer that is not Success shows at once
    assert r.tick(2, answer=(False, CAPTIVE)).why == CAPTIVE and r.view() == Conn(OFFLINE, CAPTIVE)
    # offline -> offline with another reason
    assert r.tick(2, answer=(False, NO_ANSWER)).why == NO_ANSWER
    # offline -> online
    assert r.tick(2, answer=OK).ok
    # online -> held (one timeout: not shown yet) -> offline (the second)
    r.tick(2), r.tick(2), r.tick(2)  # the three fast requests after the change run out
    assert r.m.fast_left == 0
    assert r.tick(5, answer=(False, NO_ANSWER)) is None and r.m.held and r.view() == Conn(ONLINE)
    assert r.tick(2).why == NO_ANSWER and not r.m.held and r.view() == Conn(OFFLINE, NO_ANSWER)
    # online -> held -> online (the recheck answered)
    r.tick(2, answer=OK)
    r.tick(2), r.tick(2), r.tick(2)
    assert r.tick(5, answer=(False, NO_DNS)) is None and r.m.held
    assert r.tick(2, answer=OK).ok and not r.m.held
    # online -> unknown: no answer for more than 15 s (the check stopped, the machine slept)
    assert r.view() == Conn(ONLINE)
    r.t += 15.5
    assert r.view() == Conn(UNKNOWN, "last checked 15s ago")
    # unknown -> online: the next tick asks at once (it is long overdue)
    assert r.tick(0).ok and r.view() == Conn(ONLINE)


def test_no_default_route_never_asks():
    r = Rig()
    for _ in range(10):
        r.tick(1, route=None)
    assert r.asked == 0 and r.view() == Conn(OFFLINE, NO_NETWORK)
    assert r.m.rec.at == r.t  # published every tick: it never goes stale while there is no route


def test_a_timeout_on_the_first_answer_shows_at_once():
    r = Rig()
    assert r.tick(answer=(False, NO_ANSWER)) == Online(False, NO_ANSWER, 1000.0)


def test_a_timeout_on_a_new_route_shows_at_once():
    r = Rig()
    r.tick()
    assert r.tick(1, route="4:192.168.1.5", answer=(False, NO_ANSWER)).why == NO_ANSWER


def test_cadence_5_s_and_2_s_after_a_change():
    r = Rig()
    times = []
    for _ in range(40):  # 40 s of one-second ticks, online throughout
        before = r.asked
        r.tick(1)
        if r.asked > before:
            times.append(r.t - 1000)
    # the first answer is a change: three requests 2 s apart, then every 5 s
    assert times == [1, 3, 5, 7, 12, 17, 22, 27, 32, 37]
    # a new route: asked at the next tick, then three requests 2 s apart again
    r.asked, times = 0, []
    r.route = "4:192.168.1.5"
    for _ in range(12):
        before = r.asked
        r.tick(1)
        if r.asked > before:
            times.append(r.t - 1040)
    assert times == [1, 3, 5, 7, 12]


def test_the_published_answer_is_never_stale_while_the_check_runs():
    """With every request at its slowest (the 2 s timeout) and one held failure, the answer the
    indicator reads is never older than 15 s."""
    r = Rig()
    worst = 0.0
    answers = [OK] * 20 + [(False, NO_ANSWER), OK] * 5
    for a in answers * 3:
        r.answer = a
        r.m.tick(r.route, lambda: (setattr(r, "t", r.t + 2.0), r.answer)[1], lambda: r.t)
        r.t += 1
        worst = max(worst, r.t - r.m.rec.at)
    assert worst <= 12.0, worst


# ── I/O: the route lookup and the request ───────────────────────────────────────────────────
class FakeSock:
    def __init__(self, routes: dict, family, kind):
        self.family, self.routes = family, routes
        assert kind == socket.SOCK_DGRAM

    def connect(self, addr):
        if self.family not in self.routes:
            raise OSError(65, "No route to host")
        self.addr = addr

    def getsockname(self):
        return (self.routes[self.family], 0)

    def close(self):
        pass


def test_route_key():
    def sock(routes):
        return lambda family, kind: FakeSock(routes, family, kind)
    assert route_key(sock({})) is None  # no default route in either family
    assert route_key(sock({socket.AF_INET: "10.0.0.2"})) == "4:10.0.0.2"
    assert route_key(sock({socket.AF_INET6: "fd00::2"})) == "6:fd00::2"  # an IPv6-only network is connected
    assert route_key(sock({socket.AF_INET: "10.0.0.2", socket.AF_INET6: "fd00::2"})) == "4:10.0.0.2 6:fd00::2"

    def no_ipv6(family, kind):
        if family == socket.AF_INET6:
            raise OSError(47, "Address family not supported")
        return FakeSock({socket.AF_INET: "10.0.0.2"}, family, kind)
    assert route_key(no_ipv6) == "4:10.0.0.2"


class Server:
    """A local HTTP server answering every GET with (status, headers, body)."""

    def __init__(self, status=200, body=cx.APPLE_SUCCESS, headers=()):
        answer = (status, headers, body)
        self.paths: list[str] = []
        paths = self.paths

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                paths.append(self.path)
                st, hd, bd = answer
                self.send_response(st)
                for k, v in hd:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(bd)))
                self.end_headers()
                self.wfile.write(bd)

            def log_message(self, *a):
                pass
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/hotspot-detect.html"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.mark.parametrize("status, body, headers, want", [
    (200, cx.APPLE_SUCCESS, (), OK),
    (200, b"<html><body><form action='/login'>Sign in</form></body></html>", (), (False, CAPTIVE)),
    (302, b"", (("Location", "http://127.0.0.1:9/ok"),), (False, CAPTIVE)),  # never followed
    (503, b"down", (), (False, "HTTP 503")),
])
def test_ask_internet_against_a_local_server(status, body, headers, want):
    srv = Server(status, body, headers)
    try:
        assert ask_internet(1.0, srv.url) == want
        assert srv.paths == ["/hotspot-detect.html"]  # one request: a redirect is not followed
    finally:
        srv.close()


def test_ask_internet_times_out():
    """A server that accepts and never answers: the answer is NO_ANSWER at the timeout."""
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    try:
        t0 = time.monotonic()
        assert ask_internet(0.3, f"http://127.0.0.1:{lst.getsockname()[1]}/") == (False, NO_ANSWER)
        assert time.monotonic() - t0 < 2.0
    finally:
        lst.close()


def test_ask_internet_failures():
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    free = port.getsockname()[1]
    port.close()  # nothing listens there now: refused
    assert ask_internet(1.0, f"http://127.0.0.1:{free}/") == (False, UNREACHABLE)

    class Conn_:
        def __init__(self, exc):
            self.exc = exc

        def __call__(self, host, port, timeout):
            return self

        def request(self, *a, **kw):
            raise self.exc

        def close(self):
            pass
    assert ask_internet(connection=Conn_(socket.gaierror(8, "nodename nor servname provided"))) == (False, NO_DNS)
    assert ask_internet(connection=Conn_(TimeoutError("timed out"))) == (False, NO_ANSWER)
    import http.client
    assert ask_internet(connection=Conn_(http.client.RemoteDisconnected("closed")))[0] is False


def test_bounded_never_waits_past_its_timeout_and_never_doubles_a_hung_call():
    release = threading.Event()
    calls = []

    def hang():
        calls.append(1)
        release.wait(5)
        return OK
    b = Bounded(hang, 0.2)
    t0 = time.monotonic()
    assert b() == (False, NO_ANSWER) and time.monotonic() - t0 < 1.0
    assert b() == (False, NO_ANSWER) and len(calls) == 1  # still running: not started again
    release.set()
    b.thread.join(2)
    assert b() == OK and len(calls) == 2
    assert Bounded(lambda: 1 / 0, 0.5)() == (False, UNREACHABLE)


# ── the source ──────────────────────────────────────────────────────────────────────────────
def test_the_source_publishes_into_the_store_and_starts_no_process(monkeypatch):
    """The check is never listed as network work: it runs in the dashboard's own process (ps
    lists processes, never threads), and it starts no child process at all."""
    def no_process(*a, **kw):
        raise AssertionError("the internet check started a process")
    monkeypatch.setattr(subprocess, "Popen", no_process)
    srv = Server()
    try:
        store = Store()
        t = [NOW]
        src = ConnectivitySource(store, route=lambda: "4:127.0.0.1",
                                 ask=Bounded(lambda: ask_internet(1.0, srv.url), 1.0), clock=lambda: t[0])
        src.poll_once()
        assert store.get("online") == Online(True, "", NOW)
        t[0] += 1
        src.poll_once()  # not due: nothing new
        assert store.get("online") == Online(True, "", NOW) and len(srv.paths) == 1
        src.route = lambda: None
        src.poll_once()
        assert store.get("online") == Online(False, NO_NETWORK, NOW + 1) and len(srv.paths) == 1
    finally:
        srv.close()
    assert src.interval_s == 1.0 and src.name == "connectivity"


def test_the_source_defaults_are_the_real_check():
    src = ConnectivitySource(Store())
    assert src.route is route_key and isinstance(src.ask, Bounded) and src.ask.fn is ask_internet
    assert src.ask.timeout_s == 2.0 and cx.CHECK_URL == "http://captive.apple.com/hotspot-detect.html"


# ── the engine, the frame and the live view ─────────────────────────────────────────────────
def test_the_engine_starts_the_check_only_when_it_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr(Source, "start", lambda self: None)
    p = Project(tmp_path)
    e = p.engine()
    e.start_sources(probe=False)
    assert any(isinstance(s, ConnectivitySource) for s in e.sources)
    p.write_config("[network]\ncheck_internet = false\n")
    e = p.engine()
    e.start_sources(probe=False)
    assert not any(isinstance(s, ConnectivitySource) for s in e.sources)
    assert e.connection() is None  # off: the indicator shows only whether switching is safe


def test_once_waits_for_the_first_answer(tmp_path):
    p = Project(tmp_path)
    midway(p)
    p.store["online"] = None
    e = p.engine()
    assert not e.ready()
    e.store.set("online", Online(False, CAPTIVE, NOW))
    assert e.ready()
    e.store.set("online", None)
    e.internet_check = False
    assert e.ready()  # off: nothing to wait for


def test_config_check_internet(tmp_path):
    p = Project(tmp_path)
    assert load_config(p.dot / "mission-control.toml").network.check_internet is True
    p.write_config("[network]\ncheck_internet = false\n")
    assert load_config(p.dot / "mission-control.toml").network.check_internet is False
    p.write_config('[network]\ncheck_internet = "no"\n')
    with pytest.raises(ConfigError, match=r"\[network\].check_internet must be"):
        load_config(p.dot / "mission-control.toml")


def _bottom(plain: str) -> str:
    return plain.rstrip("\n").splitlines()[-1]


@pytest.mark.parametrize("online, ps, want", [
    (Online(True, "", NOW - 3), "", "● safe to switch networks"),
    (Online(False, CAPTIVE, NOW - 3), "", f"{OFFLINE_CHIP}  captive portal"),
    (Online(False, NO_NETWORK, NOW - 1), "  300  1  00:20 git push origin main\n",
     f"{OFFLINE_CHIP}  no network · git push (20s)"),
    (Online(True, "", NOW - 3), "  300  1  00:20 git push origin main\n", f"{NET_CHIP}  git push (20s)"),
    (Online(True, "", NOW - 20), "", f"● safe to switch networks  {UNKNOWN_NOTE}"),
    (None, "", f"● safe to switch networks  {UNKNOWN_NOTE}"),
])
def test_the_whole_frame_shows_the_three_states(tmp_path, online, ps, want):
    from lsw_mission_control import testing

    p = Project(tmp_path)
    midway(p)
    p.store["online"] = online
    p.ps = ps
    plain, _ = testing.render_engine(p.engine(), 150)
    assert _bottom(plain).endswith(want)


def test_a_frame_error_still_shows_not_connected(tmp_path, monkeypatch):
    from lsw_mission_control import testing

    p = Project(tmp_path)
    midway(p)
    p.store["online"] = Online(False, CAPTIVE, NOW - 1)
    e = p.engine()
    monkeypatch.setattr(e, "build_frame", lambda width: 1 / 0)
    plain, _ = testing.render_engine(e, 120)
    assert "Mission control error" in plain and OFFLINE_CHIP in _bottom(plain)

    def broken():
        raise RuntimeError("store")
    monkeypatch.setattr(e, "connection", broken)
    plain, _ = testing.render_engine(e, 120)
    assert _bottom(plain).endswith(UNKNOWN_NOTE)  # the view could not be read: unknown, never a guess


def test_the_live_view_pins_not_connected(tmp_path):
    p = Project(tmp_path)
    midway(p)
    p.store["online"] = Online(False, NO_ANSWER, NOW - 1)
    e = p.engine()
    s = Scroll(150, e.logo)
    console = Console(file=io.StringIO(), width=150, height=40)
    s.body, s.net, _ = e.safe_frame(console)
    out = Console(file=io.StringIO(), width=150, height=40, force_terminal=True, color_system=None)
    out.print(s)
    assert out.file.getvalue().splitlines()[-1].endswith(f"{OFFLINE_CHIP}  {NO_ANSWER}")


def test_not_connected_reads_on_its_own_chip():
    """A chip carries its own background, as NETWORK-CRITICAL's does: it reads the same on any
    terminal, and in any [theme] whose NETWORK-CRITICAL chip reads."""
    from lsw_mission_control.theme import C

    flag = network_flag([], 60, Conn(OFFLINE, CAPTIVE))
    chip = next(sp for sp in flag.spans if flag.plain[sp.start:sp.end] == OFFLINE_CHIP)
    assert str(chip.style) == f"bold {C.BG} on {C.AMBER}"
    crit = network_flag(["git push (1m)"], 60)
    assert str(crit.spans[0].style) == f"bold {C.BG} on {C.RED}"  # distinct from the red chip
    assert "⊘" in OFFLINE_CHIP and "●" not in OFFLINE_CHIP  # and its own glyph


def test_not_connected_reads_in_a_light_theme_too():
    """In a light [theme] the chip's letters take the theme's dark text, not its light background."""
    from lsw_mission_control import theme

    theme.use(theme.theme_from({"bg": "#fdfbf9", "surface": "#f0ecf1", "text": "#252226"}))
    flag = network_flag([], 60, Conn(OFFLINE, CAPTIVE))
    chip = next(sp for sp in flag.spans if flag.plain[sp.start:sp.end] == OFFLINE_CHIP)
    assert str(chip.style) == "bold #252226 on #d29922"
    assert theme.contrast("#252226", "#d29922") > 6 and theme.contrast("#fdfbf9", "#d29922") < 3


def test_check_net_reports_a_connection_mismatch(monkeypatch):
    monkeypatch.setitem(cx.MACHINE_CASES, "broken on purpose", [(0, "4:10.0.0.2", OK, "offline:no network +ask")])
    lines = []
    assert check_net_cases(out=lines.append) == 1
    assert lines[0].endswith("network cases correct")  # the reload gate's phrase is unchanged
    assert any(x.startswith("CONNECTION MISMATCH broken on purpose") for x in lines)
    assert lines[-1] == "30/31 connection cases correct"


def test_a_frame_never_waits_on_the_network(tmp_path, monkeypatch):
    """The render path only reads the store: with every socket refused and the check's own
    functions broken, a frame still draws the last answer."""
    import lsw_mission_control.sources.connectivity as sc
    from lsw_mission_control import testing

    def no_io(*a, **kw):
        raise AssertionError("a frame touched the network")
    monkeypatch.setattr(socket, "socket", no_io)
    monkeypatch.setattr(socket, "create_connection", no_io)
    monkeypatch.setattr(sc, "route_key", no_io)
    monkeypatch.setattr(sc, "ask_internet", no_io)
    p = Project(tmp_path)
    midway(p)
    p.store["online"] = Online(False, CAPTIVE, NOW - 4)
    e = p.engine()
    plain, _ = testing.render_engine(e, 150)
    assert not e.last_error and _bottom(plain).endswith(f"{OFFLINE_CHIP}  captive portal")

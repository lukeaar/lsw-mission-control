"""Compute jobs: the heavy work running here and on an adopter's remotes (docs/config.md, [compute]).

Three kinds of row, merged at frame time (ComputeBook.rows):

- descriptors: JSON files a long job writes into `[compute] descriptors`, naming itself, what it runs
  as (a pid, a process group, a pid file; on a remote, a container name or prefix), the VM whose CPU
  is its own, and how its progress counts;
- this computer's process groups over `[compute] min_cores` that no descriptor claims, each by its
  heaviest command, and its virtual machines (macOS's Virtualization framework: colima and lima) by
  name;
- a plugin's rows (Plugin.compute_jobs): its remotes' jobs, which a remote descriptor can name.

The source samples `ps` every `[compute] poll_s`: a process's CPU is its CPU time's growth since the
last sample (ps's own %cpu the first time), in cores.
"""

from __future__ import annotations

import glob as _glob
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from lsw_mission_control.sources import Source
from lsw_mission_control.util import iso, now
from lsw_mission_control.util import run as _run

PS_ARGS = ("ps", "-A", "-o", "pid=,pgid=,ppid=,%cpu=,time=,rss=,etime=,command=")
VM_EXE = "com.apple.Virtualization.VirtualMachine"
# Never a job: the system's own programs, desktop apps, editors and the agents themselves (by the
# program's path or name), and any mission-control window. [compute] exclude adds substrings.
SYSTEM_PREFIXES = ("/System/", "/usr/libexec/", "/usr/sbin/", "/sbin/", "/Library/", "/Applications/",
                   "/usr/lib/systemd/")
EXCLUDE_NAMES = frozenset({"claude", "ps", "kernel_task", "launchd", "WindowServer", "systemd", "Xorg", "code",
                           "Code", "cursor", "Cursor"})
EXCLUDE_SUBSTRINGS = ("/.vscode/", "/.cursor/", "lsw_mission_control", "lsw-mc ")
RATE_WINDOW_S = 3 * 3600  # an ETA reads the rate over at most the last 3 h
RATE_MIN_S = 300  # ... once it spans 5 min


@dataclass
class ComputeJob:
    """One heavy job, as a row of the Compute panel. A plugin returns these for its remotes."""

    machine: str  # where it runs: a remote's name ("local" rows are the engine's own)
    label: str
    started: float | None = None  # epoch seconds
    cores: float | None = None  # CPU in use, in cores (docker's 400% is 4.0)
    mem: float | None = None  # bytes
    done: float | None = None  # progress, with total
    total: float | None = None
    detail: str = ""  # shown dimmed after the label ("stage 6"), where the job column has room
    names: tuple[str, ...] = ()  # the containers (or processes) it covers: a remote descriptor matches these
    eta: float | None = None  # seconds left, when its source knows better than the rate the dashboard sees
    as_of: float | None = None  # when it was read (a remote's last answer); None: now
    waiting: bool = False  # registered, but nothing of it runs yet
    ended: float | None = None  # a descriptor's job that ended this session, at this time
    key: str = ""  # what its progress samples are kept under (the engine sets it)
    # What it is doing when that is not plainly running ("frozen (battery)", "waiting (memory)"):
    # its state column reads this, in amber, and it has no finish while it does. "" is running.
    state: str = ""
    unit: str = ""  # what its progress counts ("files", "reads"): the progress column says it


@dataclass
class Proc:
    pid: int
    pgid: int
    ppid: int
    cores: float
    cpu_s: float
    rss: float  # bytes
    etime: float
    command: str


@dataclass
class Descriptor:
    key: str  # the file's stem
    label: str
    machine: str = "local"
    pids: tuple[int, ...] = ()
    pgids: tuple[int, ...] = ()
    pidfiles: tuple[str, ...] = ()
    containers: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    vm: str = ""
    progress: dict | None = None
    total: float | None = None
    started: float | None = None
    detail: str = ""
    state: dict | None = None  # {"file": f, "when": {substring: state}}: read_state()
    unit: str = ""


@dataclass
class DescState:
    """A descriptor as the last sample found it."""

    d: Descriptor
    alive: bool  # its local match (pid, process group, pid file) runs
    cores: float | None = None
    mem: float | None = None
    started: float | None = None
    done: float | None = None
    total: float | None = None
    state: str = ""  # its state source's answer ("" running)


@dataclass
class ComputeSnapshot:
    at: float
    local: list[ComputeJob] = field(default_factory=list)  # unclaimed groups and VMs over the threshold
    descs: list[DescState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # descriptors that did not load: "name.json: why"


# ── ps ──────────────────────────────────────────────────────────────────────────────────────
def parse_clock(text: str) -> float:
    """ps's times as seconds: '1-02:03:04', '02:03:04', '03:04', '1:23.45' (macOS's cpu time)."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    secs = 0.0
    for part in text.split(":"):
        secs = secs * 60 + float(part)
    return days * 86400 + secs


def parse_ps(text: str) -> list[Proc]:
    out = []
    for line in text.splitlines():
        f = line.split(None, 7)
        if len(f) < 8:
            continue
        try:
            out.append(Proc(int(f[0]), int(f[1]), int(f[2]), float(f[3]) / 100, parse_clock(f[4]), float(f[5]) * 1024,
                            parse_clock(f[6]), f[7]))
        except ValueError:
            continue
    return out


def exe_of(command: str) -> str:
    """The program's path: the command's first word, or a macOS path with spaces up to its binary."""
    m = re.match(r"(/.*?\.app/Contents/MacOS/\S+)", command)
    return m.group(1) if m else (command.split(None, 1) or [""])[0]


def is_vm(p: Proc) -> bool:
    return os.path.basename(exe_of(p.command)) == VM_EXE


def excluded(p: Proc, extra: tuple[str, ...] = ()) -> bool:
    if p.pid <= 1 or p.pgid == 0 or p.command.startswith("["):  # launchd/init, kernel threads
        return True
    if is_vm(p):
        return False
    exe = exe_of(p.command)
    if exe.startswith(SYSTEM_PREFIXES) or os.path.basename(exe) in EXCLUDE_NAMES:
        return True
    return any(s in p.command for s in EXCLUDE_SUBSTRINGS + tuple(extra))


def short_command(command: str, width: int = 60) -> str:
    """'python -m pytest -q tests/x.py' from the whole line: the program's name and each path's last
    part, cut to `width`."""
    exe = exe_of(command)
    rest = command[len(exe):].split()
    name = os.path.basename(exe)
    if name in ("Python", "Python3"):
        name = "python"
    words = [name] + [os.path.basename(w.rstrip("/")) if "/" in w and not w.startswith("--") else w for w in rest]
    text = " ".join(w for w in words if w)
    return text if len(text) <= width else text[:width - 1] + "…"


def vm_names(procs: list[Proc], homes: list[Path]) -> dict[int, str]:
    """{pid: name} of the VM processes: a lima instance's host agent (its ha.pid) and its VM start
    within a few seconds of each other. colima's profile 'x' is lima's 'colima-x' ('colima' alone is
    its default profile)."""
    by_pid = {p.pid: p for p in procs}
    vms = [p for p in procs if is_vm(p)]
    out: dict[int, str] = {}
    for home in homes:
        for pidfile in sorted(home.glob("*/ha.pid")):
            try:
                agent = by_pid.get(int(pidfile.read_text().strip()))
            except (OSError, ValueError):
                continue
            if agent is None:
                continue
            inst = pidfile.parent.name
            name = (f"colima {inst[7:]}" if inst.startswith("colima-") else "colima" if inst == "colima"
                    else f"lima {inst}")
            near = [v for v in vms if v.pid not in out and abs(v.etime - agent.etime) <= 5]
            if near:
                out[min(near, key=lambda v: abs(v.etime - agent.etime)).pid] = name
    return out


def lima_homes() -> list[Path]:
    homes = [Path(os.path.expanduser("~/.colima/_lima"))]
    homes.append(Path(os.environ["LIMA_HOME"]) if os.environ.get("LIMA_HOME") else Path(os.path.expanduser("~/.lima")))
    return homes


def vm_profile(name: str) -> str:
    """A descriptor's `vm` as vm_names() names it: 'cand' and 'colima cand' are colima's profile."""
    name = name.strip()
    if name.startswith(("colima", "lima ")):
        return name
    return "colima" if name == "default" else f"colima {name}"


# ── descriptors ─────────────────────────────────────────────────────────────────────────────
def _ints(v) -> tuple[int, ...]:
    vs = v if isinstance(v, list) else [v]
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in vs):
        raise ValueError("pid and pgid must be whole numbers")
    return tuple(vs)


def _strs(v) -> tuple[str, ...]:
    vs = v if isinstance(v, list) else [v]
    if not all(isinstance(x, str) and x for x in vs):
        raise ValueError("pidfile, container and container_prefix must be text")
    return tuple(vs)


def _time(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        return iso(v)
    raise ValueError("started must be epoch seconds or an ISO time")


def parse_descriptor(key: str, raw) -> Descriptor:
    if not isinstance(raw, dict):
        raise ValueError("not a JSON object")
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("no label")
    machine = raw.get("machine", "local")
    if not isinstance(machine, str) or not machine:
        raise ValueError("machine must be text")
    m = raw.get("match") or {}
    if not isinstance(m, dict):
        raise ValueError("match must be an object")
    unknown = set(m) - {"pid", "pgid", "pidfile", "container", "container_prefix"}
    if unknown:
        raise ValueError(f"unknown match key {sorted(unknown)[0]}")
    vm = raw.get("vm", "")
    if not isinstance(vm, str):
        raise ValueError("vm must be text")
    if not m and not vm:
        raise ValueError("nothing to match (match or vm)")
    prog = raw.get("progress")
    if prog is not None and not (isinstance(prog, dict) and ("file" in prog or "glob" in prog)):
        raise ValueError("progress must be an object with a file or a glob")
    total = raw.get("total")
    if total is not None and not (isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0):
        raise ValueError("total must be a number above 0")
    detail = raw.get("detail", "")
    state = raw.get("state")
    if state is not None and not (isinstance(state, dict) and isinstance(state.get("file"), str)
                                  and isinstance(state.get("when"), dict) and state["when"]
                                  and all(isinstance(k, str) and k and isinstance(v, str)
                                          for k, v in state["when"].items())):
        raise ValueError("state must be an object with a file and a when of text to text")
    unit = raw.get("unit", "")
    if not isinstance(unit, str):
        raise ValueError("unit must be text")
    return Descriptor(
        key=key, label=label.strip(), machine=machine, vm=vm_profile(vm) if vm else "",
        pids=_ints(m["pid"]) if "pid" in m else (), pgids=_ints(m["pgid"]) if "pgid" in m else (),
        pidfiles=_strs(m["pidfile"]) if "pidfile" in m else (),
        containers=_strs(m["container"]) if "container" in m else (),
        prefixes=_strs(m["container_prefix"]) if "container_prefix" in m else (),
        progress=prog, total=float(total) if total is not None else None, started=_time(raw.get("started")),
        detail=detail if isinstance(detail, str) else "", state=state, unit=unit.strip())


def load_descriptors(folder: Path | None) -> tuple[list[Descriptor], list[str]]:
    if folder is None or not folder.is_dir():
        return [], []
    out, errors = [], []
    for path in sorted(folder.glob("*.json")):
        try:
            out.append(parse_descriptor(path.stem, json.loads(path.read_text())))
        except (OSError, ValueError) as e:  # json.JSONDecodeError is a ValueError
            errors.append(f"{path.name}: {str(e)[:80]}")
    return out, errors


def _field(data, dotted: str):
    for part in dotted.split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def read_progress(spec: dict, t: float) -> tuple[float | None, float | None]:
    """(done, total) from a progress spec; (None, None) when its file is missing, stale or unread.
    {"file": f, "count": "lines", "match": s}: lines (that contain s); {"glob": g}: the files it
    matches; {"file": f, "field": "a.b", "total_field": "c"}: JSON numbers; {"file": f, "regex": r}:
    the last match in the file's end, group 1 done and group 2 (if any) the total. "fresh_min": a
    file (the newest match of a glob) older than that is no progress. "total" sets the total."""
    total = spec.get("total")
    total = float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None
    fresh = spec.get("fresh_min")
    try:
        if "glob" in spec:
            paths = _glob.glob(os.path.expanduser(str(spec["glob"])))
            if fresh is not None and (not paths or t - max(os.stat(p).st_mtime for p in paths) > fresh * 60):
                return None, None
            return float(len(paths)), total
        path = Path(os.path.expanduser(str(spec["file"])))
        st = path.stat()
        if fresh is not None and t - st.st_mtime > fresh * 60:
            return None, None
        if "regex" in spec:
            with open(path, "rb") as fh:
                fh.seek(max(0, st.st_size - 65536))
                tail = fh.read().decode(errors="replace")
            hits = list(re.finditer(str(spec["regex"]), tail))
            if not hits:
                return None, None
            g = hits[-1].groups()
            done = float(g[0]) if g else None
            if len(g) > 1 and g[1] is not None:
                total = float(g[1])
            return done, total
        if "field" in spec:
            data = json.loads(path.read_text())
            done = float(_field(data, str(spec["field"])))
            if "total_field" in spec:
                total = float(_field(data, str(spec["total_field"])))
            return done, total
        if st.st_size > 64 * 1024 * 1024:
            return None, None
        match = spec.get("match")
        with open(path, "rb") as fh:
            needle = str(match).encode() if match else None
            done = sum(1 for line in fh if line.strip() and (needle is None or needle in line))
        return float(done), total
    except (OSError, ValueError, KeyError, IndexError, TypeError, re.error):
        return None, None


def read_state(spec: dict) -> str:
    """A job's state from its state source: the last line in the end of `file` that holds one of
    `when`'s substrings gives that substring's state ("" for running); "" when none does or the
    file cannot be read. A kit's power-pause log, say: {"froze": "frozen (battery)", "thawed": ""}."""
    when = spec["when"]
    try:
        path = Path(os.path.expanduser(str(spec["file"])))
        with open(path, "rb") as fh:
            fh.seek(max(0, path.stat().st_size - 65536))
            lines = fh.read().decode(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        hits = [(line.rfind(k), v) for k, v in when.items() if k in line]
        if hits:
            return max(hits)[1].strip()  # two in one line: the one said last
    return ""


# ── one sample ──────────────────────────────────────────────────────────────────────────────
def descendants(procs: list[Proc], roots: set[int]) -> set[int]:
    kids: dict[int, list[int]] = {}
    for p in procs:
        kids.setdefault(p.ppid, []).append(p.pid)
    out, todo = set(), list(roots)
    while todo:
        pid = todo.pop()
        if pid in out:
            continue
        out.add(pid)
        todo.extend(kids.get(pid, ()))
    return out


def _pidfile_pid(path: str) -> int | None:
    try:
        return int(Path(os.path.expanduser(path)).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def sample(procs: list[Proc], descs: list[Descriptor], *, t: float, min_cores: float, exclude: tuple[str, ...] = (),
           self_pid: int | None = None, homes: list[Path] | None = None) -> tuple[list[ComputeJob], list[DescState]]:
    """(the unclaimed local rows over min_cores, each descriptor's state) from one ps sample."""
    own = descendants(procs, {self_pid}) if self_pid else set()
    live = [p for p in procs if p.pid not in own and not excluded(p, exclude)]
    pids = {p.pid for p in procs}
    pgids = {p.pgid for p in procs}
    vms = vm_names(procs, lima_homes() if homes is None else homes)
    claimed: set[int] = set()
    states = []
    for d in descs:
        roots = set(d.pids) | {pid for pid in map(_pidfile_pid, d.pidfiles) if pid is not None}
        mine = {p.pid for p in live if p.pgid in d.pgids} | (descendants(procs, roots & pids) - own)
        vm = [p for p in procs if d.vm and vms.get(p.pid) == d.vm]
        alive = bool(roots & pids or set(d.pgids) & pgids)
        # A remote job's local side (its driver) is how it says it waits; its CPU is the remote's.
        if d.machine != "local":
            states.append(DescState(d, alive, state=read_state(d.state) if d.state else ""))
            continue
        members = [p for p in live if p.pid in mine] + vm
        claimed |= {p.pid for p in members}
        started = d.started
        if started is None and members:
            started = t - max(p.etime for p in members)
        state = read_state(d.state) if d.state else ""
        # A job its state source says is not running (frozen, waiting) writes no progress: the file's
        # age is then no reason to drop what it last said.
        spec = {k: v for k, v in d.progress.items() if k != "fresh_min"} if d.progress and state else d.progress
        done, total = read_progress(spec, t) if spec else (None, None)
        states.append(DescState(d, alive or bool(vm), cores=sum(p.cores for p in members) if members else None,
                                mem=sum(p.rss for p in members) if members else None, started=started, done=done,
                                total=total if total is not None else d.total, state=state))
    groups: dict[int, list[Proc]] = {}
    rows = []
    for p in live:
        if p.pid in claimed:
            continue
        if is_vm(p):
            if p.cores >= min_cores:
                rows.append(ComputeJob("local", f"VM {vms.get(p.pid, '(unnamed)')}", started=t - p.etime, cores=p.cores,
                                       mem=p.rss, names=(str(p.pid),)))
            continue
        groups.setdefault(p.pgid, []).append(p)
    for pgid, members in groups.items():
        cores = sum(p.cores for p in members)
        if cores < min_cores:
            continue
        top = max(members, key=lambda p: (p.cores, p.etime))
        rows.append(ComputeJob("local", short_command(top.command), started=t - max(p.etime for p in members),
                               cores=cores, mem=sum(p.rss for p in members), names=(f"pgid {pgid}",)))
    rows.sort(key=lambda r: -(r.cores or 0))
    return rows, states


class CpuMeter:
    """Each process's CPU in cores: its CPU time's growth since the last sample, or ps's %cpu for a
    process it has not seen (or one whose pid was reused: a shorter run time than before)."""

    def __init__(self) -> None:
        self.last: dict[int, tuple[float, float]] = {}
        self.last_t: float | None = None

    def measure(self, procs: list[Proc], t: float) -> list[Proc]:
        dt = t - self.last_t if self.last_t is not None else 0.0
        out = []
        for p in procs:
            prev = self.last.get(p.pid)
            if prev is not None and dt >= 0.5 and p.etime + 2 >= prev[1] and p.cpu_s >= prev[0]:
                p = replace(p, cores=(p.cpu_s - prev[0]) / dt)
            out.append(p)
        self.last = {p.pid: (p.cpu_s, p.etime) for p in procs}
        self.last_t = t
        return out


class ComputeSource(Source):
    name = "compute"

    def __init__(self, cfg, store, self_pid: int | None = None, run=None) -> None:
        self.cfg = cfg
        self.store = store
        self.interval_s = cfg.poll_s
        self.self_pid = self_pid
        self.run = run or _run
        self.meter = CpuMeter()

    def poll_once(self) -> None:
        text = self.run(*PS_ARGS, timeout=10)
        t = now()
        if not text:
            return
        procs = self.meter.measure(parse_ps(text), t)
        descs, errors = load_descriptors(self.cfg.descriptors)
        local, states = sample(procs, descs, t=t, min_cores=self.cfg.min_cores, exclude=self.cfg.exclude,
                               self_pid=self.self_pid)
        self.store.set("compute", ComputeSnapshot(t, local, states, errors))


# ── the rows a frame shows ──────────────────────────────────────────────────────────────────
def check_compute_jobs(out) -> None:
    if out is None:
        return
    if not isinstance(out, dict) or not all(isinstance(k, str) and isinstance(v, list) for k, v in out.items()):
        raise TypeError("compute_jobs() must return None or {machine: [ComputeJob, ...]}")
    for jobs in out.values():
        for j in jobs:
            if not isinstance(j, ComputeJob):
                raise TypeError(f"compute_jobs(): a {type(j).__name__} is not a ComputeJob")


class ComputeBook:
    """Across frames: each row's progress samples (its ETA is the rate they show) and the
    descriptor jobs seen this session (one that ends shows `ended` for [compute] ended_min)."""

    def __init__(self) -> None:
        self.samples: dict[str, list[tuple[float, float]]] = {}
        self.seen: dict[str, tuple[ComputeJob, float]] = {}

    def note(self, key: str, t: float, done: float | None) -> list[tuple[float, float]]:
        s = self.samples.setdefault(key, [])
        if done is None:
            return s
        if s and done < s[-1][1]:  # it started again: what went before says nothing of this run
            s.clear()
        if not s or done != s[-1][1] or t - s[-1][0] >= 60:
            s.append((t, done))
        while len(s) > 2 and t - s[1][0] > RATE_WINDOW_S:
            s.pop(0)
        return s

    @staticmethod
    def eta(job: ComputeJob, samples: list[tuple[float, float]], t: float) -> float | None:
        if job.eta is not None:
            return job.eta
        if job.done is None or not job.total or job.done >= job.total:
            return 0.0 if job.done is not None and job.total and job.done >= job.total else None
        rate = None
        if samples:
            t0, d0 = samples[0]
            if job.done > d0 and t - t0 >= RATE_MIN_S:
                rate = (job.done - d0) / (t - t0)
        if rate is None and job.started and job.done > 0 and t - job.started >= RATE_MIN_S:
            rate = job.done / (t - job.started)
        return (job.total - job.done) / rate if rate else None

    def rows(self, snap: ComputeSnapshot | None, reports: dict[str, list[ComputeJob]], t: float, *,
             ended_min: float = 10) -> list[ComputeJob]:
        rows: list[ComputeJob] = []
        claimed: set[int] = set()
        remote = [(m, j) for m, jobs in reports.items() for j in jobs]
        for st in snap.descs if snap else []:
            d = st.d
            key = f"desc:{d.key}"
            if d.machine == "local":
                if not st.alive and st.cores is None:
                    continue
                rows.append(ComputeJob("local", d.label, started=st.started, cores=st.cores, mem=st.mem, done=st.done,
                                       total=st.total, detail=d.detail, key=key, state=st.state, unit=d.unit))
                continue
            hits = [(i, j) for i, (m, j) in enumerate(remote) if m == d.machine and i not in claimed
                    and any(n in d.containers or n.startswith(d.prefixes) for n in j.names)]
            done, total = read_progress(d.progress, t) if d.progress else (None, None)
            for i, j in hits:
                claimed.add(i)
                detail = " · ".join(x for x in (d.detail, j.label, j.detail) if x)
                rows.append(replace(j, machine=d.machine, label=d.label, detail=detail, key=f"{key}:{j.label}",
                                    done=done if done is not None else j.done,
                                    total=(total or d.total) if done is not None else j.total if j.total else d.total,
                                    started=j.started or d.started, state=st.state or j.state, unit=d.unit or j.unit))
            if not hits and st.alive and d.machine in reports:
                rows.append(ComputeJob(d.machine, d.label, started=d.started, done=done, total=total or d.total,
                                       detail=d.detail, waiting=True, key=key, state=st.state, unit=d.unit))
        rows += [replace(j, key=f"auto:{j.label}") for j in (snap.local if snap else [])]
        rows += [replace(j, machine=m, key=j.key or f"{m}:{j.label}:{j.detail}") for i, (m, j) in enumerate(remote)
                 if i not in claimed]
        # The descriptors' jobs that ended since this session saw them.
        live_keys = {r.key for r in rows}
        for r in rows:
            if r.key.startswith("desc:"):
                self.seen[r.key] = (r, t)
        for k, (r, last) in list(self.seen.items()):
            if k in live_keys:
                continue
            if t - last > ended_min * 60:
                del self.seen[k]
            elif not any(x.key == k for x in rows):
                rows.append(replace(r, ended=last, cores=None, mem=None, waiting=False, state=""))
        # This computer's rows first, then each remote's together, in the order they first came.
        order = {m: i for i, m in enumerate(dict.fromkeys(["local"] + [r.machine for r in rows]))}
        rows.sort(key=lambda r: order[r.machine])
        # Each row's finish: its own source's, else the rate its progress shows.
        return [r if r.ended else replace(r, eta=self.eta(r, self.note(r.key, t, r.done), t)) for r in rows]

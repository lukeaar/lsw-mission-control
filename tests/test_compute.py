"""Compute jobs (sources/compute.py, render/compute.py): ps parsing, the CPU meter, descriptors,
VMs, plugin rows, ETAs, and the rows at the foot of Other work in progress."""

from __future__ import annotations

import json
import os

import pytest

from lsw_mission_control import testing
from lsw_mission_control.config import ConfigError
from lsw_mission_control.render.other import other_panel
from lsw_mission_control.sources.compute import (
    ComputeBook,
    ComputeJob,
    ComputeSnapshot,
    ComputeSource,
    CpuMeter,
    Descriptor,
    DescState,
    Proc,
    excluded,
    load_descriptors,
    parse_clock,
    parse_descriptor,
    parse_ps,
    read_progress,
    sample,
    short_command,
    vm_names,
)

from conftest import NOW
from golden_util import check
from scenarios import HOUR, MIN, Project, midway

GIB = 2**30
VM = "/System/Library/Frameworks/Virtualization.framework/Versions/A/XPCServices/com.apple.Virtualization.VirtualMachine.xpc/Contents/MacOS/com.apple.Virtualization.VirtualMachine"  # noqa: E501
PS = f"""\
    1     1     0   1.2  10:00.00   17472 06-09:59:02 /sbin/launchd
  422   422     1  28.0  55:00.00   59584 06-09:58:36 /System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer -daemon
 5027  4945  4958  95.0  80:00.00  951728 06-09:37:02 /Users/x/.vscode/extensions/some.ext/claude --output-format stream-json
 4952  4945  4945 120.0  30:00.00  541888 06-09:37:05 /Applications/Code.app/Contents/Frameworks/Code Helper (Renderer).app/Contents/MacOS/Code Helper (Renderer) --type=renderer
33189 33189     1 397.0 1100:00.00 2748832    04:55:50 {VM}
32425 32425     1   0.1   1:00.00  193840    04:59:25 {VM}
32418 32418     1   0.0   0:01.00   20000    04:59:25 /opt/homebrew/bin/limactl hostagent --pidfile /Users/x/.colima/_lima/colima/ha.pid
33183 33183     1   0.0   0:01.00   20000    04:55:50 /opt/homebrew/bin/limactl hostagent --pidfile /Users/x/.colima/_lima/colima-work/ha.pid
72171 72171  5027   0.0   0:00.01    3000       12:00 /bin/zsh -c source /Users/x/.claude/shell-snapshots/snap.sh
72190 72171 72171 180.0  21:30.00  600000       11:58 /opt/homebrew/Cellar/python@3.14/3.14.0/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python -m pytest -q tests/test_slow.py
72191 72171 72190  20.0   2:00.00  100000       11:00 /usr/bin/tesseract /tmp/page.png stdout
 8000  8000     1  30.0   0:30.00   50000       01:00 bash nightly.sh
 8001  8000  8000  20.0   0:20.00   40000       00:59 python3 import.py
"""  # noqa: E501 — real ps lines, placeholders for the paths


def procs():
    return parse_ps(PS.format(VM=VM))


def homes(tmp_path):
    lima = tmp_path / "_lima"
    for inst, pid in (("colima", 32418), ("colima-work", 33183)):
        (lima / inst).mkdir(parents=True, exist_ok=True)
        (lima / inst / "ha.pid").write_text(f"{pid}\n")
    return [lima]


def test_ps_times_and_lines():
    assert parse_clock("1:23.45") == pytest.approx(83.45)
    assert parse_clock("02:03:04") == 7384
    assert parse_clock("1-00:00:10") == 86410
    ps = procs()
    assert len(ps) == 13
    p = next(p for p in ps if p.pid == 72190)
    assert (p.pgid, p.ppid, p.cores, p.etime) == (72171, 72171, 1.8, 718)
    assert p.rss == 600000 * 1024 and p.cpu_s == 1290
    assert parse_ps("garbage\n  1 2 3\n") == []


def test_what_is_never_a_job():
    by = {p.pid: p for p in procs()}
    assert excluded(by[1]) and excluded(by[422]) and excluded(by[5027]) and excluded(by[4952])
    assert not excluded(by[33189]), "a VM is under /System/ but is the work"
    assert not excluded(by[72190]) and not excluded(by[8001])
    assert excluded(by[8001], ("import.py",)), "[compute] exclude"
    assert short_command(by[72190].command) == "python -m pytest -q test_slow.py"
    assert short_command("bash " + "x" * 100, 20) == "bash " + "x" * 14 + "…"


def test_vms_are_named_by_their_lima_instance(tmp_path):
    assert vm_names(procs(), homes(tmp_path)) == {33189: "colima work", 32425: "colima"}
    assert vm_names(procs(), [tmp_path / "none"]) == {}


def test_a_sample_groups_claims_and_thresholds(tmp_path):
    d_pg = Descriptor("nightly", "Nightly import", pgids=(8000,), progress=None, total=10)
    d_vm = Descriptor("vmjob", "Work in the VM", pids=(999999,), vm="colima work")
    d_remote = Descriptor("far", "Render pass", machine="buildbox", prefixes=("render-",), pids=(8001,))
    rows, states = sample(procs(), [d_pg, d_vm, d_remote], t=NOW, min_cores=1.0, homes=homes(tmp_path))
    # the pytest group (the shell, python, tesseract: 2.0 cores) by its heaviest command; the
    # claimed VM and the descriptor's group are not rows of their own; the idle VM is under 1 core
    assert [(r.label, round(r.cores, 2)) for r in rows] == [("python -m pytest -q test_slow.py", 2.0)]
    assert rows[0].started == NOW - 720 and rows[0].mem == (3000 + 600000 + 100000) * 1024
    pg, vm, far = states
    assert pg.alive and pg.cores == pytest.approx(0.5) and pg.started == NOW - 60 and pg.total == 10
    assert vm.alive and vm.cores == pytest.approx(3.97) and vm.started == NOW - parse_clock("04:55:50")
    assert far.alive and far.cores is None, "a remote job's local match is its driver: its CPU is the remote's"
    rows, _ = sample(procs(), [], t=NOW, min_cores=1.0, homes=homes(tmp_path))
    assert [r.label for r in rows] == ["VM colima work", "python -m pytest -q test_slow.py"]
    rows, _ = sample(procs(), [], t=NOW, min_cores=1.0, homes=homes(tmp_path), self_pid=72171)
    assert [r.label for r in rows] == ["VM colima work"], "the dashboard's own children are not jobs"


def test_the_cpu_meter_reads_cpu_time_growth():
    m = CpuMeter()
    a = [Proc(10, 10, 1, 0.5, 100.0, 0, 50, "x")]
    assert m.measure(a, NOW)[0].cores == 0.5, "first sight: ps's own %cpu"
    b = [Proc(10, 10, 1, 0.5, 130.0, 0, 60, "x")]
    assert m.measure(b, NOW + 10)[0].cores == pytest.approx(3.0)
    reused = [Proc(10, 10, 1, 0.2, 1.0, 0, 3, "y")]
    assert m.measure(reused, NOW + 20)[0].cores == 0.2, "a reused pid starts again"


def test_descriptors_load_and_say_why_not(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"label": "A", "match": {"pgid": [1, 2]}, "started": "2026-09-21T14:00:00Z",
                                                 "vm": "work", "total": 5}))
    (tmp_path / "b.json").write_text("{nope")
    (tmp_path / "c.json").write_text(json.dumps({"label": "C"}))
    (tmp_path / "d.json").write_text(json.dumps({"label": "D", "match": {"host": "x"}}))
    descs, errors = load_descriptors(tmp_path)
    assert [d.key for d in descs] == ["a"]
    assert descs[0].pgids == (1, 2) and descs[0].vm == "colima work" and descs[0].started == 1789999200.0
    assert [e.split(":")[0] for e in errors] == ["b.json", "c.json", "d.json"]
    assert "nothing to match" in errors[1] and "unknown match key host" in errors[2]
    assert load_descriptors(tmp_path / "missing") == ([], [])
    assert parse_descriptor("v", {"label": "V", "vm": "default"}).vm == "colima"
    with pytest.raises(ValueError):
        parse_descriptor("p", {"label": "P", "match": {"pid": "12"}})


def test_progress_kinds(tmp_path):
    log = tmp_path / "p.jsonl"
    log.write_text('{"unit": 1}\n{"unit": 2}\n\n{"stage": "x"}\n')
    assert read_progress({"file": str(log), "count": "lines"}, NOW) == (3, None)
    assert read_progress({"file": str(log), "count": "lines", "match": "unit", "total": 9}, NOW) == (2, 9)
    (tmp_path / "out").mkdir()
    for i in range(4):
        (tmp_path / "out" / f"{i}.json").write_text("{}")
    assert read_progress({"glob": str(tmp_path / "out" / "*.json"), "total": 8}, NOW + 1e9)[0] == 4
    st = tmp_path / "state.json"
    st.write_text(json.dumps({"run": {"done": 7, "of": 20}}))
    assert read_progress({"file": str(st), "field": "run.done", "total_field": "run.of"}, NOW) == (7, 20)
    beat = tmp_path / "liveness.txt"
    beat.write_text("t key=a units=1/174\nt key=a units=3/174 FAIL=0\n")
    assert read_progress({"file": str(beat), "regex": r"units=(\d+)/(\d+)"}, NOW) == (3, 174)
    os.utime(beat, (NOW - 3600, NOW - 3600))
    assert read_progress({"file": str(beat), "regex": r"units=(\d+)/(\d+)", "fresh_min": 15}, NOW) == (None, None)
    assert read_progress({"file": str(tmp_path / "none")}, NOW) == (None, None)
    assert read_progress({"file": str(st), "field": "run.nope"}, NOW) == (None, None)


def test_the_source_writes_one_snapshot(tmp_path):
    from lsw_mission_control.config import ComputeCfg
    from lsw_mission_control.store import Store

    (tmp_path / "j.json").write_text(json.dumps({"label": "Nightly import", "match": {"pgid": 8000}}))
    store = Store()
    src = ComputeSource(ComputeCfg(descriptors=tmp_path), store, run=lambda *a, **k: PS.format(VM=VM))
    src.poll_once()
    snap = store.get("compute")
    assert [s.d.label for s in snap.descs] == ["Nightly import"] and snap.descs[0].alive
    assert any(r.label.startswith("python -m pytest") for r in snap.local)
    store.set("compute", None)
    ComputeSource(ComputeCfg(), store, run=lambda *a, **k: "").poll_once()
    assert store.get("compute") is None, "ps that did not answer keeps the last sample"


def remote_report(**over):
    job = ComputeJob("", "render pass 7", started=NOW - 3 * HOUR, cores=4.0, mem=2.4 * GIB, done=5, total=156,
                     detail="stage 6", names=("render-7-0", "render-7-1"), as_of=NOW - 30)
    return {"buildbox": [job, ComputeJob("", "transcode", started=NOW - HOUR, cores=0.8, mem=3e8, names=("tx",),
                                         as_of=NOW - 10 * MIN)], **over}


def snapshot(alive=True):
    return ComputeSnapshot(NOW, local=[ComputeJob("local", "VM colima", started=NOW - 6 * HOUR, cores=2.0, mem=4 * GIB),
                                       ComputeJob("local", "python -m pytest -q tests", started=NOW - 12 * MIN,
                                                  cores=1.2, mem=6e8)],
                           descs=[DescState(Descriptor("nightly", "Nightly data import", pgids=(1,)), alive,
                                            cores=3.9 if alive else None, mem=2.6 * GIB if alive else None,
                                            started=NOW - 5 * HOUR, done=40, total=174),
                                  DescState(Descriptor("render", "Render farm pass", machine="buildbox",
                                                       prefixes=("render-",)), False),
                                  DescState(Descriptor("weekly", "Weekly rebuild", machine="buildbox",
                                                       prefixes=("rebuild-",), pids=(2,)), True)],
                           errors=["broken.json: no label"])


def test_rows_merge_descriptors_plugins_and_rates():
    book = ComputeBook()
    rows = book.rows(snapshot(), remote_report(), NOW)
    by = {(r.machine, r.label, r.detail): r for r in rows}
    assert list(by) == [("local", "Nightly data import", ""), ("local", "VM colima", ""),
                        ("local", "python -m pytest -q tests", ""),
                        ("buildbox", "Render farm pass", "render pass 7 · stage 6"),
                        ("buildbox", "Weekly rebuild", "waiting"), ("buildbox", "transcode", "")]
    # no samples yet: the whole run's rate (40 in 5 h: 134 left at 8 an hour)
    assert by["local", "Nightly data import", ""].eta == pytest.approx(134 / 8 * HOUR)
    assert by["buildbox", "Render farm pass", "render pass 7 · stage 6"].eta == pytest.approx(151 / (5 / (3 * HOUR)))
    assert by["buildbox", "Weekly rebuild", "waiting"].waiting
    # 30 min on, 10 more done: the recent rate (20 an hour) wins
    snap = snapshot()
    snap.descs[0].done = 50
    rows = book.rows(snap, remote_report(), NOW + 30 * MIN)
    assert rows[0].eta == pytest.approx(124 / 20 * HOUR)
    # a machine the plugin did not answer for: its descriptor's driver says nothing of waiting
    rows = book.rows(snapshot(), {}, NOW + 31 * MIN)
    assert not any(r.waiting for r in rows)
    # the job ends: "ended" for ended_min, then gone
    rows = book.rows(snapshot(alive=False), {}, NOW + 32 * MIN, ended_min=10)
    ended = [r for r in rows if r.ended]
    assert [r.label for r in ended] == ["Nightly data import", "Render farm pass", "Weekly rebuild"]
    assert ended[0].ended == NOW + 31 * MIN and ended[0].cores is None
    assert not [r for r in book.rows(snapshot(alive=False), {}, NOW + 45 * MIN) if r.ended]
    assert book.rows(None, {}, NOW) == []


def compute_project(tmp_path, compute: str = ""):
    p = Project(tmp_path)
    midway(p)
    if compute:
        cfg = p.dot / "mission-control.toml"
        cfg.write_text(cfg.read_text() + "\n" + compute)
    p.store["compute"] = snapshot()
    p.plugin_state["compute_jobs"] = remote_report()
    return p


@pytest.mark.parametrize("width", (80, 100, 120, 160))
def test_other_panel_lists_the_compute_jobs(tmp_path, update_golden, width):
    p = compute_project(tmp_path, '[compute]\nlocal_name = "laptop"\n')
    engine = p.engine()
    f = engine.build_frame(width)
    plain, styled = testing.render_text(other_panel(f, width), width)
    check(f"other/compute-{width}.txt", plain, update_golden)
    check(f"other/compute-{width}.ansi", styled, update_golden)
    assert "compute jobs" in plain and "laptop" in plain and "not read: broken.json: no label" in plain
    assert max(len(line) for line in plain.splitlines()) <= width


def test_nothing_heavy_draws_nothing(tmp_path):
    p = Project(tmp_path)
    midway(p)
    plain, _ = testing.render_text(other_panel(p.engine().build_frame(120), 120), 120)
    assert "compute jobs" not in plain


def test_a_plugin_that_fails_its_compute_jobs_shows_in_its_card(tmp_path):
    p = compute_project(tmp_path)
    p.plugin_state["fail"] = "bad-compute"
    engine = p.engine()
    f = engine.build_frame(120)
    assert any("not a ComputeJob" in m for m in f.plugin_errors["stub"])
    assert [r.machine for r in f.compute if r.machine != "local"] == [], "it knows nothing: none even waits"


def test_config(tmp_path):
    p = Project(tmp_path)
    midway(p)
    cfg = p.dot / "mission-control.toml"
    base = cfg.read_text()
    from lsw_mission_control.config import load_config

    c = load_config(cfg).compute
    assert c.enabled and c.descriptors is None and c.min_cores == 1.0 and c.local_name == "here"
    cfg.write_text(base + '\n[compute]\ndescriptors = "~/jobs"\nmin_cores = 2\nexclude = ["render"]\n')
    c = load_config(cfg).compute
    assert c.descriptors == tmp_path / "home" / "jobs" and c.min_cores == 2.0 and c.exclude == ("render",)
    for bad in ('min_cores = 0', 'poll_s = 1', 'nope = 1', 'exclude = "x"'):
        cfg.write_text(base + f"\n[compute]\n{bad}\n")
        with pytest.raises(ConfigError):
            load_config(cfg)

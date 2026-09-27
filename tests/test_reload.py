from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from lsw_mission_control.reload import Reloader, user_args

OK = 'import sys\nprint("28/28 network cases correct")\nprint("7/7 indicator cases correct")\n'


def setup(tmp_path: Path, launcher_text: str = OK):
    launcher = tmp_path / ".claude" / "status.py"
    launcher.parent.mkdir()
    launcher.write_text(launcher_text)
    engine = tmp_path / "engine" / "mod.py"
    engine.parent.mkdir()
    engine.write_text("X = 1\n")
    past = time.time() - 100
    for f in (launcher, engine):
        os.utime(f, (past, past))
    return launcher, engine


def touch(path: Path, text: str | None = None, age: float = 10.0) -> None:
    if text is not None:
        path.write_text(text)
    t = time.time() - age
    os.utime(path, (t, t))


def test_nothing_changed(tmp_path):
    launcher, engine = setup(tmp_path)
    assert Reloader([engine], launcher, ["--launcher", str(launcher)]).poll() == "none"


def test_waits_for_the_edit_to_settle(tmp_path):
    launcher, engine = setup(tmp_path)
    r = Reloader([engine], launcher, [])
    touch(engine, "X = 2\n", age=0.5)
    assert r.poll() == "wait"
    touch(engine, age=3)
    assert r.poll() == "go"


def test_a_syntax_error_is_refused_and_only_the_next_edit_retries(tmp_path):
    launcher, engine = setup(tmp_path)
    r = Reloader([engine], launcher, [], package_dir=engine.parent)
    touch(engine, "def broken(:\n")
    assert r.poll() == "rejected" and r.note == "edit to engine/mod.py not loaded: SyntaxError, line 1"
    assert r.poll() == "none" and r.note  # the note stays until a reload succeeds
    touch(engine, "X = 3\n", age=5)
    assert r.poll() == "go"


def test_a_launcher_edit_reads_as_before(tmp_path):
    launcher, engine = setup(tmp_path)
    r = Reloader([engine], launcher, [])
    touch(launcher, "import sys\nsys.stderr.write('Traceback\\nNameError: name x is not defined\\n')\nsys.exit(1)\n")
    assert r.poll() == "rejected" and r.note == "edit to status.py not loaded: NameError: name x is not defined"


def test_the_contract_phrase_is_not_enough_exit_0_is_required(tmp_path):
    launcher, engine = setup(tmp_path)
    r = Reloader([engine], launcher, [])
    touch(launcher, 'import sys\nprint("MISMATCH want=\'git push\' got=None: git push")\n'
                    'print("27/28 network cases correct")\nsys.exit(1)\n')
    assert r.poll() == "rejected" and r.note.endswith("MISMATCH want='git push' got=None: git push")


def test_a_silent_failure(tmp_path):
    launcher, engine = setup(tmp_path)
    r = Reloader([engine], launcher, [])
    touch(launcher, "import sys\nsys.exit(3)\n")
    assert r.poll() == "rejected" and r.note == "edit to status.py not loaded: it did not start"


def test_new_engine_files_count(tmp_path):
    launcher, engine = setup(tmp_path)
    files = [engine]
    r = Reloader(files, launcher, [], rescan=lambda: files)
    new = engine.parent / "new.py"
    touch(new, "Y = 1\n")
    files.append(new)
    assert r.poll() == "go"


def test_the_engine_watches_only_module_files(tmp_path, monkeypatch):
    import lsw_mission_control.engine as eng

    from scenarios import Project

    pkg = tmp_path / "pkg"
    (pkg / "render").mkdir(parents=True)
    for name in ("a.py", "render/b.py", ".#a.py", "a (1).py", "render/.hidden.py", "not a dir/c.py"):
        (pkg / name).parent.mkdir(parents=True, exist_ok=True)
        (pkg / name).write_text("X = 1\n")
    monkeypatch.setattr(eng, "PACKAGE_DIR", pkg)
    files = Project(tmp_path).engine().watched_files()
    assert [f.relative_to(pkg).as_posix() for f in files if f.is_relative_to(pkg)] == ["a.py", "render/b.py"]


def test_exec_goes_through_the_launcher(tmp_path, monkeypatch):
    launcher, engine = setup(tmp_path)
    seen = {}
    monkeypatch.setattr(os, "execv", lambda path, argv: seen.update(path=path, argv=argv))
    r = Reloader([engine], launcher, ["--launcher", str(launcher), "--no-server"], legacy_scroll_env="OLD_SCROLL")
    r.exec(12)
    assert seen == {"path": sys.executable, "argv": [sys.executable, str(launcher), "--no-server"]}
    assert os.environ["LSW_MC_SCROLL"] == "12" and os.environ["OLD_SCROLL"] == "12"
    monkeypatch.delenv("LSW_MC_SCROLL")
    monkeypatch.delenv("OLD_SCROLL")
    r2 = Reloader([engine], None, ["--project", "/x"])
    assert r2.exec_argv() == [sys.executable, "-m", "lsw_mission_control", "--project", "/x"]
    assert r2.gate_command()[-2:] == ["--check-net", "--self-check"]


@pytest.mark.parametrize("argv, want", [
    (["--launcher", "/a/status.py", "--once"], ["--once"]),
    (["--launcher=/a/status.py", "x"], ["x"]),
    (["--no-server"], ["--no-server"]),
])
def test_user_args(argv, want):
    assert user_args(argv) == want

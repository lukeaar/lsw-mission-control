"""Golden files: tests/golden/<name>. `pytest --update-golden` rewrites them; review every changed
line before committing (git diff --word-diff tests/golden)."""

from __future__ import annotations

import difflib
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden"


def check(name: str, text: str, update: bool) -> None:
    path = GOLDEN / name
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return
    assert path.exists(), f"no golden {name}: run pytest --update-golden and review it"
    want = path.read_text()
    if want != text:
        diff = "\n".join(list(difflib.unified_diff(want.splitlines(), text.splitlines(), "golden", "now", lineterm="", n=1))[:60])
        raise AssertionError(f"{name} differs from its golden:\n{diff}")

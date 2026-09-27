"""The notes Claude keeps by hand (docs/plan-schema.md): what is waiting on the owner, and
work in motion that no plan row covers. Re-read whenever the file changes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Notes:
    waiting_on_owner: tuple[str, ...] = ()
    in_progress_elsewhere: tuple[str, ...] = ()
    mtime: float | None = None  # None: there is no notes file (or it cannot be read)


def notes_from(data: dict, mtime: float | None) -> Notes:
    """The notes of a parsed file; their two lists always come back as lists of strings."""
    lists = []
    for key in ("waiting_on_owner", "in_progress_elsewhere"):
        v = data.get(key)
        lists.append(tuple(str(x) for x in v) if isinstance(v, list) else ((str(v),) if v else ()))
    return Notes(lists[0], lists[1], mtime)


def load_notes(path: Path) -> Notes:
    """The notes, or an exception saying why the file does not load (OSError, ValueError)."""
    data, mtime = json.loads(path.read_text()), path.stat().st_mtime
    if not isinstance(data, dict):
        raise ValueError("it must hold a JSON object")
    return notes_from(data, mtime)


def read_notes(path: Path) -> Notes:
    """The notes, or empty ones when the file is missing or does not load."""
    try:
        return load_notes(path)
    except (OSError, ValueError):
        return Notes()


class NotesLoader:
    """The last good notes, re-read when the file's mtime changes. A file that does not load keeps
    the last good notes and says why in `note` (a typo must never read as "nothing is waiting on
    you"); a missing file is simply no notes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.notes = Notes()
        self.note = ""
        self._mtime: float | None = None

    def refresh(self) -> Notes:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self.notes, self.note, self._mtime = Notes(), "", None
            return self.notes
        if mtime == self._mtime:
            return self.notes
        self._mtime = mtime
        try:
            notes = load_notes(self.path)
        except (OSError, ValueError) as e:
            self.note = f"{self.path.name} not loaded ({type(e).__name__}: {e})"[:160]
            return self.notes
        self.notes, self.note = notes, ""
        return notes

"""The thread-safe state the background sources write and each frame reads."""

from __future__ import annotations

import threading


class Store:
    """Keys the engine's sources write: git, release_gh, gh_timing, tokens, tokens_by_model.
    Each plugin gets a Store of its own."""

    def __init__(self, initial: dict | None = None) -> None:
        self.lock = threading.Lock()
        self._data: dict = dict(initial or {})

    def get(self, key: str, default=None):
        with self.lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self.lock:
            self._data[key] = value

    def update(self, **kv) -> None:
        with self.lock:
            self._data.update(kv)

    @property
    def data(self) -> dict:
        """The live dict, for a change that must be atomic: use it only inside `with store.lock:`."""
        return self._data

    def snapshot(self) -> dict:
        """A shallow copy for one frame."""
        with self.lock:
            return dict(self._data)

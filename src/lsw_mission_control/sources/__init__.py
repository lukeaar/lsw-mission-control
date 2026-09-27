"""Background sources: each polls once, then again every `interval_s`, in a daemon thread.
A failed call never raises and keeps the last answer."""

from __future__ import annotations

import threading
import time


class Source:
    name = "source"
    interval_s: float = 60.0

    def poll_once(self) -> None:
        raise NotImplementedError

    def next_sleep(self) -> float:
        return self.interval_s

    def loop(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 — a source must never die; it tries again next round
                pass
            time.sleep(self.next_sleep())

    def start(self) -> None:
        threading.Thread(target=self.loop, name=f"lsw-mc-{self.name}", daemon=True).start()

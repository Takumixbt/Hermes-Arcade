"""The few browser/JS primitives the Chromium dino code leans on.

Keeping these tiny and literal is what makes the port auditable: every call
site reads the same as the TypeScript it came from.
"""

from __future__ import annotations

import math
from typing import Callable


def js_round(x: float) -> int:
    """``Math.round``: ties go toward +infinity (Python's round() is banker's)."""
    return int(math.floor(x + 0.5))


def js_floor(x: float) -> int:
    return int(math.floor(x))


def js_ceil(x: float) -> int:
    return int(math.ceil(x))


class Scheduler:
    """``requestAnimationFrame`` plus a clock, driven by whoever owns the loop.

    ``now`` is what ``performance.now()`` would return. The owner sets it
    before delivering input or running a frame, so event handlers see the time
    of the most recent tick, exactly as a browser's frame clock behaves.
    """

    def __init__(self, random: Callable[[], float]) -> None:
        self.now = 0.0
        self.random = random
        self._next_id = 1
        self._pending: dict[int, Callable[[float], None]] = {}
        self._batch: dict[int, Callable[[float], None]] = {}

    def get_time_stamp(self) -> float:
        return self.now

    def get_random_num(self, lo: int, hi: int) -> int:
        return int(math.floor(self.random() * (hi - lo + 1))) + lo

    def request_animation_frame(self, callback: Callable[[float], None]) -> int:
        rid = self._next_id
        self._next_id += 1
        self._pending[rid] = callback
        return rid

    def cancel_animation_frame(self, rid: int) -> None:
        self._pending.pop(rid, None)
        self._batch.pop(rid, None)

    def run_frame(self, now: float) -> None:
        """Run every callback registered before this frame, in order.

        Callbacks registered while the frame runs wait for the next one, and a
        callback cancelled by an earlier one in the same frame never fires.
        """
        self.now = now
        self._batch = self._pending
        self._pending = {}
        while self._batch:
            rid = next(iter(self._batch))
            callback = self._batch.pop(rid)
            callback(now)

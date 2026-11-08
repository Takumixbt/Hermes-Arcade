"""Chrome's offline dinosaur as a Console game.

``DinoGame`` wraps the ported ``Runner`` with the few things the browser
supplies around it: the page colour, the CSS filter transition that turns the
page black at night, and the 44 px -> 600 px container the canvas lives in.
"""

from __future__ import annotations

import random
from typing import Callable

from ...catalog import register
from ...gfx.canvas import Canvas
from ..base import Frame, Game
from .constants import DEFAULT_HEIGHT, DEFAULT_WIDTH, INVERT_TRANSITION_MS
from .cssmath import invert_curve
from .jsrt import Scheduler
from .runner import Runner
from .sprites import load_sheet

_KEYCODES = {"space": 32, "up": 38, "down": 40, "enter": 13}
_INVERT_LUTS: dict[int, bytes] = {}

# The canvas is a grey plane, but Frame carries a palette. Reserve three grey
# levels for the Hermes cap and bolts so they render in gold without touching
# the renderer or the sprite colours around them.
_HERMES_GOLD = {205: (198, 160, 52), 225: (238, 201, 94), 245: (255, 245, 205)}


def _palette() -> tuple:
    pal = [(g, g, g) for g in range(256)]
    for level, rgb in _HERMES_GOLD.items():
        pal[level] = rgb
    return tuple(pal)


_PALETTE = _palette()


def _invert_lut(amount: float) -> bytes:
    """CSS ``filter: invert(amount)`` on a grey level."""
    key = int(round(amount * 255))
    lut = _INVERT_LUTS.get(key)
    if lut is None:
        a = key / 255
        lut = bytes(int(g * (1 - a) + (255 - g) * a + 0.5) for g in range(256))
        _INVERT_LUTS[key] = lut
    return lut


@register("dino")
class DinoGame(Game):
    name = "dino"
    title = "Chrome Dino"
    width = DEFAULT_WIDTH
    height = DEFAULT_HEIGHT

    def __init__(
        self,
        *,
        seed: int | None = None,
        high_score: int = 0,
        on_high_score: Callable[[int], None] | None = None,
        on_sound: Callable[[str], None] | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.sched = Scheduler(self.rng.random)
        self.canvas = Canvas(load_sheet(), DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.runner = Runner(self.canvas, self.sched, on_high_score=on_high_score, on_sound=on_sound)
        self._inv_from = 0.0
        self._inv_to = 0.0
        self._inv_start = 0.0
        self._started = False
        self._high_score = high_score

    @classmethod
    def create(cls) -> "DinoGame":
        from ...state import load_high_score, save_high_score

        from .sound import Sounds

        return cls(
            high_score=load_high_score("dino"),
            on_high_score=lambda score: save_high_score("dino", score),
            on_sound=Sounds().play,
        )

    # -- lifecycle ----------------------------------------------------------

    def _ensure_started(self, now_ms: float) -> None:
        if not self._started:
            self._started = True
            self.sched.now = now_ms
            self.runner.start()
            # Chrome's error page always reports the stored score (0 on a fresh profile),
            # which is what makes "HI 00000" appear.
            self.runner.initialize_high_score(self._high_score)

    def tick(self, now_ms: float) -> None:
        self._ensure_started(now_ms)
        self.runner.frame(now_ms)

    def handle_key(self, key: str, pressed: bool, now_ms: float | None = None) -> None:
        code = _KEYCODES.get(key)
        if code is None:
            return
        if now_ms is not None:
            self._ensure_started(now_ms)
            self.sched.now = now_ms
        if pressed:
            self.runner.on_key_down(code)
        else:
            self.runner.on_key_up(code)

    def pause(self, now_ms: float) -> None:
        self.sched.now = now_ms
        self.runner.on_visibility_change(True)

    def resume(self, now_ms: float) -> None:
        self.sched.now = now_ms
        self.runner.on_visibility_change(False)

    def hint(self) -> str:
        return "Space / Up jump    Down duck    Esc leave"

    # -- picture ------------------------------------------------------------

    def invert_amount(self, now_ms: float) -> float:
        """Current strength of the page's ``invert()`` filter (0 = day, 1 = night)."""
        target = 1.0 if self.runner.html_inverted else 0.0
        elapsed = now_ms - self._inv_start
        t = invert_curve(min(1.0, max(0.0, elapsed / INVERT_TRANSITION_MS)))
        current = self._inv_from + (self._inv_to - self._inv_from) * t
        if target != self._inv_to:
            # Retargeted: the transition restarts from wherever it currently is.
            self._inv_from = current
            self._inv_to = target
            self._inv_start = now_ms
            return current
        return current

    def frame(self, now_ms: float) -> Frame:
        self._ensure_started(now_ms)
        amount = self.invert_amount(now_ms)
        width, height = self.canvas.width, self.canvas.height
        plane = bytearray(self.canvas.plane)
        visible = min(width, max(1, int(self.runner.container_width(now_ms) + 0.5)))
        lut = _invert_lut(amount) if amount > 0 else None
        if amount > 0:
            plane = plane.translate(lut)
        if visible < width:
            # Only the container's width of the canvas is on the page.
            plane = bytearray(b"".join(plane[y * width : y * width + visible] for y in range(height)))
        page = lut[255] if lut else 255
        return Frame(visible, height, bytes(plane), _PALETTE, page)

    @property
    def high_score(self) -> int:
        return self.runner.highest_score

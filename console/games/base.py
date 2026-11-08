"""What every Console game provides.

A game owns its own clock-driven logic and hands the terminal layer a finished
picture each frame. The terminal layer never needs to know the rules, and a
game never needs to know how pixels reach the screen.
"""

from __future__ import annotations

from dataclasses import dataclass


_GRAY = tuple((g, g, g) for g in range(256))


@dataclass
class Frame:
    """An indexed-colour picture: ``pixels[y * width + x]`` indexes ``palette``.

    Palette indices should mean the same colour from frame to frame (the dino
    uses the grey level itself); the sixel encoder caches finished bands.
    """

    width: int
    height: int
    pixels: bytes
    palette: tuple  # ((r, g, b), ...), at most 256 entries
    page: int = 255  # palette index of the "page" colour around the picture

    @classmethod
    def from_gray(cls, width: int, height: int, gray: bytes, page_gray: int = 255) -> "Frame":
        """A frame whose pixels are grey levels (index == level)."""
        return cls(width, height, gray, _GRAY, page_gray)


class Game:
    """Base class. Subclasses register themselves via ``console.catalog.register``."""

    name = "game"
    title = "Game"
    #: Native picture size in pixels; the terminal layer scales by whole numbers.
    width = 600
    height = 150

    @classmethod
    def create(cls) -> "Game":
        """A fresh game wired to Console's saved state (high scores and the like)."""
        return cls()

    def handle_key(self, key: str, pressed: bool, now_ms: float) -> None:  # pragma: no cover - interface
        """``key`` is one of: space up down left right enter, or a single character."""

    def tick(self, now_ms: float) -> None:  # pragma: no cover - interface
        """Advance the game to ``now_ms`` (a monotonic clock in milliseconds)."""

    def frame(self, now_ms: float) -> Frame:  # pragma: no cover - interface
        raise NotImplementedError

    def hint(self) -> str:
        """One line of help shown under the picture."""
        return ""

    def pause(self, now_ms: float) -> None:
        """Console is being left. Chrome stops the game when its tab loses focus."""

    def resume(self, now_ms: float) -> None:
        """Console is back."""

    def close(self) -> None:
        """Persist anything worth keeping."""

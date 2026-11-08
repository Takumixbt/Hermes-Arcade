"""Chrome's own 1x sprite sheet (``100-offline-sprite.png``), decoded once."""

from __future__ import annotations

from pathlib import Path

from ...gfx.canvas import SpriteSheet

SHEET_PATH = Path(__file__).with_name("assets") / "offline-sprite-1x.png"

_sheet: SpriteSheet | None = None


def load_sheet() -> SpriteSheet:
    global _sheet
    if _sheet is None:
        _sheet = SpriteSheet.from_png(SHEET_PATH.read_bytes())
    return _sheet

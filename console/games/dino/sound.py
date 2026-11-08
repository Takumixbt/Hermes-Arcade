"""Chrome's three dino sounds (jump, hit, 100-point chime), played on Windows.

The WAVs in ``assets/`` are Chrome's own clips, re-encoded from the page's Ogg
data to small 8-bit files. Elsewhere, or with ``CONSOLE_SOUND=0``, this is
silent. A new sound cuts the previous one off (``winsound`` plays one at a time).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ASSETS = Path(__file__).with_name("assets")
NAMES = {"press": "press.wav", "hit": "hit.wav", "reached": "reached.wav"}


def enabled() -> bool:
    return sys.platform == "win32" and os.environ.get("CONSOLE_SOUND", "1").strip().lower() not in {"0", "false", "no", "off"}


class Sounds:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._on = enabled()

    def play(self, name: str) -> None:
        if not self._on or name not in NAMES:
            return
        try:
            import winsound

            data = self._data.get(name)
            if data is None:
                data = self._data[name] = (ASSETS / NAMES[name]).read_bytes()
            winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            self._on = False  # no audio device, missing file: never let sound break the game

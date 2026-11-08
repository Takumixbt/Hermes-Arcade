"""ANSI truecolour half-block renderer: the arcade without sixel.

VS Code, Cursor and other xterm.js terminals cannot draw sixel at all. This
renderer samples the game's indexed frame down to character cells and paints
each cell with two vertical pixels (upper half block, foreground over
background), which every truecolor terminal understands.

It is a fallback, not a replacement: sixel keeps the exact pixel grid and is
what Windows Terminal / WezTerm should use. Text mode trades resolution for
working everywhere.
"""

from __future__ import annotations

import math

UPPER_HALF = "\u2580"  # ▀


def _scale_for(frame, cols: int, rows: int) -> int:
    """Largest integer sampling stride that still fits the terminal."""
    avail_rows = max(1, rows - 2)  # two text rows stay free for the hint/status
    s = max(1, math.ceil(frame.width / max(1, cols)))
    s = max(s, math.ceil((frame.height / 2) / avail_rows))
    return s


class TextScreen:
    """A frame painter that only needs ANSI SGR."""

    def __init__(self, cols: int, rows: int) -> None:
        self.cols = max(20, cols)
        self.rows = max(8, rows)
        self._prev: dict[int, str] = {}

    def reset(self) -> None:
        self._prev.clear()

    def paint(self, frame) -> bytes:
        s = _scale_for(frame, self.cols, self.rows)
        w = max(1, frame.width // s)
        h = max(1, frame.height // s)
        out: list[str] = []
        rows = 0
        for cy in range(0, (h + 1) // 2):
            y0 = min(h - 1, cy * 2)
            y1 = min(h - 1, cy * 2 + 1)
            row: list[str] = []
            for x in range(w):
                i0 = (y0 * s) * frame.width + x * s
                i1 = (y1 * s) * frame.width + x * s
                top = frame.palette[frame.pixels[i0]]
                bot = frame.palette[frame.pixels[i1]]
                if top == bot:
                    row.append(f"\x1b[48;2;{top[0]};{top[1]};{top[2]}m ")
                else:
                    row.append(
                        f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m"
                        f"\x1b[48;2;{bot[0]};{bot[1]};{bot[2]}m{UPPER_HALF}")
            text = "".join(row)
            rows = cy + 1
            if self._prev.get(cy) != text:
                out.append(f"\x1b[{cy + 1};1H{text}\x1b[0m")
                self._prev[cy] = text
        # clear anything the picture no longer covers
        for stale in [k for k in self._prev if k >= rows]:
            out.append(f"\x1b[{stale + 1};1H\x1b[K")
            del self._prev[stale]
        return "".join(out).encode("utf-8")

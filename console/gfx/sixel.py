"""Sixel encoder for indexed-colour pictures.

Sixel packs six vertical pixels into one character, so a picture is a stack of
six-pixel "bands". For every colour in a band we need one bit per pixel
column. The trick that keeps this fast in pure Python is to treat each row's
one-colour mask as a single big integer (one byte per pixel), OR the six rows
of a band together with shifts, and add 63 to every byte at once. Run-length
encoding (``!n<char>``) then collapses the long flat stretches Chrome's dino
page is made of.

A band's output depends only on the pixels in its six rows, and most of a game
screen is the same from one frame to the next, so finished bands are cached.
Palette indices must therefore mean the same colour from frame to frame (the
games use the grey level itself as the index).
"""

from __future__ import annotations

import re

_RUN = re.compile(rb"(.)\1*", re.S)
_ONES: dict[int, int] = {}
_TABLES: dict[int, bytes] = {}


def _ones63(width: int) -> int:
    value = _ONES.get(width)
    if value is None:
        value = int.from_bytes(bytes([63]) * width, "little")
        _ONES[width] = value
    return value


def _table(color: int) -> bytes:
    t = _TABLES.get(color)
    if t is None:
        t = bytes(1 if i == color else 0 for i in range(256))
        _TABLES[color] = t
    return t


_BOUNDARY = re.compile(rb"[^\x00]")


def _rle(chars: bytes, scale: int) -> bytes:
    """Run-length encode a band's characters, each repeated ``scale`` times.

    Run boundaries come from one big-integer XOR (byte i vs byte i + 1); only
    the boundaries are visited in Python, never the individual columns.
    """
    n = len(chars)
    x = int.from_bytes(chars, "little")
    diff = (x ^ (x >> 8)).to_bytes(n, "little")
    out = []
    start = 0
    for m in _BOUNDARY.finditer(diff, 0, n - 1):
        end = m.start() + 1
        length = (end - start) * scale
        c = chars[start : start + 1]
        out.append(b"!%d%s" % (length, c) if length > 3 else c * length)
        start = end
    length = (n - start) * scale
    c = chars[start : start + 1]
    out.append(b"!%d%s" % (length, c) if length > 3 else c * length)
    return b"".join(out)


def _percent(v: int) -> int:
    return (v * 100 + 127) // 255


_MASKS: dict[tuple[bytes, int], int] = {}


def _mask(row: bytes, color: int) -> int:
    """One-colour mask of a row as an integer with one byte (0 or 1) per pixel."""
    key = (row, color)
    v = _MASKS.get(key)
    if v is None:
        if len(_MASKS) > 40000:
            _MASKS.clear()
        v = int.from_bytes(row.translate(_table(color)), "little") if color in row else 0
        _MASKS[key] = v
    return v


class SixelEncoder:
    """Stateful encoder: keeps a cache of finished bands between frames."""

    MAX_CACHE = 6000

    def __init__(self) -> None:
        self._cache: dict[tuple, tuple[bytes, frozenset]] = {}
        self._plans: dict[tuple[int, int], list[tuple[tuple, tuple]]] = {}

    @staticmethod
    def _plan_for(height: int, scale: int) -> list[tuple[tuple, tuple]]:
        """For each six-pixel band: which source rows feed it, and which of the six exist."""
        out_h = height * scale
        plan = []
        for band in range((out_h + 5) // 6):
            ys = tuple(min(height - 1, (band * 6 + k) // scale) for k in range(6))
            valid = tuple((band * 6 + k) < out_h for k in range(6))
            plan.append((ys, valid))
        return plan

    def _band(self, rows: tuple, valid: tuple, width: int, scale: int, page: int | None):
        key = (rows, valid, scale, page)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        colors = sorted(set(b"".join(rows)) - {page})
        ones = _ones63(width)
        pieces = []
        used = []
        if page is not None:
            # The page colour covers the whole band in one run; the other colours
            # are painted over it (unset bits leave pixels alone), so its mask is never built.
            bits = sum(1 << k for k in range(6) if valid[k])
            pieces.append(b"#%d!%d%c" % (page, width * scale, 63 + bits))
            used.append(page)
        for c in colors:
            value = 0
            for k in range(6):
                if valid[k]:
                    m = _mask(rows[k], c)
                    if m:
                        value |= m << k
            if value:
                pieces.append(b"#%d" % c + _rle((value + ones).to_bytes(width, "little"), scale))
                used.append(c)
        entry = (b"$".join(pieces) + b"-", frozenset(used))
        if len(self._cache) >= self.MAX_CACHE:
            self._cache.clear()
        self._cache[key] = entry
        return entry

    def encode(self, pixels: bytes, width: int, height: int, palette, scale: int = 1, page: int | None = None) -> bytes:
        """Return a complete sixel sequence (DCS ... ST).

        ``pixels`` holds ``width * height`` palette indices; every pixel is
        painted and each source pixel becomes a ``scale`` x ``scale`` block.
        """
        plan = self._plans.get((height, scale))
        if plan is None:
            plan = self._plans[(height, scale)] = self._plan_for(height, scale)
        rows = [pixels[y * width : (y + 1) * width] for y in range(height)]
        bands = []
        used: set[int] = set()
        for ys, valid in plan:
            data, colors = self._band(tuple([rows[y] for y in ys]), valid, width, scale, page)
            bands.append(data)
            used |= colors
        head = [b"P0;1;0q\"1;1;%d;%d" % (width * scale, height * scale)]
        for index in sorted(used):
            r, g, b = palette[index]
            head.append(b"#%d;2;%d;%d;%d" % (index, _percent(r), _percent(g), _percent(b)))
        return b"".join(head) + b"".join(bands) + b"\\"

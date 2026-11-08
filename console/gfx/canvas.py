"""A tiny 2D canvas that speaks just enough of ``CanvasRenderingContext2D``.

The dino port calls ``draw_image`` / ``clear_rect`` / ``translate`` exactly the
way Chrome's TypeScript does. Everything the game draws is a 1:1 copy out of
one sprite sheet, so the canvas stores a single grey plane (the page colour
and every sprite pixel are neutral greys) and never resamples.

Because sprites are mostly flat runs of one colour, the sheet is pre-split
into horizontal runs and drawn with slice assignment, which keeps a full
60 fps frame cheap in pure Python.
"""

from __future__ import annotations

import math

from .png import decode

PAGE = 255  # chrome://dino page background (#fff)

Run = tuple  # (dy, dx, n, gray, alpha, solid_bytes)


class SpriteSheet:
    """Decoded sprite sheet with cached per-region run tables."""

    def __init__(self, width: int, height: int, rgba: bytes) -> None:
        self.width = width
        self.height = height
        n = width * height
        gray = bytearray(n)
        alpha = bytearray(n)
        for i in range(n):
            r, g, b, a = rgba[i * 4 : i * 4 + 4]
            gray[i] = (r * 299 + g * 587 + b * 114 + 500) // 1000
            alpha[i] = a
        self.gray = bytes(gray)
        self.alpha = bytes(alpha)
        self._regions: dict[tuple[int, int, int, int], tuple[Run, ...]] = {}

    @classmethod
    def from_png(cls, data: bytes) -> "SpriteSheet":
        w, h, rgba = decode(data)
        return cls(w, h, rgba)

    def runs(self, sx: int, sy: int, sw: int, sh: int) -> tuple[Run, ...]:
        key = (sx, sy, sw, sh)
        hit = self._regions.get(key)
        if hit is not None:
            return hit
        out: list[Run] = []
        w = self.width
        for dy in range(sh):
            y = sy + dy
            if y < 0 or y >= self.height:
                continue
            x = 0
            while x < sw:
                sxx = sx + x
                if sxx < 0 or sxx >= w:
                    x += 1
                    continue
                i = y * w + sxx
                a = self.alpha[i]
                if a == 0:
                    x += 1
                    continue
                g = self.gray[i]
                start = x
                x += 1
                while x < sw:
                    sxx = sx + x
                    if sxx >= w:
                        break
                    j = y * w + sxx
                    if self.alpha[j] != a or self.gray[j] != g:
                        break
                    x += 1
                n = x - start
                out.append((dy, start, n, g, a, bytes([g]) * n))
        result = tuple(out)
        self._regions[key] = result
        return result


def _css_gray(style: str) -> int:
    """Grey level of the few fill styles the game uses (#fff, #f7f7f7, ...)."""
    h = style.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16)


_LUTS: dict[tuple[int, int], bytes] = {}


def _blend_lut(src: int, e255: int) -> bytes:
    """Lookup table: destination grey -> grey after ``src`` is drawn at e/255."""
    key = (src, e255)
    lut = _LUTS.get(key)
    if lut is None:
        e = e255 / 255.0
        lut = bytes(int(src * e + d * (1.0 - e) + 0.5) for d in range(256))
        _LUTS[key] = lut
    return lut


class Canvas:
    """Grey-plane canvas. ``log`` (when a list) records every call for tests.

    Drawing is deferred: calls are queued and only rasterised when ``plane`` is
    read. A clear of the whole canvas makes everything queued before it
    irrelevant, so it is simply dropped. That matters because Chrome's loop
    redraws the whole scene twice per frame once a game is running.
    """

    def __init__(self, sheet: SpriteSheet, width: int = 600, height: int = 150) -> None:
        self.sheet = sheet
        self.width = width
        self.height = height
        self._plane = bytearray([PAGE]) * (width * height)
        self._ops: list[tuple] = []
        self._blank = False
        self._global_alpha = 1.0
        self.log: list | None = None
        self.fractional_draws = 0
        self.fill_style = "#000000"
        self._path: list[tuple[float, float, float, float]] = []
        self._tx = 0.0
        self._ty = 0.0
        self._stack: list[tuple[float, float, float, str]] = []

    # -- state ------------------------------------------------------------

    @property
    def global_alpha(self) -> float:
        return self._global_alpha

    @global_alpha.setter
    def global_alpha(self, value: float) -> None:
        # Like the real thing: values outside 0..1 (and NaN) are ignored.
        if 0.0 <= value <= 1.0:
            self._global_alpha = value

    def save(self) -> None:
        self._stack.append((self._tx, self._ty, self._global_alpha, self.fill_style))

    def restore(self) -> None:
        if self._stack:
            self._tx, self._ty, self._global_alpha, self.fill_style = self._stack.pop()

    def translate(self, x: float, y: float) -> None:
        self._tx += x
        self._ty += y

    def set_size(self, width: int, height: int) -> None:
        """Assigning canvas.width/height in a browser also clears the bitmap."""
        self.width = width
        self.height = height
        self._plane = bytearray([PAGE]) * (width * height)
        self._ops.clear()
        self._blank = False

    @property
    def plane(self) -> bytearray:
        """The pixels, with everything drawn so far applied."""
        if self._ops or self._blank:
            self._flush()
        return self._plane

    # -- drawing (queued) -------------------------------------------------

    def clear_rect(self, x: float, y: float, w: float, h: float) -> None:
        if self.log is not None:
            self.log.append(("clear", x + self._tx, y + self._ty, w, h))
        x0 = max(0, int(x + self._tx))
        y0 = max(0, int(y + self._ty))
        x1 = min(self.width, int(x + self._tx + w))
        y1 = min(self.height, int(y + self._ty + h))
        if x1 <= x0 or y1 <= y0:
            return
        if x0 == 0 and y0 == 0 and x1 == self.width and y1 == self.height:
            self._ops.clear()  # nothing drawn before a full clear can show
            self._blank = True
            return
        self._ops.append(("clear", x0, y0, x1, y1))

    def begin_path(self) -> None:
        self._path = []

    def rect(self, x: float, y: float, w: float, h: float) -> None:
        self._path.append((x + self._tx, y + self._ty, w, h))

    def fill(self) -> None:
        """Fill every rect added since ``begin_path`` (the path is never reset by fill)."""
        gray = _css_gray(self.fill_style)
        for x, y, w, h in self._path:
            if self.log is not None:
                self.log.append(("fillrect", x, y, w, h))
            x0, y0 = max(0, int(x)), max(0, int(y))
            x1, y1 = min(self.width, int(x + w)), min(self.height, int(y + h))
            if x1 > x0 and y1 > y0:
                self._ops.append(("fill", gray, x0, y0, x1, y1))

    def draw_image(self, sx: float, sy: float, sw: float, sh: float, dx: float, dy: float, dw: float, dh: float) -> None:
        dx += self._tx
        dy += self._ty
        if self.log is not None:
            self.log.append(("draw", sx, sy, sw, sh, dx, dy, dw, dh, self._global_alpha))
        try:
            ox, oy = int(dx // 1), int(dy // 1)
            isx, isy, isw, ish = int(sx), int(sy), int(sw), int(sh)
        except (ValueError, OverflowError):
            return  # drawImage ignores non-finite arguments
        if dw != sw or dh != sh:
            raise ValueError("Console's canvas only does 1:1 sprite copies")
        if dx != ox or dy != oy:
            self.fractional_draws += 1
        self._ops.append(("draw", isx, isy, isw, ish, ox, oy, self._global_alpha))

    # -- rasterising --------------------------------------------------------

    def _flush(self) -> None:
        plane = self._plane
        width, height = self.width, self.height
        if self._blank:
            plane[:] = bytes([PAGE]) * (width * height)
            self._blank = False
        runs_of = self.sheet.runs
        for op in self._ops:
            kind = op[0]
            if kind == "draw":
                _, sx, sy, sw, sh, ox, oy, alpha = op
                for ry, rx, n, gray, a, solid in runs_of(sx, sy, sw, sh):
                    y = oy + ry
                    if y < 0 or y >= height:
                        continue
                    x0 = ox + rx
                    x1 = x0 + n
                    if x1 <= 0 or x0 >= width:
                        continue
                    lo = 0
                    hi = n
                    if x0 < 0:
                        lo = -x0
                        x0 = 0
                    if x1 > width:
                        hi = n - (x1 - width)
                        x1 = width
                    base = y * width
                    e = a * alpha
                    if e >= 254.5:
                        plane[base + x0 : base + x1] = solid[lo:hi]
                    elif e > 0.0:
                        plane[base + x0 : base + x1] = plane[base + x0 : base + x1].translate(_blend_lut(gray, int(e + 0.5)))
            elif kind == "clear":
                _, x0, y0, x1, y1 = op
                blank = bytes([PAGE]) * (x1 - x0)
                for yy in range(y0, y1):
                    o = yy * width
                    plane[o + x0 : o + x1] = blank
            else:  # fill
                _, gray, x0, y0, x1, y1 = op
                row = bytes([gray]) * (x1 - x0)
                for yy in range(y0, y1):
                    o = yy * width
                    plane[o + x0 : o + x1] = row
        self._ops.clear()

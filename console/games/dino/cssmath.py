"""CSS timing functions, for the two transitions the dino page uses."""

from __future__ import annotations


def cubic_bezier(x1: float, y1: float, x2: float, y2: float):
    """Return f(t) for ``cubic-bezier(x1, y1, x2, y2)`` like a browser would."""

    def sample(a: float, b: float, c: float, t: float) -> float:
        return ((a * t + b) * t + c) * t

    cx = 3 * x1
    bx = 3 * (x2 - x1) - cx
    ax = 1 - cx - bx
    cy = 3 * y1
    by = 3 * (y2 - y1) - cy
    ay = 1 - cy - by

    def solve_x(x: float) -> float:
        t = x
        for _ in range(8):  # Newton
            err = sample(ax, bx, cx, t) - x
            if abs(err) < 1e-7:
                return t
            d = (3 * ax * t + 2 * bx) * t + cx
            if abs(d) < 1e-6:
                break
            t -= err / d
        lo, hi = 0.0, 1.0  # bisection fallback
        t = x
        while lo < hi:
            err = sample(ax, bx, cx, t)
            if abs(err - x) < 1e-7:
                return t
            if x > err:
                lo = t
            else:
                hi = t
            t = (hi - lo) * 0.5 + lo
            if hi - lo < 1e-9:
                break
        return t

    def f(progress: float) -> float:
        if progress <= 0:
            return 0.0
        if progress >= 1:
            return 1.0
        return sample(ay, by, cy, solve_x(progress))

    return f


# `intro .4s ease-out` and `.offline { transition: filter 1.5s cubic-bezier(...) }`
ease_out = cubic_bezier(0.0, 0.0, 0.58, 1.0)
invert_curve = cubic_bezier(0.65, 0.05, 0.36, 1.0)

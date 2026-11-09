"""Render the README GIF: chess, kart and dino, headless, no terminal needed.

    python tools/make_readme_gif.py            # writes docs/arcade.gif

Frames come from each game's own renderer, so the GIF cannot drift from what the
terminal shows. ffmpeg is only used to encode the GIF (palettegen + paletteuse).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from console.games.chess.game import ChessGame
from console.games.chess.rules import mk, sq_from_name
from console.games.dino.game import DinoGame
from console.games.kart.game import KartGame, PX_PER_METRE  # noqa: E402

W, H = 600, 240
BG = (14, 14, 20)
INK = (245, 245, 245)
SOFT = (150, 150, 160)
ACCENT = (232, 196, 74)
FPS = 12

GLYPHS = KartGame._GLYPHS


def _text(buf: bytearray, x: int, y: int, text: str, scale: int, colour) -> None:
    cx = x
    for ch in text.upper():
        glyph = GLYPHS.get(ch) or GLYPHS["?"]
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px, py = cx + gx * scale + dx, y + gy * scale + dy
                        if 0 <= px < W and 0 <= py < H:
                            i = (py * W + px) * 3
                            buf[i:i + 3] = bytes(colour)
        cx += 4 * scale


def _blank() -> bytearray:
    return bytearray(bytes(BG) * (W * H))


def _blit(dst: bytearray, src: bytearray, sw: int, sh: int, ox: int, oy: int, scale: int) -> None:
    """Nearest-neighbour blit of an RGB image into the canvas."""
    for y in range(sh * scale):
        sy = y // scale
        if not (0 <= oy + y < H):
            continue
        row = sy * sw * 3
        for x in range(sw * scale):
            if not (0 <= ox + x < W):
                continue
            sx = x // scale
            si = row + sx * 3
            di = ((oy + y) * W + ox + x) * 3
            dst[di:di + 3] = src[si:si + 3]


def _frame_rgb(frame) -> tuple[bytearray, int, int]:
    palette = frame.palette
    w, h = frame.width, frame.height
    out = bytearray(w * h * 3)
    for i, idx in enumerate(frame.pixels):
        r, g, b = palette[idx]
        j = i * 3
        out[j], out[j + 1], out[j + 2] = r, g, b
    return out, w, h


def _caption(title: str, subtitle: str) -> bytearray:
    buf = _blank()
    _text(buf, 24, 84, title, 6, ACCENT)
    _text(buf, 24, 148, subtitle, 3, SOFT)
    return buf


def _fit(src: bytearray, sw: int, sh: int, tw: int, th: int) -> tuple[bytearray, int, int]:
    """Nearest-neighbour scale to exactly tw x th (no letterboxing, no crop)."""
    out = bytearray(tw * th * 3)
    for y in range(th):
        sy = min(sh - 1, y * sh // th)
        for x in range(tw):
            sx = min(sw - 1, x * sw // tw)
            si = (sy * sw + sx) * 3
            di = (y * tw + x) * 3
            out[di:di + 3] = src[si:si + 3]
    return out, tw, th


def _card(src: bytearray, sw: int, sh: int, scale: int) -> bytearray:
    buf = _blank()
    _blit(buf, src, sw, sh, (W - sw * scale) // 2, (H - sh * scale) // 2, scale)
    return buf


def chess_frames() -> list[bytearray]:
    g = ChessGame()
    g.mode = "play"
    moves = [("e2", "e4"), ("e7", "e5"), ("g1", "f3"), ("b8", "c6"),
             ("f1", "c4"), ("g8", "f6"), ("f3", "g5"), ("d7", "d5")]
    frames = []
    for frm, to in moves:
        m = mk(sq_from_name(frm), sq_from_name(to))
        legal = g.board.legal_moves()
        if m in legal:
            g.board.make(m)
            g._dirty = True
        for _ in range(5):
            rgb, sw, sh = _frame_rgb(g.frame(0))
            fitted, fw, fh = _fit(rgb, sw, sh, 224, 224)
            frames.append(_card(fitted, fw, fh, 1))
    return frames


def kart_frames(count: int = 36) -> list[bytearray]:
    g = KartGame(seed=0)
    g.state.phase = "racing"
    g.state.countdown = 0.0
    g._throttle = 1.0
    t = 0.0
    # let the field spread out so the first sample is not four karts overlapping
    for _ in range(180):
        t += 1000 / 60
        g.tick(t)
    frames = []
    for i in range(count):
        if i == 12:
            g._pending_lane = 0
        if i == 24:
            g._pending_lane = 2
        t += 1000 / 60 * 5  # 12 fps sampled from the 60 fps sim
        g.tick(t)
        rgb, sw, sh = _frame_rgb(g.frame(t))
        fitted, fw, fh = _fit(rgb, sw, sh, W, 120)
        frames.append(_card(fitted, fw, fh, 1))
    return frames


def dino_frames(count: int = 36) -> list[bytearray]:
    g = DinoGame()
    # Chrome's runner starts in a 44px container and expands on first interaction.
    g._ensure_started(0)
    g.runner.play_intro()
    t = 0.0
    for _ in range(12):
        t += 1000 / FPS
        g.tick(t)
    frames = []
    for i in range(count):
        t += 1000 / FPS
        if i % 9 == 3:
            g.handle_key("space", True, t)
        elif i % 9 == 4:
            g.handle_key("space", False, t)
        rgb, sw, sh = _frame_rgb(g.frame(t))
        fitted, fw, fh = _fit(rgb, sw, sh, W, 150)
        frames.append(_card(fitted, fw, fh, 1))
    return frames


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg is required to encode the GIF", file=sys.stderr)
        return 2

    seconds = 0.8
    plan = [
        (_caption("CHESS", "you vs the model, Stockfish or the engine"), int(seconds * FPS)),
        (None, 0),
    ]
    frames: list[bytearray] = []
    frames += [plan[0][0]] * plan[0][1]
    frames += chess_frames()

    cap = _caption("KART", "3 lanes, coins, drift boost, three times of day")
    frames += [cap] * int(seconds * FPS)
    frames += kart_frames()

    cap = _caption("DINO", "Chrome's runner, wearing the Hermes cap")
    frames += [cap] * int(seconds * FPS)
    frames += dino_frames()

    out = ROOT / "docs" / "arcade.gif"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="arcade-gif-") as tmp:
        for i, buf in enumerate(frames):
            Path(tmp, f"f{i:04d}.ppm").write_bytes(b"P6\n%d %d\n255\n" % (W, H) + bytes(buf))
        cmd = [
            ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS),
            "-i", str(Path(tmp, "f%04d.ppm")),
            "-vf", "split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=none",
            "-loop", "0", str(out),
        ]
        subprocess.run(cmd, check=True)
    print(f"{out}  {len(frames)} frames  {out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

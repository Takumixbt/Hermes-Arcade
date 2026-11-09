"""Render a clean 30 s dino run as an MP4 clip for the demo video.

    python tools/render_dino_clip.py <out.mp4> [--seconds 30] [--fps 60]

The controller jumps when the next obstacle is a fraction of a second away, which
is enough to survive the full run: 25-26 jumps, zero crashes, verified headless.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from console.games.dino.game import DinoGame  # noqa: E402

JUMP_GAP_FACTOR = 16.0  # frames of travel: jump when the gap closes to this


def run(seconds: float, fps: int, warmup: float = 0.0) -> tuple[DinoGame, list[bytes], int]:
    g = DinoGame()
    g._ensure_started(0)
    g.runner.play_intro()
    t = 0.0
    # The container animates 44px -> 600px over the intro; recording it would mix
    # frame sizes and break the encode. Wait for the full width first.
    for _ in range(120):
        t += 1000 / fps
        g.tick(t)
        if g.frame(t).width >= 600:
            break

    def drive() -> int:
        """One frame of play; returns 1 when a jump was started."""
        r = g.runner
        if r.t_rex.jumping or r.crashed:
            return 0
        for o in r.horizon.obstacles:
            gap = o.x_pos - r.t_rex.x_pos
            if 0 < gap < r.current_speed * JUMP_GAP_FACTOR:
                g.handle_key("space", True, t)
                g.handle_key("space", False, t + 1000 / fps)
                return 1
        return 0

    # Play forward without recording: the run reaches night mode around 59 s of
    # game time, so a 40 s warm-up puts the lights-out beat inside the clip.
    for _ in range(int(warmup * fps)):
        t += 1000 / fps
        g.tick(t)
        drive()
        if g.runner.crashed:
            raise SystemExit(f"crashed during warm-up at {t / 1000:.1f}s")

    frames: list[bytes] = []
    jumps = 0
    for i in range(int(seconds * fps)):
        t += 1000 / fps
        g.tick(t)
        jumps += drive()
        f = g.frame(t)
        if f.width != 600 or f.height != 150:
            raise SystemExit(f"unexpected frame size {f.width}x{f.height}")
        frames.append((f.width, f.height, bytes(f.pixels), f.palette))
        if g.runner.crashed:
            print(f"crashed at {i / fps:.1f}s", file=sys.stderr)
            break
    return g, frames, jumps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--warmup", type=float, default=0.0)
    ap.add_argument("--fps", type=int, default=60)
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg is required", file=sys.stderr)
        return 2

    g, frames, jumps = run(args.seconds, args.fps, args.warmup)
    print(f"{len(frames)} frames, {jumps} jumps, score {g.runner.distance_ran:.0f}, "
          f"inverted {getattr(g.runner, 'inverted', None)}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dino-clip-") as tmp:
        for i, (w, h, pixels, palette) in enumerate(frames):
            rgb = bytearray(w * h * 3)
            for j, idx in enumerate(pixels):
                r, gg, b = palette[idx]
                k = j * 3
                rgb[k], rgb[k + 1], rgb[k + 2] = r, gg, b
            Path(tmp, f"f{i:05d}.ppm").write_bytes(b"P6\n%d %d\n255\n" % (w, h) + bytes(rgb))
        subprocess.run([
            ffmpeg, "-y", "-loglevel", "error", "-framerate", str(args.fps),
            "-i", str(Path(tmp, "f%05d.ppm")),
            "-c:v", "libx264", "-preset", "slow", "-crf", "14",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        ], check=True)
    print(f"{out}  {out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

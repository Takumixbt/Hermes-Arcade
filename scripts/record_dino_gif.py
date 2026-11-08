#!/usr/bin/env python3
"""Record docs/console-dino.gif from the real engine (the demo autopilot playing).

The frames are exactly what Console sends to the terminal, one pixel per game
pixel, so the GIF is the game and not a mock-up. About a third of the clip is
night mode: the first run is fast-forwarded to just under 700 points, which is
the only thing this script does that a player could not.

    python -m pip install pillow
    python scripts/record_dino_gif.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from console.games.dino.demo import DemoDino  # noqa: E402

OUT = ROOT / "docs" / "console-dino.gif"
FRAME_MS = 1000 / 60
SAMPLE_EVERY = 3  # 20 fps GIF
SECONDS = 22


def main() -> None:
    game = DemoDino(seed=11)
    t = 0.0
    game.tick(t)
    frames: list[Image.Image] = []
    boosted = False
    for i in range(int(SECONDS * 60)):
        t += FRAME_MS
        game.tick(t)
        r = game.runner
        if not boosted and r.playing and r.activated and not r.playing_intro and r.running_time > 3500:
            r.distance_ran = 26200  # a few seconds short of the first night
            boosted = True
        if i % SAMPLE_EVERY == 0:
            f = game.frame(t)
            img = Image.frombytes("L", (f.width, f.height), f.pixels)
            canvas = Image.new("L", (600, 150), f.page)
            canvas.paste(img, ((600 - f.width) // 2, 0))
            frames.append(canvas)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=int(FRAME_MS * SAMPLE_EVERY),
        loop=0,
        optimize=True,
        disposal=1,
    )
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB, {len(frames)} frames)")


if __name__ == "__main__":
    main()

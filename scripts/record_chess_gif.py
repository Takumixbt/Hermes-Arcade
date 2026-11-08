#!/usr/bin/env python3
"""Record docs/console-chess.gif from the real chess game.

A scripted game (the Ruy Lopez) is played through the same key handling a player
uses: the cursor travels square to square, a piece is picked up (its legal
squares light up) and put down, and each reply arrives after a short "thinking"
pause. Frames are exactly what Console sends to the terminal.

    python -m pip install pillow
    python scripts/record_chess_gif.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from console.games.chess.game import ChessGame  # noqa: E402
from console.games.chess.rules import sq_from_name  # noqa: E402

OUT = ROOT / "docs" / "console-chess.gif"
SCALE = 3

# (your move, the reply) pairs; you are White
GAME = [("e4", "e5"), ("Nf3", "Nc6"), ("Bb5", "a6"), ("Ba4", "Nf6"), ("O-O", "Be7"), ("Re1", "b5"), ("Bb3", "d6")]


def main() -> None:
    game = ChessGame()
    game.handle_key("enter", True, 0)  # start a game as White
    frames: list[tuple[Image.Image, int]] = []

    def snap(ms: int) -> None:
        f = game.frame(0)
        img = Image.frombytes("P", (f.width, f.height), f.pixels)
        img.putpalette([c for rgb in f.palette for c in rgb])
        img = img.resize((f.width * SCALE, f.height * SCALE), Image.NEAREST)
        frames.append((img, ms))

    def press(key: str, ms: int = 110) -> None:
        game.handle_key(key, True, 0)
        snap(ms)

    def travel(to: str) -> None:
        target = sq_from_name(to)
        while game.cursor != target:
            f, r = game.cursor & 7, game.cursor >> 3
            tf, tr = target & 7, target >> 3
            press("right" if tf > f else "left" if tf < f else "up" if tr > r else "down")

    snap(900)
    for mine, reply in GAME:
        move = game.board.find_move(mine)
        frm, to = (move & 63), (move >> 6) & 63
        names = "abcdefgh"
        travel(names[frm & 7] + str((frm >> 3) + 1))
        snap(250)
        press("enter", 650)  # pick up: legal squares are dotted
        travel(names[to & 7] + str((to >> 3) + 1))
        snap(250)
        press("enter", 500)  # put down
        # the opponent thinks for a moment, then answers
        game.thinking = True
        snap(700)
        game.thinking = False
        game._apply(game.board.find_move(reply))
        snap(900)
    frames[-1] = (frames[-1][0], 2500)

    first = frames[0][0]
    first.save(
        OUT,
        save_all=True,
        append_images=[f for f, _ in frames[1:]],
        duration=[d for _, d in frames],
        loop=0,
        optimize=True,
        disposal=1,
    )
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB, {len(frames)} frames)")


if __name__ == "__main__":
    main()

"""PNG decoding, the canvas, the sixel encoder and the on-screen layout."""

import random
import re

import pytest

from console.games.base import Frame
from console.games.dino.sprites import SHEET_PATH, load_sheet
from console.gfx import png
from console.gfx.canvas import Canvas
from console.gfx.sixel import SixelEncoder
from console.runner import compose, compute_layout


def test_chrome_sprite_sheet_decodes():
    w, h, rgba = png.decode(SHEET_PATH.read_bytes())
    assert (w, h) == (1233, 100)
    opaque = {tuple(rgba[i : i + 4]) for i in range(0, len(rgba), 4)}
    assert (83, 83, 83, 255) in opaque  # Chrome's #535353 ink
    assert (0, 0, 0, 0) in opaque


def test_decoder_matches_pillow_on_the_sheet():
    pil = pytest.importorskip("PIL.Image")
    ours = png.decode(SHEET_PATH.read_bytes())[2]
    theirs = pil.open(SHEET_PATH).convert("RGBA").tobytes()
    assert ours == theirs


def test_rejects_garbage():
    with pytest.raises(png.PngError):
        png.decode(b"not a png")


def test_canvas_draws_sprites_one_to_one_and_clips():
    sheet = load_sheet()
    c = Canvas(sheet, 600, 150)
    c.draw_image(848, 2, 44, 47, 50, 93, 44, 47)  # the standing T-rex
    assert 83 in set(c.plane)
    c2 = Canvas(sheet, 600, 150)
    c2.draw_image(848, 2, 44, 47, 590, 140, 44, 47)  # mostly off the bottom-right edge: must not raise
    c2.draw_image(848, 2, 44, 47, -30, -30, 44, 47)
    with pytest.raises(ValueError):
        c.draw_image(0, 0, 10, 10, 0, 0, 20, 20)


def test_global_alpha_ignores_out_of_range_values():
    c = Canvas(load_sheet())
    c.global_alpha = 0.5
    c.global_alpha = 1.015
    assert c.global_alpha == 0.5
    c.global_alpha = float("nan")
    assert c.global_alpha == 0.5


def decode_sixel(data: bytes, width: int, height: int):
    """A tiny sixel decoder, just enough to check the encoder against."""
    assert data.startswith(b"\x1bP") and data.endswith(b"\x1b\\")
    body = data[data.index(b"q") + 1 : -2]
    m = re.match(rb'"1;1;(\d+);(\d+)', body)
    assert (int(m.group(1)), int(m.group(2))) == (width, height)
    body = body[m.end() :]
    palette, img = {}, [[None] * width for _ in range(height)]
    x = band = 0
    color = None
    for tok in re.finditer(rb"#(\d+);2;(\d+);(\d+);(\d+)|#(\d+)|!(\d+)(.)|([?-~])|(\$)|(-)", body, re.S):
        if tok.group(1):
            palette[int(tok.group(1))] = tuple(int(tok.group(i)) for i in (2, 3, 4))
        elif tok.group(5):
            color = int(tok.group(5))
        elif tok.group(6) or tok.group(8):
            n, ch = (int(tok.group(6)), tok.group(7)) if tok.group(6) else (1, tok.group(8))
            bits = ch[0] - 63
            for _ in range(n):
                for k in range(6):
                    if bits >> k & 1 and band * 6 + k < height and x < width:
                        img[band * 6 + k][x] = color
                x += 1
        elif tok.group(9):
            x = 0
        elif tok.group(10):
            x = 0
            band += 1
    return palette, img


@pytest.mark.parametrize("scale", [1, 2, 3])
def test_sixel_round_trip(scale):
    rng = random.Random(scale)
    w, h = 40, 17
    levels = [255, 83, 218, 172]
    pixels = bytes(rng.choice(levels) if rng.random() < 0.3 else 255 for _ in range(w * h))
    frame = Frame.from_gray(w, h, pixels)
    data = SixelEncoder().encode(pixels, w, h, frame.palette, scale, page=255)
    palette, img = decode_sixel(data, w * scale, h * scale)
    for y in range(h * scale):
        for x in range(w * scale):
            want = pixels[(y // scale) * w + x // scale]
            got = img[y][x]
            assert got is not None, f"pixel {x},{y} was never painted"
            pct = palette[got][0]
            assert abs(pct * 255 / 100 - want) <= 2, (x, y, want, pct)


def test_sixel_cache_does_not_change_the_output():
    w, h = 30, 12
    pixels = bytes((i * 7) % 3 * 80 for i in range(w * h))
    frame = Frame.from_gray(w, h, pixels)
    enc = SixelEncoder()
    first = enc.encode(pixels, w, h, frame.palette, 2, page=0)
    again = enc.encode(pixels, w, h, frame.palette, 2, page=0)
    assert first == again == SixelEncoder().encode(pixels, w, h, frame.palette, 2, page=0)


def test_layout_prefers_the_biggest_whole_scale():
    lay = compute_layout(cols=120, rows=30, cell_w=10, cell_h=20, fw=600, fh=150)
    assert lay.scale == 2 and lay.width == 600 and lay.height == 150
    big = compute_layout(cols=240, rows=60, cell_w=10, cell_h=20, fw=600, fh=150)
    assert big.scale == 4
    assert compute_layout(cols=40, rows=10, cell_w=10, cell_h=20, fw=600, fh=150) is None


def test_layout_keeps_whole_text_rows_for_the_picture():
    lay = compute_layout(cols=120, rows=30, cell_w=10, cell_h=20, fw=600, fh=150)
    assert (lay.height * lay.scale) % 20 == 0  # never spills into the help text rows
    lay = compute_layout(cols=100, rows=32, cell_w=9, cell_h=19, fw=600, fh=150)
    assert lay.scale == 1 and lay.width * lay.scale <= 100 * 9


def test_compose_centres_the_frame_on_the_page():
    lay = compute_layout(cols=120, rows=30, cell_w=10, cell_h=20, fw=600, fh=150)
    f = Frame.from_gray(44, 150, bytes([83]) * (44 * 150))
    img = compose(f, lay)
    row = img[75 * lay.width : 76 * lay.width]
    ink = [i for i, v in enumerate(row) if v == 83]
    assert ink[0] == (lay.width - 44) // 2 and len(ink) == 44
    assert row[0] == 255

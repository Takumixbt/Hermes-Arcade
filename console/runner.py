"""The session loop: own the screen, feed keys to a game, paint its frames."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .games.base import Frame, Game
from .gfx.sixel import SixelEncoder
from .state import ACTIVITY
from .terminal import SYNC_BEGIN, SYNC_END, Terminal, TerminalError

FRAME_S = 1 / 60

# One clock for the whole process, so a game's own timestamps stay continuous
# when Console is left and re-entered.
_EPOCH = time.perf_counter()


def now_ms() -> float:
    return (time.perf_counter() - _EPOCH) * 1000.0


@dataclass
class Layout:
    """Where the picture goes. All sizes in game pixels unless noted."""

    scale: int  # screen pixels per game pixel (whole numbers only, so pixels stay square)
    col0: int  # 1-based text column of the picture's left edge
    row0: int  # 1-based text row of the picture's top edge
    width: int  # picture size: the page strip the game frame is centred in
    height: int


def compute_layout(cols: int, rows: int, cell_w: int, cell_h: int, fw: int, fh: int, reserve: int = 2) -> Layout | None:
    """Largest whole-number scale that fits; ``reserve`` text rows stay free for help text."""
    avail_rows = max(1, rows - reserve)
    scale = min((cols * cell_w) // fw, (avail_rows * cell_h) // fh)
    if scale < 1:
        return None
    region_cols = cols
    while region_cols > 1 and (region_cols * cell_w) % scale:
        region_cols -= 1
    pic_px_h = -(-fh * scale // cell_h) * cell_h  # whole text rows, so rows never overlap text
    pic_rows = pic_px_h // cell_h
    return Layout(
        scale=scale,
        col0=(cols - region_cols) // 2 + 1,
        row0=1 + max(0, (avail_rows - pic_rows) * 3 // 10),
        width=region_cols * cell_w // scale,
        height=pic_px_h // scale,
    )


def compose(frame: Frame, layout: Layout) -> bytes:
    """Centre the game frame in a page-coloured strip."""
    w, h = layout.width, layout.height
    img = bytearray([frame.page]) * (w * h)
    fw = frame.width
    x0 = max(0, (w - fw) // 2)
    y0 = max(0, (h - frame.height) // 2)
    for y in range(frame.height):
        o = (y0 + y) * w + x0
        img[o : o + fw] = frame.pixels[y * fw : (y + 1) * fw]
    return bytes(img)


def _page_style(rgb: tuple) -> bytes:
    """SGR that makes the terminal itself the page: background and a readable ink."""
    ink = 83 if sum(rgb) / 3 > 127 else 172
    return b"\x1b[0m\x1b[48;2;%d;%d;%dm\x1b[38;2;%d;%d;%dm" % (rgb[0], rgb[1], rgb[2], ink, ink, ink)


def _status_text(game: Game) -> tuple[str, str]:
    busy, finished = ACTIVITY.snapshot()
    host = ACTIVITY.host or "Agent"
    if finished:
        status = f"{host} finished. Press Esc to go back."
    elif busy:
        status = f"{host} is working..."
    else:
        status = ""
    return game.hint(), status


def _cup(row: int, col: int) -> bytes:
    return b"\x1b[%d;%dH" % (row, col)


def play(game: Game, term: Terminal | None = None) -> str:
    """Run ``game`` until the player presses Esc. Returns a short message."""
    if term is not None:
        return _play(game, term)
    try:
        with Terminal() as t:
            return _play(game, t)
    except TerminalError as exc:
        return f"Console needs a real terminal ({exc})."


def _unsupported(term: Terminal) -> str:
    term.write(b"\x1b[2J\x1b[H")
    term.write(
        (
            "Console draws with sixel graphics, and this terminal did not say it supports them.\r\n"
            "Use Windows Terminal 1.22+, WezTerm, iTerm2 or foot (or set CONSOLE_SIXEL=1 to force it).\r\n"
            "\r\nPress any key to go back.\r\n"
        ).encode()
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if term.poll_keys(now_ms() / 1000.0):
            break
        time.sleep(0.03)
    return "This terminal does not support sixel graphics."


def _play(game: Game, term: Terminal) -> str:
    caps = term.caps
    if not caps.sixel:
        return _unsupported(term)

    encoder = SixelEncoder()
    debug = bool(os.environ.get("CONSOLE_DEBUG"))
    size = term.size()
    reserve = game.hint().count("\n") + 2
    layout = compute_layout(size[0], size[1], caps.cell_w, caps.cell_h, game.width, game.height, reserve)
    last_text = None
    last_page = None
    last_picture = None
    frames = 0
    fps_t0 = time.perf_counter()
    fps = 0.0
    encode_ms = 0.0
    prof = {"tick": 0.0, "frame": 0.0, "write": 0.0}
    game.resume(now_ms())
    next_frame = time.perf_counter()
    try:
        while True:
            t = now_ms()
            leaving = False
            for key, pressed in term.poll_keys(t / 1000.0):
                if key == "esc" and pressed:
                    leaving = True
                    break
                game.handle_key(key, pressed, t)
            if leaving:
                break

            if frames % 15 == 0:
                new_size = term.size()
                if new_size != size:
                    size = new_size
                    layout = compute_layout(size[0], size[1], caps.cell_w, caps.cell_h, game.width, game.height, reserve)
                    last_text = last_page = last_picture = None

            p0 = time.perf_counter()
            game.tick(t)
            prof["tick"] = prof["tick"] * 0.9 + (time.perf_counter() - p0) * 100
            want = game.hint().count("\n") + 2
            if want != reserve:
                reserve = want
                layout = compute_layout(size[0], size[1], caps.cell_w, caps.cell_h, game.width, game.height, reserve)
                last_text = last_page = last_picture = None
            out = []
            picture_added = False
            if layout is None:
                if last_text != "small":
                    last_text = "small"
                    out.append(b"\x1b[0m\x1b[2J" + _cup(1, 1) + b"Window too small: enlarge it to play.")
            else:
                p0 = time.perf_counter()
                frame = game.frame(t)
                prof["frame"] = prof["frame"] * 0.9 + (time.perf_counter() - p0) * 100
                if frame.page != last_page:
                    out.append(_page_style(frame.palette[frame.page]) + b"\x1b[2J")
                    last_page = frame.page
                    last_text = last_picture = None
                picture = (frame.width, frame.pixels)
                if picture != last_picture:
                    last_picture = picture
                    t0 = time.perf_counter()
                    sixel = encoder.encode(compose(frame, layout), layout.width, layout.height, frame.palette, layout.scale, frame.page)
                    encode_ms = encode_ms * 0.9 + (time.perf_counter() - t0) * 1000 * 0.1
                    out.append(_cup(layout.row0, layout.col0) + sixel)
                    picture_added = True
                hint, status = _status_text(game)
                if debug:
                    status = (
                        f"{fps:4.1f} fps  tick {prof['tick']:.1f}  frame {prof['frame']:.1f}  "
                        f"encode {encode_ms:.1f}  write {prof['write']:.1f} ms  scale {layout.scale}"
                    )
                text = (hint, status, size)
                if text != last_text:
                    last_text = text
                    cols, rows = size
                    bell = b"\x07" if status.startswith(f"{ACTIVITY.host or 'Agent'} finished") else b""
                    lines = hint.split("\n")
                    for i, line in enumerate(lines):
                        out.append(_cup(rows - len(lines) + i, 1) + b"\x1b[2K" + line.center(cols)[:cols].encode())
                    out.append(_cup(rows, 1) + b"\x1b[2K" + status.center(cols)[:cols].encode() + bell)
            if out:
                p0 = time.perf_counter()
                # a packet that only repaints the picture may be superseded by the next one
                term.send(SYNC_BEGIN + b"".join(out) + SYNC_END, droppable=picture_added and len(out) == 1)
                prof["write"] = prof["write"] * 0.9 + (time.perf_counter() - p0) * 100

            frames += 1
            if frames % 30 == 0:
                nowp = time.perf_counter()
                fps = 30 / (nowp - fps_t0)
                fps_t0 = nowp
            next_frame += FRAME_S
            delay = next_frame - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_frame = time.perf_counter()  # fell behind: do not try to catch up
    finally:
        game.pause(now_ms())
        game.close()
        ACTIVITY.acknowledge()
    return "Left Console."

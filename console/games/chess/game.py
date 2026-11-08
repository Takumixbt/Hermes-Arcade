"""Chess in Console: you against the built-in engine, Stockfish, a coding agent or an API model."""

from __future__ import annotations

import random
import threading

from ...catalog import register
from ..base import Frame, Game
from . import models, opponents
from .engine import LEVELS
from .pieces import SIZE, outline, silhouette
from .rules import BLACK, KING, PAWN, WHITE, Board, mk, move_from, move_promo, move_to, sq_name, QUEEN, ROOK, BISHOP, KNIGHT

BOARD = 8 * SIZE
MARGIN = 8
SIDE = BOARD + 2 * MARGIN

# -- palette (index = position in this list) ---------------------------------------------
_COLORS = {
    "page": (38, 36, 33),
    "light": (240, 217, 181),
    "dark": (181, 136, 99),
    "light_last": (205, 210, 106),
    "dark_last": (170, 162, 58),
    "light_sel": (246, 246, 130),
    "dark_sel": (214, 214, 60),
    "light_check": (235, 120, 100),
    "dark_check": (210, 90, 75),
    "white_fill": (250, 250, 250),
    "white_edge": (25, 25, 25),
    "black_fill": (60, 60, 66),
    "black_edge": (12, 12, 12),
    "light_dot": (170, 150, 115),
    "dark_dot": (125, 90, 62),
    "cursor": (255, 210, 40),
    "coord": (160, 158, 150),
}
NAMES = list(_COLORS)
IDX = {name: i for i, name in enumerate(NAMES)}
PALETTE = tuple(_COLORS[n] for n in NAMES)

_FONT = {
    "a": (".#.", "#.#", "###", "#.#", "#.#"), "b": ("##.", "#.#", "##.", "#.#", "##."),
    "c": (".##", "#..", "#..", "#..", ".##"), "d": ("##.", "#.#", "#.#", "#.#", "##."),
    "e": ("###", "#..", "##.", "#..", "###"), "f": ("###", "#..", "##.", "#..", "#.."),
    "g": (".##", "#..", "#.#", "#.#", ".##"), "h": ("#.#", "#.#", "###", "#.#", "#.#"),
    "1": (".#.", "##.", ".#.", ".#.", "###"), "2": ("##.", "..#", ".#.", "#..", "###"),
    "3": ("##.", "..#", ".#.", "..#", "##."), "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "##.", "..#", "##."), "6": (".##", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", ".#.", ".#.", ".#."), "8": ("###", "#.#", "###", "#.#", "###"),
}

_SILHOUETTES = {p: (silhouette(p), outline(silhouette(p))) for p in (PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING)}
_TILES: dict[tuple, bytes] = {}


def _tile(square_color: str, piece: int, marker: int, cursor: bool) -> bytes:
    """One 16x16 square. ``marker``: 0 none, 1 quiet-move dot, 2 capture ring."""
    key = (square_color, piece, marker, cursor)
    hit = _TILES.get(key)
    if hit is not None:
        return hit
    base = IDX[square_color]
    px = [[base] * SIZE for _ in range(SIZE)]
    if piece:
        mask, edge = _SILHOUETTES[abs(piece)]
        side = "white" if piece > 0 else "black"
        fill, ink = IDX[side + "_fill"], IDX[side + "_edge"]
        for y in range(SIZE):
            for x in range(SIZE):
                if mask[y][x]:
                    px[y][x] = fill
                elif edge[y][x]:
                    px[y][x] = ink
    if marker:
        dot = IDX["light_dot" if square_color.startswith("light") else "dark_dot"]
        if marker == 1:
            for y in range(6, 10):
                for x in range(6, 10):
                    if not ((x in (6, 9)) and (y in (6, 9))):
                        px[y][x] = dot
        else:
            for i in range(SIZE):
                for t in (0, 1):
                    px[t][i] = px[SIZE - 1 - t][i] = px[i][t] = px[i][SIZE - 1 - t] = dot
    if cursor:
        c = IDX["cursor"]
        for i in range(SIZE):
            for t in (0, 1):
                px[t][i] = px[SIZE - 1 - t][i] = px[i][t] = px[i][SIZE - 1 - t] = c
    out = bytes(v for row in px for v in row)
    _TILES[key] = out
    return out


def _glyph(ch: str) -> list[tuple[int, int]]:
    return [(x, y) for y, row in enumerate(_FONT[ch]) for x, c in enumerate(row) if c == "#"]


class ChessGame(Game):
    name = "chess"
    title = "Chess"
    width = SIDE
    height = SIDE
    HINT_LINES = 4
    MENU_LINES = 6  # opponent, strength, provider, model, side, plus the key hint

    def __init__(self) -> None:
        self.board = Board()
        self.mode = "menu"
        self.opp_options = opponents.available()
        self.opp_index = 0
        host = opponents.get_host()
        if host:
            # Opened from an agent: start on that agent, so its model list is right there.
            wanted = "model" if host == "hermes" else host
            for index, (key, _label) in enumerate(self.opp_options):
                if key == wanted:
                    self.opp_index = index
                    break
        self.level = 1
        self.side = 0  # 0 white, 1 black, 2 random
        # The host's own model list; loaded in the background because two of the
        # CLIs take seconds to answer.
        self.groups: list[models.Group] = []
        self.groups_ready = False
        self._groups_for: str | None = None
        self.group_index = 0
        self.model_index = 0
        self.menu_row = 0
        self.menu_error = ""
        self.human = WHITE
        self.flipped = False
        self.cursor = 12  # e2
        self.selected: int | None = None
        self.targets: dict[int, list[int]] = {}
        self.history: list[str] = []
        self.last_move: tuple[int, int] | None = None
        self.opponent: opponents.Opponent | None = None
        self.thinking = False
        self.note = ""
        self.result = ""
        self._token = 0
        self._ready: tuple[int | None, str] | None = None
        self._lock = threading.Lock()
        self._promo: tuple[int, int] | None = None
        self._frame: Frame | None = None
        self._dirty = True
        self._now = 0.0
        self._ensure_groups()

    def _kind(self) -> str:
        return self.opp_options[self.opp_index % len(self.opp_options)][0]

    def _current_group(self) -> models.Group | None:
        if not self.groups:
            return None
        return self.groups[self.group_index % len(self.groups)]

    def _has_models(self) -> bool:
        return any(group.models for group in self.groups)

    def _ensure_groups(self, force: bool = False) -> None:
        """Start loading the selected opponent's own model list, once per selection."""
        kind = self._kind()
        if kind not in models.MODEL_KINDS:
            self.groups, self.groups_ready, self._groups_for = [], True, kind
            self.group_index = self.model_index = 0
            return
        if self._groups_for == kind and not force:
            return
        self._groups_for = kind
        self.groups, self.groups_ready = [], False
        self.group_index = self.model_index = 0
        threading.Thread(target=self._load_groups, args=(kind,), name="chess-models", daemon=True).start()

    def _load_groups(self, kind: str) -> None:
        try:
            # Catalogs are per signed-in host account. Refresh on each game so
            # a provider/account change is reflected without waiting for cache TTL.
            groups = models.for_opponent(kind, refresh=True)
        except Exception:
            groups = []
        if self._groups_for != kind:
            return  # the player moved to another opponent while we were asking
        self.groups = groups
        self.groups_ready = True
        self._dirty = True

    # -- menu --------------------------------------------------------------------------

    def _menu_rows(self) -> list[str]:
        """Which rows the menu shows: strengths for an engine, the host's models for an agent."""
        kind = self._kind()
        rows = ["opponent"]
        if kind in ("engine", "stockfish"):
            rows.append("strength")
        if len(self.groups) > 1:
            rows.append("provider")
        if kind in models.MODEL_KINDS or self._has_models():
            rows.append("model")
        rows.append("side")
        return rows

    def _menu_keys(self, key: str) -> None:
        self.opp_options = opponents.available()
        self.opp_index %= len(self.opp_options)
        rows = self._menu_rows()
        self.menu_row = min(self.menu_row, len(rows) - 1)
        if key == "up":
            self.menu_row = (self.menu_row - 1) % len(rows)
        elif key == "down":
            self.menu_row = (self.menu_row + 1) % len(rows)
        elif key in ("left", "right", ",", "."):
            d = 1 if key in ("right", ".") else -1
            step = d * (10 if key in (",", ".") else 1)
            row = rows[self.menu_row]
            if row == "opponent":
                self.opp_index = (self.opp_index + d) % len(self.opp_options)
                self._ensure_groups()
                self.menu_row = min(self.menu_row, len(self._menu_rows()) - 1)
            elif row == "strength":
                self.level = (self.level + d) % len(LEVELS)
            elif row == "provider" and self.groups:
                self.group_index = (self.group_index + d) % len(self.groups)
                self.model_index = 0
            elif row == "model":
                group = self._current_group()
                if group is not None and group.models:
                    self.model_index = (self.model_index + step) % len(group.models)
            elif row == "side":
                self.side = (self.side + d) % 3
        elif key in ("enter", "space"):
            self._start()

    def _start(self) -> None:
        kind = self._kind()
        try:
            if self.opponent:
                self.opponent.close()
            provider, model = "", ""
            group = self._current_group()
            if group is not None:
                provider = "" if group.default else group.slug
                if group.models:
                    model = group.models[self.model_index % len(group.models)]
            self.opponent = opponents.make(kind, self.level, provider, model)
        except Exception as exc:
            self.menu_error = str(exc)
            return
        self.menu_error = ""
        self.board = Board()
        self.history = []
        self.last_move = None
        self.selected = None
        self.targets = {}
        self.note = self.result = ""
        self.thinking = False
        self._token += 1
        self.human = WHITE if self.side == 0 else BLACK if self.side == 1 else random.choice((WHITE, BLACK))
        self.flipped = self.human == BLACK
        self.cursor = 12 if self.human == WHITE else 52
        self.mode = "play"
        if self.board.turn != self.human:
            self._ask_opponent()

    # -- playing -----------------------------------------------------------------------

    def _ask_opponent(self) -> None:
        self.thinking = True
        token = self._token
        board = self.board.copy()
        history = list(self.history)
        opp = self.opponent

        def work() -> None:
            try:
                move, note = opp.choose(board, history)
            except Exception as exc:
                from .engine import best_move

                move, note = best_move(board, LEVELS[1]), f"{type(exc).__name__}: the engine moved instead."
            with self._lock:
                if token == self._token:
                    self._ready = (move, note)

        threading.Thread(target=work, name="chess-opponent", daemon=True).start()

    def _apply(self, move: int) -> None:
        san = self.board.san(move)
        self.board.make(move)
        self.history.append(san)
        self.last_move = (move_from(move), move_to(move))
        self.selected, self.targets = None, {}
        out = self.board.outcome()
        if out:
            self.mode = "over"
            result, reason = out
            who = {"1-0": "White wins", "0-1": "Black wins", "1/2-1/2": "Draw"}[result]
            you = (result == "1-0" and self.human == WHITE) or (result == "0-1" and self.human == BLACK)
            self.result = f"{who} by {reason}." + (" You win!" if you else "")
        self._dirty = True

    def _select(self) -> None:
        s = self.cursor
        piece = self.board.sq[s]
        if self.selected is not None and s in self.targets:
            moves = self.targets[s]
            if len(moves) > 1:  # promotion: four moves share one target
                self._promo = (self.selected, s)
                self.mode = "promote"
                return
            self._apply(moves[0])
            if self.mode == "play":
                self._ask_opponent()
            return
        if piece and (piece > 0) == (self.human > 0):
            self.selected = s
            self.targets = {}
            for m in self.board.legal_moves():
                if move_from(m) == s:
                    self.targets.setdefault(move_to(m), []).append(m)
        else:
            self.selected, self.targets = None, {}

    def _undo(self) -> None:
        """Take back your last move (and the reply to it, if it has been played)."""
        if self.thinking:
            return
        n = 2 if self.board.turn == self.human else 1
        if len(self.history) < n:
            return
        for _ in range(n):
            self.board.unmake()
            self.history.pop()
        self.last_move = None
        self.selected, self.targets = None, {}
        self.mode = "play"
        self.result = ""
        if self.board.turn != self.human:
            self._ask_opponent()

    def handle_key(self, key: str, pressed: bool, now_ms: float) -> None:
        if not pressed:
            return
        self._dirty = True
        if self.mode == "menu":
            self._menu_keys(key)
            return
        if self.mode == "promote":
            pick = {"q": QUEEN, "r": ROOK, "b": BISHOP, "n": KNIGHT, "enter": QUEEN, "space": QUEEN}.get(key)
            if key == "backspace":
                self._promo = None
                self.mode = "play"
            elif pick:
                frm, to = self._promo
                self._promo = None
                self.mode = "play"
                self._apply(mk(frm, to, pick))
                if self.mode == "play":
                    self._ask_opponent()
            return
        if key == "n":
            self._token += 1
            self.thinking = False
            self.mode = "menu"
            return
        if key == "f":
            self.flipped = not self.flipped
            return
        if key == "u":
            self._undo()
            return
        if key == "r" and self.mode == "play":
            self._token += 1
            self.thinking = False
            self.mode = "over"
            self.result = "You resigned."
            return
        if self.mode == "over":
            if key in ("enter", "space"):
                self.mode = "menu"
            return
        if key in ("left", "right", "up", "down"):
            f, r = self.cursor & 7, self.cursor >> 3
            sign = -1 if self.flipped else 1
            if key == "left":
                f -= sign
            elif key == "right":
                f += sign
            elif key == "up":
                r += sign
            else:
                r -= sign
            self.cursor = max(0, min(7, r)) * 8 + max(0, min(7, f))
        elif key in ("enter", "space") and not self.thinking and self.board.turn == self.human:
            self._select()
        elif key == "backspace":
            self.selected, self.targets = None, {}

    def tick(self, now_ms: float) -> None:
        self._now = now_ms
        with self._lock:
            ready, self._ready = self._ready, None
        if ready is not None and self.mode == "play":
            move, note = ready
            self.thinking = False
            self.note = note
            if move is not None:
                self._apply(move)
        if self.thinking:
            self._dirty = True  # keeps the "thinking" dots moving

    # -- picture ------------------------------------------------------------------------

    def frame(self, now_ms: float) -> Frame:
        if self._frame is not None and not self._dirty:
            return self._frame
        self._dirty = False
        W = SIDE
        img = bytearray([IDX["page"]]) * (W * W)
        board = self.board
        check_sq = board.king[board.turn] if board.in_check() else -1
        for ry in range(8):
            for rx in range(8):
                f, r = (7 - rx, ry) if self.flipped else (rx, 7 - ry)
                s = r * 8 + f
                shade = "dark" if (f + r) % 2 == 0 else "light"
                if s == check_sq:
                    color = shade + "_check"
                elif s == self.selected:
                    color = shade + "_sel"
                elif self.last_move and s in self.last_move:
                    color = shade + "_last"
                else:
                    color = shade
                marker = 0
                if s in self.targets:
                    marker = 2 if board.sq[s] else 1
                    if board.sq[s] == 0 and abs(board.sq[self.selected]) == PAWN and (s & 7) != (self.selected & 7):
                        marker = 2  # en passant
                tile = _tile(color, board.sq[s], marker, self.mode in ("play", "promote") and s == self.cursor)
                x0, y0 = MARGIN + rx * SIZE, MARGIN + ry * SIZE
                for row in range(SIZE):
                    o = (y0 + row) * W + x0
                    img[o : o + SIZE] = tile[row * SIZE : (row + 1) * SIZE]
        coord = IDX["coord"]
        for i in range(8):
            f = 7 - i if self.flipped else i
            r = i if self.flipped else 7 - i
            for ch, x0, y0 in (("abcdefgh"[f], MARGIN + i * SIZE + SIZE // 2 - 1, MARGIN + BOARD + 1), (str(r + 1), 2, MARGIN + i * SIZE + SIZE // 2 - 2)):
                for gx, gy in _glyph(ch):
                    img[(y0 + gy) * W + x0 + gx] = coord
        self._frame = Frame(W, W, bytes(img), PALETTE, 0)
        return self._frame

    # -- text under the picture ----------------------------------------------------------

    def hint(self) -> str:
        lines = self._lines()
        n = self.MENU_LINES if self.mode == "menu" else self.HINT_LINES
        lines += [""] * (n - len(lines))
        return "\n".join(lines[:n])

    def _lines(self) -> list[str]:
        if self.mode == "menu":
            kind = self._kind()
            texts = {
                "opponent": f"Opponent: {self.opp_options[self.opp_index % len(self.opp_options)][1]}",
                "strength": f"Strength: {LEVELS[self.level].name}",
                "provider": f"Provider: {self._provider_text()}",
                "model": f"Model: {self._model_text()}",
                "side": f"You play: {('White', 'Black', 'Random')[self.side]}",
            }
            rows = self._menu_rows()
            shown = [("> " if i == self.menu_row else "  ") + texts[r] for i, r in enumerate(rows)]
            if self._has_models():
                tail = "Up/Down row   Left/Right change   , . jump 10   Enter starts   Esc leave"
            else:
                tail = "Up/Down row   Left/Right change   Enter starts   Esc leave"
            return shown + [self.menu_error or tail]
        if self.mode == "promote":
            return ["Promote to:  Q queen   R rook   B bishop   N knight", "Backspace cancels"]
        if self.mode == "over":
            return [self.result, f"Moves: {self._last_san()}", "Enter new game   U take back   Esc leave"]
        label = self.opponent.label if self.opponent else "Opponent"
        if self.thinking:
            dots = "." * (1 + int(self._now / 400) % 3)
            status = f"{label} is thinking{dots}"
        else:
            status = "Your move" + ("  (check!)" if self.board.in_check() else "")
        last = f"   Last: {self.history[-1]}" if self.history else ""
        lines = [status + last]
        if self.note:
            lines.append(self.note)
        lines.append("Arrows move   Enter select/move   Backspace cancel   U take back   F flip   N new   R resign   Esc leave")
        return lines

    def _provider_text(self) -> str:
        if not self.groups_ready:
            return "loading..."
        group = self._current_group()
        if group is None:
            return "this host has no model list"
        if group.default:
            return group.name
        return f"{group.name} ({len(group.models)} models)"

    def _model_text(self) -> str:
        group = self._current_group()
        if not self.groups_ready:
            return "loading..."
        if group is None or not group.models:
            if self._kind() == "model":
                current = opponents.load_catalog().current[1]
                return f"the one Hermes is running ({current})" if current else "the one Hermes is running"
            return "whatever this host is set to"
        model = group.models[self.model_index % len(group.models)]
        shown = model
        return f"{shown}   {self.model_index % len(group.models) + 1}/{len(group.models)}"

    def _last_san(self) -> str:
        return " ".join(self.history[-6:])

    def close(self) -> None:
        pass


register("chess")(ChessGame)

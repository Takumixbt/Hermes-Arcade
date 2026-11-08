"""Chess rules: board, legal moves, FEN, SAN, and game outcome.

Squares are 0..63 with a1 = 0, b1 = 1 ... h8 = 63. Pieces are signed ints:
+1..+6 for white (pawn, knight, bishop, rook, queen, king) and the negatives for
black. A move is one int: ``from | to << 6 | promotion << 12``.

Correctness is checked by perft (see ``tests/test_chess_rules.py``): the number
of legal move sequences from standard test positions must match published values.
"""

from __future__ import annotations

PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING = range(1, 7)
WHITE, BLACK = 1, -1
WK, WQ, BK, BQ = 1, 2, 4, 8

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_LETTER = {PAWN: "p", KNIGHT: "n", BISHOP: "b", ROOK: "r", QUEEN: "q", KING: "k"}
_FROM_LETTER = {v: k for k, v in _LETTER.items()}
_PROMO_LETTER = {KNIGHT: "n", BISHOP: "b", ROOK: "r", QUEEN: "q"}


def sq_name(s: int) -> str:
    return "abcdefgh"[s & 7] + str((s >> 3) + 1)


def sq_from_name(name: str) -> int:
    return "abcdefgh".index(name[0]) + (int(name[1]) - 1) * 8


def mk(frm: int, to: int, promo: int = 0) -> int:
    return frm | (to << 6) | (promo << 12)


def move_from(m: int) -> int:
    return m & 63


def move_to(m: int) -> int:
    return (m >> 6) & 63


def move_promo(m: int) -> int:
    return m >> 12


def uci(m: int) -> str:
    return sq_name(m & 63) + sq_name((m >> 6) & 63) + _PROMO_LETTER.get(m >> 12, "")


# -- precomputed geometry ------------------------------------------------------


def _build_tables():
    knight, king, rays = [], [], []
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)]  # first 4 straight, last 4 diagonal
    for s in range(64):
        f, r = s & 7, s >> 3
        knight.append([(r + dr) * 8 + f + df for df, dr in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)) if 0 <= f + df < 8 and 0 <= r + dr < 8])
        king.append([(r + dr) * 8 + f + df for df, dr in dirs if 0 <= f + df < 8 and 0 <= r + dr < 8])
        sq_rays = []
        for df, dr in dirs:
            ray, ff, rr = [], f + df, r + dr
            while 0 <= ff < 8 and 0 <= rr < 8:
                ray.append(rr * 8 + ff)
                ff += df
                rr += dr
            sq_rays.append(ray)
        rays.append(sq_rays)
    return knight, king, rays


KNIGHT_TO, KING_TO, RAYS = _build_tables()
STRAIGHT = range(0, 4)
DIAGONAL = range(4, 8)


class Board:
    __slots__ = ("sq", "turn", "castling", "ep", "halfmove", "fullmove", "king", "_undo", "_keys")

    def __init__(self, fen: str = START_FEN) -> None:
        self.set_fen(fen)

    # -- FEN ------------------------------------------------------------------

    def set_fen(self, fen: str) -> None:
        parts = fen.split()
        if len(parts) < 4:
            raise ValueError("bad FEN")
        self.sq = [0] * 64
        rank, file = 7, 0
        for ch in parts[0]:
            if ch == "/":
                rank, file = rank - 1, 0
            elif ch.isdigit():
                file += int(ch)
            else:
                piece = _FROM_LETTER[ch.lower()]
                self.sq[rank * 8 + file] = piece if ch.isupper() else -piece
                file += 1
        self.turn = WHITE if parts[1] == "w" else BLACK
        self.castling = 0
        for ch in parts[2]:
            self.castling |= {"K": WK, "Q": WQ, "k": BK, "q": BQ}.get(ch, 0)
        self.ep = -1 if parts[3] == "-" else sq_from_name(parts[3])
        self.halfmove = int(parts[4]) if len(parts) > 4 else 0
        self.fullmove = int(parts[5]) if len(parts) > 5 else 1
        self.king = {WHITE: self.sq.index(KING), BLACK: self.sq.index(-KING)}
        self._undo = []
        self._keys = [self.position_key()]

    def fen(self) -> str:
        rows = []
        for rank in range(7, -1, -1):
            row, empty = "", 0
            for file in range(8):
                p = self.sq[rank * 8 + file]
                if p == 0:
                    empty += 1
                    continue
                if empty:
                    row += str(empty)
                    empty = 0
                ch = _LETTER[abs(p)]
                row += ch.upper() if p > 0 else ch
            rows.append(row + (str(empty) if empty else ""))
        castle = "".join(c for c, bit in (("K", WK), ("Q", WQ), ("k", BK), ("q", BQ)) if self.castling & bit) or "-"
        ep = sq_name(self.ep) if self.ep >= 0 else "-"
        return f"{'/'.join(rows)} {'w' if self.turn == WHITE else 'b'} {castle} {ep} {self.halfmove} {self.fullmove}"

    def position_key(self) -> str:
        """FEN without the move counters: what 'the same position' means for repetition."""
        return " ".join(self.fen().split()[:4])

    def copy(self) -> "Board":
        return Board(self.fen())

    # -- attacks ----------------------------------------------------------------

    def attacked(self, s: int, by: int) -> bool:
        """Is square ``s`` attacked by any piece of colour ``by``?"""
        sq = self.sq
        # pawns
        f = s & 7
        if by == WHITE:
            if f > 0 and s >= 9 and sq[s - 9] == PAWN:
                return True
            if f < 7 and s >= 7 and sq[s - 7] == PAWN:
                return True
        else:
            if f > 0 and s <= 56 and sq[s + 7] == -PAWN:
                return True
            if f < 7 and s <= 54 and sq[s + 9] == -PAWN:
                return True
        n, k = KNIGHT * by, KING * by
        for t in KNIGHT_TO[s]:
            if sq[t] == n:
                return True
        for t in KING_TO[s]:
            if sq[t] == k:
                return True
        rook, bishop, queen = ROOK * by, BISHOP * by, QUEEN * by
        rays = RAYS[s]
        for d in STRAIGHT:
            for t in rays[d]:
                p = sq[t]
                if p:
                    if p == rook or p == queen:
                        return True
                    break
        for d in DIAGONAL:
            for t in rays[d]:
                p = sq[t]
                if p:
                    if p == bishop or p == queen:
                        return True
                    break
        return False

    def in_check(self, color: int | None = None) -> bool:
        c = self.turn if color is None else color
        return self.attacked(self.king[c], -c)

    # -- move generation ----------------------------------------------------------

    def pseudo_moves(self) -> list[int]:
        sq, turn = self.sq, self.turn
        out: list[int] = []
        add = out.append
        for s in range(64):
            p = sq[s]
            if p == 0 or (p > 0) != (turn > 0):
                continue
            t = abs(p)
            if t == PAWN:
                r = s >> 3
                f = s & 7
                fwd = s + 8 * turn
                promo_rank = 7 if turn == WHITE else 0
                if 0 <= fwd < 64 and sq[fwd] == 0:
                    if (fwd >> 3) == promo_rank:
                        for pr in (QUEEN, ROOK, BISHOP, KNIGHT):
                            add(s | (fwd << 6) | (pr << 12))
                    else:
                        add(s | (fwd << 6))
                        if r == (1 if turn == WHITE else 6) and sq[fwd + 8 * turn] == 0:
                            add(s | ((fwd + 8 * turn) << 6))
                for df in (-1, 1):
                    if 0 <= f + df < 8 and 0 <= fwd < 64:
                        to = fwd + df
                        target = sq[to]
                        if (target and (target > 0) != (turn > 0)) or (to == self.ep and target == 0):
                            if (to >> 3) == promo_rank:
                                for pr in (QUEEN, ROOK, BISHOP, KNIGHT):
                                    add(s | (to << 6) | (pr << 12))
                            else:
                                add(s | (to << 6))
            elif t == KNIGHT:
                for to in KNIGHT_TO[s]:
                    target = sq[to]
                    if target == 0 or (target > 0) != (turn > 0):
                        add(s | (to << 6))
            elif t == KING:
                for to in KING_TO[s]:
                    target = sq[to]
                    if target == 0 or (target > 0) != (turn > 0):
                        add(s | (to << 6))
                self._castles(s, out)
            else:
                rays = RAYS[s]
                for d in (range(8) if t == QUEEN else STRAIGHT if t == ROOK else DIAGONAL):
                    for to in rays[d]:
                        target = sq[to]
                        if target == 0:
                            add(s | (to << 6))
                            continue
                        if (target > 0) != (turn > 0):
                            add(s | (to << 6))
                        break
        return out

    def _castles(self, s: int, out: list[int]) -> None:
        turn, sq = self.turn, self.sq
        home = 4 if turn == WHITE else 60
        if s != home or self.in_check():
            return
        enemy = -turn
        k_bit, q_bit = (WK, WQ) if turn == WHITE else (BK, BQ)
        if self.castling & k_bit and sq[home + 1] == 0 and sq[home + 2] == 0 and sq[home + 3] == ROOK * turn:
            if not self.attacked(home + 1, enemy) and not self.attacked(home + 2, enemy):
                out.append(home | ((home + 2) << 6))
        if self.castling & q_bit and sq[home - 1] == 0 and sq[home - 2] == 0 and sq[home - 3] == 0 and sq[home - 4] == ROOK * turn:
            if not self.attacked(home - 1, enemy) and not self.attacked(home - 2, enemy):
                out.append(home | ((home - 2) << 6))

    def legal_moves(self) -> list[int]:
        moves = []
        me = self.turn
        for m in self.pseudo_moves():
            self.make(m)
            if not self.attacked(self.king[me], -me):
                moves.append(m)
            self.unmake()
        return moves

    # -- make / unmake ----------------------------------------------------------------

    def make(self, m: int) -> None:
        sq = self.sq
        frm, to, promo = m & 63, (m >> 6) & 63, m >> 12
        piece = sq[frm]
        turn = self.turn
        captured = sq[to]
        cap_sq = to
        if abs(piece) == PAWN and to == self.ep and captured == 0 and (frm & 7) != (to & 7):
            cap_sq = to - 8 * turn
            captured = sq[cap_sq]
            sq[cap_sq] = 0
        self._undo.append((m, piece, captured, cap_sq, self.castling, self.ep, self.halfmove, self.fullmove))
        sq[frm] = 0
        sq[to] = promo * turn if promo else piece
        if abs(piece) == KING:
            self.king[turn] = to
            if abs(to - frm) == 2:  # castling: move the rook too
                if to > frm:
                    sq[frm + 1], sq[frm + 3] = sq[frm + 3], 0
                else:
                    sq[frm - 1], sq[frm - 4] = sq[frm - 4], 0
        # castling rights
        if abs(piece) == KING:
            self.castling &= ~(WK | WQ) if turn == WHITE else ~(BK | BQ)
        for corner, bit in ((0, WQ), (7, WK), (56, BQ), (63, BK)):
            if frm == corner or to == corner:
                self.castling &= ~bit
        self.ep = frm + 8 * turn if abs(piece) == PAWN and abs(to - frm) == 16 else -1
        self.halfmove = 0 if abs(piece) == PAWN or captured else self.halfmove + 1
        if turn == BLACK:
            self.fullmove += 1
        self.turn = -turn
        self._keys.append(self.position_key())

    def unmake(self) -> None:
        m, piece, captured, cap_sq, castling, ep, halfmove, fullmove = self._undo.pop()
        self._keys.pop()
        sq = self.sq
        frm, to = m & 63, (m >> 6) & 63
        self.turn = -self.turn
        turn = self.turn
        sq[frm] = piece
        sq[to] = 0
        if cap_sq != to:
            sq[cap_sq] = captured
        else:
            sq[to] = captured
        if abs(piece) == KING:
            self.king[turn] = frm
            if abs(to - frm) == 2:
                if to > frm:
                    sq[frm + 3], sq[frm + 1] = sq[frm + 1], 0
                else:
                    sq[frm - 4], sq[frm - 1] = sq[frm - 1], 0
        self.castling, self.ep, self.halfmove, self.fullmove = castling, ep, halfmove, fullmove

    # -- notation ----------------------------------------------------------------------

    def san(self, m: int, legal: list[int] | None = None) -> str:
        """Standard algebraic notation of legal move ``m`` in this position."""
        frm, to, promo = m & 63, (m >> 6) & 63, m >> 12
        piece = abs(self.sq[frm])
        if piece == KING and abs(to - frm) == 2:
            text = "O-O" if to > frm else "O-O-O"
        else:
            capture = self.sq[to] != 0 or (piece == PAWN and (frm & 7) != (to & 7))
            if piece == PAWN:
                text = (sq_name(frm)[0] + "x" if capture else "") + sq_name(to)
            else:
                text = _LETTER[piece].upper()
                others = [o for o in (legal if legal is not None else self.legal_moves()) if o != m and (o >> 6) & 63 == to and abs(self.sq[o & 63]) == piece]
                if others:
                    if all((o & 7) != (frm & 7) for o in others):
                        text += sq_name(frm)[0]
                    elif all((o >> 3 & 7) != (frm >> 3) for o in others):
                        text += sq_name(frm)[1]
                    else:
                        text += sq_name(frm)
                text += ("x" if capture else "") + sq_name(to)
            if promo:
                text += "=" + _PROMO_LETTER[promo].upper()
        self.make(m)
        if self.in_check():
            text += "#" if not self.legal_moves() else "+"
        self.unmake()
        return text

    def find_move(self, text: str) -> int | None:
        """Parse UCI (e2e4, e7e8q) or SAN (Nf3, exd5, O-O) into a legal move, else None."""
        legal = self.legal_moves()
        t = text.strip().replace("0", "O")
        for m in legal:
            if uci(m) == t.lower():
                return m
        # a promotion without a suffix in UCI form means queen
        for m in legal:
            if uci(m) == t.lower() + "q":
                return m
        clean = t.rstrip("+#!?")
        for m in legal:
            if self.san(m, legal).rstrip("+#") == clean:
                return m
        return None

    # -- outcome ------------------------------------------------------------------------

    def insufficient_material(self) -> bool:
        pieces = [abs(p) for p in self.sq if p and abs(p) != KING]
        if not pieces:
            return True
        if len(pieces) == 1 and pieces[0] in (KNIGHT, BISHOP):
            return True
        if all(p == BISHOP for p in pieces):  # bishops all on one colour complex
            colours = {((s >> 3) + (s & 7)) & 1 for s, p in enumerate(self.sq) if abs(p) == BISHOP}
            return len(colours) == 1
        return False

    def outcome(self) -> tuple[str, str] | None:
        """``None`` while the game goes on, else ``(result, reason)`` with result 1-0, 0-1 or 1/2-1/2."""
        if not self.legal_moves():
            if self.in_check():
                return ("0-1" if self.turn == WHITE else "1-0", "checkmate")
            return ("1/2-1/2", "stalemate")
        if self.insufficient_material():
            return ("1/2-1/2", "insufficient material")
        if self._keys.count(self._keys[-1]) >= 3:
            return ("1/2-1/2", "threefold repetition")
        if self.halfmove >= 100:
            return ("1/2-1/2", "fifty-move rule")
        return None


def perft(board: Board, depth: int) -> int:
    if depth == 0:
        return 1
    total = 0
    for m in board.legal_moves():
        board.make(m)
        total += perft(board, depth - 1)
        board.unmake()
    return total

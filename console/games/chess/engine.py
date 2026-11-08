"""A small alpha-beta chess engine in pure Python.

Material plus piece-square tables (Tomasz Michniewski's "simplified evaluation"),
iterative deepening, quiescence search on captures and capture-first move
ordering. Not Stockfish: roughly a club beginner at the low levels and a
careful, tactically aware opponent at the top level, which is plenty for a game
you play while an agent works. ``Stockfish`` is used instead when found.
"""

from __future__ import annotations

import random
import shutil
import subprocess
import time
from dataclasses import dataclass

from .rules import BISHOP, BLACK, KING, KNIGHT, PAWN, QUEEN, ROOK, WHITE, Board, uci

VALUE = {PAWN: 100, KNIGHT: 320, BISHOP: 330, ROOK: 500, QUEEN: 900, KING: 0}
MATE = 100000
TIE = 4  # centipawns: root moves this close to the best count as equal

# Piece-square tables from white's point of view, listed rank 8 first (as printed in books).
_PST_TEXT = {
    PAWN: [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10, 5, 5, 10, 25, 25, 10, 5, 5, 0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5, 5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
    KNIGHT: [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30, -30, 5, 15, 20, 20, 15, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30, -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50],
    BISHOP: [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10, -10, 5, 5, 10, 10, 5, 5, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10, -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20],
    ROOK: [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0],
    QUEEN: [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 5, 5, 5, 0, -10, -5, 0, 5, 5, 5, 5, 0, -5, 0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10, -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20],
    KING: [-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20, -20, -10, 20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20],
}
# index by square 0..63 (a1 = 0) for white; black mirrors vertically
PST = {p: [t[(7 - (s >> 3)) * 8 + (s & 7)] for s in range(64)] for p, t in _PST_TEXT.items()}


def evaluate(b: Board) -> int:
    """Score from the point of view of the side to move."""
    score = 0
    for s, p in enumerate(b.sq):
        if p == 0:
            continue
        t = abs(p)
        if p > 0:
            score += VALUE[t] + PST[t][s]
        else:
            score -= VALUE[t] + PST[t][s ^ 56]
    return score if b.turn == WHITE else -score


class _Timeout(Exception):
    pass


class Searcher:
    def __init__(self, board: Board, deadline: float) -> None:
        self.b = board
        self.deadline = deadline
        self.nodes = 0

    def _check_time(self) -> None:
        self.nodes += 1
        if self.nodes & 1023 == 0 and time.monotonic() > self.deadline:
            raise _Timeout

    def _order(self, moves: list[int]) -> list[int]:
        sq = self.b.sq

        def key(m: int) -> int:
            victim = abs(sq[(m >> 6) & 63])
            score = 0
            if victim:
                score = 10 * VALUE[victim] - VALUE[abs(sq[m & 63])] // 10 + 1000
            if m >> 12:
                score += 800
            return -score

        return sorted(moves, key=key)

    def quiesce(self, alpha: int, beta: int) -> int:
        self._check_time()
        stand = evaluate(self.b)
        if stand >= beta:
            return stand
        alpha = max(alpha, stand)
        b = self.b
        me = b.turn
        sq = b.sq
        caps = [m for m in b.pseudo_moves() if sq[(m >> 6) & 63] or m >> 12 == QUEEN or ((m >> 6) & 63) == b.ep and abs(sq[m & 63]) == PAWN]
        for m in self._order(caps):
            b.make(m)
            if b.attacked(b.king[me], -me):
                b.unmake()
                continue
            score = -self.quiesce(-beta, -alpha)
            b.unmake()
            if score >= beta:
                return score
            alpha = max(alpha, score)
        return alpha

    def search(self, depth: int, alpha: int, beta: int, ply: int) -> int:
        self._check_time()
        b = self.b
        if b.halfmove >= 100:
            return 0
        if depth <= 0:
            return self.quiesce(alpha, beta)
        me = b.turn
        in_check = b.attacked(b.king[me], -me)
        best = -MATE
        legal = 0
        for m in self._order(b.pseudo_moves()):
            b.make(m)
            if b.attacked(b.king[me], -me):
                b.unmake()
                continue
            legal += 1
            score = -self.search(depth - 1 + (1 if in_check and depth == 1 else 0), -beta, -alpha, ply + 1)
            b.unmake()
            if score > best:
                best = score
            if score > alpha:
                alpha = score
                if alpha >= beta:
                    break
        if legal == 0:
            return -(MATE - ply) if in_check else 0
        return best

    def root(self, depth: int, moves: list[int]) -> list[tuple[int, int]]:
        """Score every root move at ``depth``; best first."""
        b = self.b
        scored = []
        alpha = -MATE
        for m in moves:
            b.make(m)
            score = -self.search(depth - 1, -MATE, -alpha, 1)
            b.unmake()
            scored.append((score, m))
            alpha = max(alpha, score - TIE)  # moves within TIE of the best keep exact scores
        scored.sort(key=lambda x: -x[0])
        return scored


@dataclass
class Level:
    name: str
    depth: int
    seconds: float
    blunder: float  # chance of playing the 2nd or 3rd best move instead


LEVELS = [
    Level("Beginner", 1, 0.5, 0.35),
    Level("Casual", 2, 1.5, 0.12),
    Level("Club", 3, 4.0, 0.0),
    Level("Strong", 5, 6.0, 0.0),
]


def best_move(board: Board, level: Level, rng: random.Random | None = None) -> int:
    """Pick a move for the side to move, within the level's depth and time."""
    rng = rng or random.Random()
    b = board.copy()
    legal = b.legal_moves()
    if not legal:
        raise ValueError("no legal moves")
    if len(legal) == 1:
        return legal[0]
    deadline = time.monotonic() + level.seconds
    s = Searcher(b, deadline)
    ranked = [(0, m) for m in legal]
    for depth in range(1, level.depth + 1):
        try:
            ranked = s.root(depth, [m for _, m in ranked])
        except _Timeout:
            break
        if ranked[0][0] > MATE - 100:  # a forced mate is found: deeper search cannot improve on it
            break
    if level.blunder and len(ranked) > 1 and rng.random() < level.blunder:
        return rng.choice([m for _, m in ranked[1:3]])
    top = ranked[0][0]
    return rng.choice([m for sc, m in ranked if sc > top - TIE])


# -- Stockfish (optional) -------------------------------------------------------


def find_stockfish() -> str | None:
    import os

    path = os.environ.get("CONSOLE_STOCKFISH")
    if path and os.path.exists(path):
        return path
    return shutil.which("stockfish")


class Stockfish:
    """Minimal UCI client: one process, one position at a time."""

    def __init__(self, path: str) -> None:
        self.proc = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self._send("uci")
        self._wait("uciok")
        self._send("isready")
        self._wait("readyok")

    def _send(self, line: str) -> None:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def _wait(self, token: str) -> str:
        for line in self.proc.stdout:
            if line.startswith(token):
                return line
        raise RuntimeError("stockfish exited")

    def best_move(self, board: Board, skill: int, move_ms: int) -> int | None:
        self._send(f"setoption name Skill Level value {max(0, min(20, skill))}")
        self._send(f"position fen {board.fen()}")
        self._send(f"go movetime {move_ms}")
        line = self._wait("bestmove")
        text = line.split()[1] if len(line.split()) > 1 else ""
        return board.find_move(text)

    def close(self) -> None:
        try:
            self._send("quit")
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()

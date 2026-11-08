"""The move generator must reproduce the published perft node counts."""

import time

import pytest

from console.games.chess.rules import Board, START_FEN, mk, perft, sq_from_name

# (name, fen, {depth: nodes}) from the Chess Programming Wiki perft suite
POSITIONS = [
    ("start", START_FEN, {1: 20, 2: 400, 3: 8902}),
    ("kiwipete", "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", {1: 48, 2: 2039, 3: 97862}),
    ("endgame", "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", {1: 14, 2: 191, 3: 2812, 4: 43238}),
    ("promotions", "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", {1: 6, 2: 264, 3: 9467}),
    ("mirror", "r2q1rk1/pP1p2pp/Q4n2/bbp1p3/Np6/1B3NBn/pPPP1PPP/R3K2R b KQ - 0 1", {1: 6, 2: 264, 3: 9467}),
    ("talkchess", "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", {1: 44, 2: 1486, 3: 62379}),
    ("steven", "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", {1: 46, 2: 2079, 3: 89890}),
]


@pytest.mark.parametrize("name,fen,counts", POSITIONS, ids=[p[0] for p in POSITIONS])
def test_perft(name, fen, counts):
    b = Board(fen)
    for depth, nodes in counts.items():
        assert perft(b, depth) == nodes, f"{name} depth {depth}"
    assert b.fen() == fen  # make/unmake leaves the board untouched

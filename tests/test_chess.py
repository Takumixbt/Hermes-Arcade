"""Chess: notation, outcomes, the game flow and the opponents."""

import time
from types import SimpleNamespace

import pytest

from console.games.chess import opponents
from console.games.chess.game import ChessGame
from console.games.chess.rules import BLACK, WHITE, Board, uci


def play(b, *moves):
    for text in moves:
        m = b.find_move(text)
        assert m is not None, f"{text} is not legal in {b.fen()}"
        b.make(m)


def test_san_round_trip_for_every_legal_move():
    for fen in (
        "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
        "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
    ):
        b = Board(fen)
        legal = b.legal_moves()
        sans = [b.san(m, legal) for m in legal]
        assert len(set(sans)) == len(sans), "SAN must be unambiguous"
        for m, s in zip(legal, sans):
            assert b.find_move(s) == m
            assert b.find_move(uci(m)) == m


def test_disambiguation_and_castling_notation():
    b = Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert {b.san(m) for m in b.legal_moves()} >= {"O-O", "O-O-O", "Rb1", "Rd1", "Rf1"}
    b2 = Board("k7/8/8/8/8/8/4K3/R6R w - - 0 1")
    assert {"Rad1", "Rhd1"} <= {b2.san(m).rstrip("+#") for m in b2.legal_moves()}


def test_checkmate_and_stalemate():
    b = Board()
    play(b, "f3", "e5", "g4", "Qh4")
    assert b.outcome() == ("0-1", "checkmate")
    assert Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1").outcome() == ("1/2-1/2", "stalemate")


def test_draws():
    assert Board("8/8/8/4k3/8/8/8/4K3 w - - 0 1").outcome()[1] == "insufficient material"
    assert Board("8/8/8/4k3/8/8/8/4KB2 w - - 0 1").outcome()[1] == "insufficient material"
    assert Board("8/8/8/4k3/8/8/8/3RK3 w - - 0 1").outcome() is None
    assert Board("8/8/8/4k3/8/8/8/4K2R w - - 100 80").outcome()[1] == "fifty-move rule"
    b = Board()
    play(b, *["Nf3", "Nf6", "Ng1", "Ng8"] * 2)
    assert b.outcome() == ("1/2-1/2", "threefold repetition")


def test_en_passant_promotion_and_undo():
    b = Board()
    play(b, "e4", "a6", "e5", "d5", "exd6")
    assert b.sq[35] == 0 and b.sq[43] == 1  # the captured pawn is gone, ours is on d6
    fen = "8/P7/8/8/8/8/k6K/8 w - - 0 1"
    b = Board(fen)
    assert {b.san(m).rstrip("+") for m in b.legal_moves() if (m >> 12)} == {"a8=Q", "a8=R", "a8=B", "a8=N"}
    play(b, "a8=N")
    b.unmake()
    assert b.fen() == fen


def wait(game, cond, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        game.tick(time.time() * 1000)
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def key(game, *names):
    for n in names:
        game.handle_key(n, True, 0.0)


@pytest.fixture
def game(monkeypatch):
    monkeypatch.setattr(opponents, "_LLM", None)
    g = ChessGame()
    g.level = 0  # beginner: fast
    return g


def click(game, square):
    game.cursor = square
    key(game, "enter")


def test_menu_then_a_full_move_exchange(game):
    assert game.mode == "menu"
    key(game, "enter")
    assert game.mode == "play" and game.human == WHITE
    click(game, 12)  # e2
    assert game.selected == 12 and set(game.targets) == {20, 28}
    click(game, 28)  # e4
    assert game.history == ["e4"]
    wait(game, lambda: len(game.history) == 2)
    assert game.board.turn == WHITE and not game.thinking
    assert game.board.legal_moves()


def test_cannot_pick_up_the_opponents_pieces(game):
    key(game, "enter")
    click(game, 52)  # e7, a black pawn
    assert game.selected is None


def test_playing_black_lets_the_engine_open(game):
    game.side = 1
    key(game, "enter")
    assert game.human == BLACK and game.flipped
    wait(game, lambda: len(game.history) == 1)


def test_promotion_asks_which_piece(game):
    key(game, "enter")
    game.board = Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    click(game, 48)
    click(game, 56)
    assert game.mode == "promote"
    key(game, "n")
    assert game.history[0].startswith("a8=N")


def test_undo_takes_back_both_plies(game):
    key(game, "enter")
    click(game, 12)
    click(game, 28)
    wait(game, lambda: len(game.history) == 2)
    key(game, "u")
    assert game.history == [] and game.board.fen().startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP")


def test_hint_has_a_fixed_number_of_lines(game):
    assert game.hint().count("\n") == ChessGame.MENU_LINES - 1
    key(game, "enter")
    assert game.hint().count("\n") == ChessGame.HINT_LINES - 1


def test_the_picture_changes_with_the_position(game):
    key(game, "enter")
    a = game.frame(0).pixels
    click(game, 12)
    assert game.frame(0).pixels != a  # selection and move dots are drawn


# -- the model opponent ---------------------------------------------------------------------


class FakeLlm:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, messages, **kw):
        self.prompts.append(messages)
        return SimpleNamespace(text=self.replies.pop(0), model="fake-model")


def test_model_move_is_parsed_from_chatty_replies(monkeypatch):
    monkeypatch.setattr(opponents, "_LLM", FakeLlm("I think the best move here is **Nf3**."))
    mv, note = opponents.ModelOpponent().choose(Board(), [])
    assert Board().san(mv) == "Nf3" and note == ""


def test_prompt_lists_the_legal_moves_and_history(monkeypatch):
    llm = FakeLlm("e4")
    monkeypatch.setattr(opponents, "_LLM", llm)
    opponents.ModelOpponent().choose(Board(), ["d4", "d5"])
    text = llm.prompts[0][1]["content"]
    assert "Legal moves:" in text and "1. d4 d5" in text and "FEN" in text


def test_illegal_replies_are_retried_then_the_engine_steps_in(monkeypatch):
    llm = FakeLlm("Qh5", "Ke2", "Zz9")
    monkeypatch.setattr(opponents, "_LLM", llm)
    mv, note = opponents.ModelOpponent().choose(Board(), [])
    assert mv in Board().legal_moves() and "illegal" in note
    assert len(llm.prompts) == 3


def test_a_failing_model_never_stalls_the_game(monkeypatch):
    class Boom:
        def complete(self, *a, **k):
            raise TimeoutError("slow")

    monkeypatch.setattr(opponents, "_LLM", Boom())
    mv, note = opponents.ModelOpponent().choose(Board(), [])
    assert mv in Board().legal_moves() and "unavailable" in note


def test_model_opponent_needs_hermes(monkeypatch):
    monkeypatch.setattr(opponents, "_LLM", None)
    assert ("model", "Hermes model") not in opponents.available()
    with pytest.raises(RuntimeError):
        opponents.ModelOpponent()
    monkeypatch.setattr(opponents, "_LLM", FakeLlm())
    assert ("model", "Hermes model") in opponents.available()


# -- picking the model --------------------------------------------------------------------


@pytest.fixture
def model_game(monkeypatch):
    from console.games.chess import models

    llm = FakeLlm("e4", "e5", "Nf3")
    monkeypatch.setattr(opponents, "_LLM", llm)
    monkeypatch.setattr(opponents, "HOST", None)
    groups = [
        models.Group("", "Hermes default", (), default=True),
        models.Group("anthropic", "Anthropic", ("claude-a", "claude-b", "claude-c")),
        models.Group("xai", "xAI", ("grok-1",)),
    ]
    monkeypatch.setattr(models, "for_opponent", lambda kind, refresh=False: groups if kind == "model" else [])
    g = ChessGame()
    g.level = 0
    g.opp_index = [k for k, _ in g.opp_options].index("model")
    g._ensure_groups()
    for _ in range(100):
        if g.groups_ready:
            break
        time.sleep(0.02)
    return g, llm


def test_model_menu_swaps_strength_for_provider_and_model(model_game):
    g, _ = model_game
    assert g._menu_rows() == ["opponent", "provider", "model", "side"]
    assert "the one Hermes is running" in g._model_text()
    assert len(g.hint().split("\n")) == g.MENU_LINES


def test_chosen_model_is_sent_to_hermes(model_game):
    g, llm = model_game
    g.menu_row = 1
    key(g, "right")  # provider 1: anthropic
    g.menu_row = 2
    key(g, "right")  # claude-b
    assert "claude-b" in g._model_text()
    g.side = 1  # we are Black, so the model moves first
    key(g, "enter")
    wait(g, lambda: g.history)
    assert g.opponent.label.startswith("Hermes model")
    assert (g.opponent.provider, g.opponent.model) == ("anthropic", "claude-b")


def test_changing_provider_resets_the_model_and_comma_jumps(model_game):
    g, _ = model_game
    g.menu_row = 1
    key(g, "right")
    g.menu_row = 2
    key(g, ".")
    assert g.model_index == 10 % 3
    g.menu_row = 1
    key(g, "right")
    assert g.model_index == 0 and "xAI" in g._provider_text()
    key(g, "right")  # wraps back to "Hermes default"
    assert g.group_index == 0

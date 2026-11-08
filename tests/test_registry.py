import pytest

from console import registry
from console.catalog import DEFAULT_GAME, GAMES


def test_chess_is_registered_and_default():
    assert DEFAULT_GAME == "chess"
    assert {"chess", "dino"} <= set(GAMES)


def test_a_game_is_created_once_per_process(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    registry.forget()
    a = registry.get_game("chess")
    assert registry.get_game("CHESS") is a
    registry.forget("chess")
    assert registry.get_game() is not a


def test_unknown_game_lists_the_known_ones():
    with pytest.raises(KeyError, match="dino"):
        registry.get_game("pong")

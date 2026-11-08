"""Resolve Console games. Importing this module loads the built-in ones."""

from __future__ import annotations

from .catalog import DEFAULT_GAME, GAMES, register
from .games.base import Game

# Built-in games register themselves on import.
from .games.chess.game import ChessGame  # noqa: F401
from .games.dino.game import DinoGame  # noqa: F401
from .games.kart.game import KartGame  # noqa: F401

__all__ = ["DEFAULT_GAME", "GAMES", "get_game", "register"]


_SESSION: dict[str, Game] = {}


def get_game(name: str | None = None) -> Game:
    """The game for this process. Re-opening Console resumes the same one."""
    key = (name or DEFAULT_GAME).strip().lower() or DEFAULT_GAME
    if key not in GAMES:
        known = ", ".join(sorted(GAMES)) or "(none)"
        raise KeyError(f"Unknown game {key!r}. Available: {known}")
    if key not in _SESSION:
        _SESSION[key] = GAMES[key].create()
    return _SESSION[key]


def forget(name: str | None = None) -> None:
    """Drop the remembered game (tests, or a fresh start)."""
    if name is None:
        _SESSION.clear()
    else:
        _SESSION.pop(name, None)

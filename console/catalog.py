"""Game catalog. Kept import-light so game modules can register without cycles."""

from __future__ import annotations

from typing import Callable

GAMES: dict[str, Callable] = {}
DEFAULT_GAME = "chess"


def register(name: str):
    """Class decorator that publishes a game under ``/console <name>``."""

    def wrapper(cls):
        GAMES[name] = cls
        return cls

    return wrapper

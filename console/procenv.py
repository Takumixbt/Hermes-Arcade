"""The environment a child process gets: Python, the terminal, the OS - no secrets.

A model-written kart controller and the game window both run as the same user, so
they can already read every file the user can. They must not also inherit the
provider keys and tokens sitting in the agent's environment. Everything passed
through here is needed to start Python on Windows or to detect and draw in the
terminal; none of it is a credential. An allowlist is used rather than a blocklist
so a new provider key added to Hermes later cannot leak by being forgotten.
"""

from __future__ import annotations

import os

#: Names copied from the parent environment. Deliberately no ``*_API_KEY``,
#: ``*_TOKEN``, ``*_SECRET`` or Hermes provider variables.
_KEEP = (
    # Python itself
    "PATH", "PATHEXT", "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE",
    # Windows process basics
    "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    # Per-user paths needed by Path.home(), config discovery and Windows Terminal
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    # Locale
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
    # Terminal detection, plus Console's own display preferences
    "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "COLORTERM",
    "WT_SESSION", "WT_PROFILE_ID",
    "CONSOLE_SIXEL", "CONSOLE_TERMINAL", "CONSOLE_SOUND", "CONSOLE_STOCKFISH",
    "CONSOLE_DEBUG",
)


def child_env(**extra: str) -> dict[str, str]:
    """A minimal environment for a child that must not see the agent's keys.

    ``extra`` adds the few values the child needs to know (``CONSOLE_HOST``,
    ``CONSOLE_HOME``, an adjusted ``PYTHONPATH``). Nothing from ``os.environ``
    is copied unless it is named in ``_KEEP``; empty overrides are dropped so a
    blank variable is not created.
    """
    env = {name: os.environ[name] for name in _KEEP if name in os.environ}
    env.update({name: value for name, value in extra.items() if value})
    return env

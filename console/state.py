"""What Console remembers: high scores on disk, the agent's activity in memory.

High scores live in ``console.json``. If Hermes is installed, that is Hermes'
home directory, so the plugin and a standalone game share one score file;
otherwise it is Console's own config directory. ``CONSOLE_HOME`` overrides both.
The activity flag is just a process-wide object: the plugin hooks and the game
run in the same process, so no state file is needed to connect them.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path


def home_dir() -> Path:
    env = os.environ.get("CONSOLE_HOME")
    if env:
        return Path(env)
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "hermes")
    candidates.append(Path.home() / ".hermes")
    for c in candidates:
        if c.is_dir():
            return c
    # Standalone: a normal per-user config directory rather than a Hermes one.
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "console"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "console"


def state_path() -> Path:
    return home_dir() / "console.json"


def _load() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_high_score(game: str) -> int:
    try:
        return max(0, int(_load().get("high_scores", {}).get(game, 0)))
    except (TypeError, ValueError):
        return 0


def save_high_score(game: str, score: int) -> None:
    data = _load()
    scores = data.setdefault("high_scores", {})
    if int(scores.get(game, 0)) >= int(score):
        return
    scores[game] = int(score)
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a missing high score is not worth interrupting a game


class AgentActivity:
    """Is the agent working? Written by the plugin hooks, read by the game screen."""

    def __init__(self, host: str = "Agent") -> None:
        self.host = host
        self._lock = threading.Lock()
        self.busy = False
        self.finished = False  # a run completed since the last time the player looked

    def started(self) -> None:
        with self._lock:
            self.busy = True
            self.finished = False

    def ended(self, completed: bool) -> None:
        with self._lock:
            self.busy = False
            self.finished = bool(completed)

    def acknowledge(self) -> None:
        with self._lock:
            self.finished = False

    def snapshot(self) -> tuple[bool, bool]:
        with self._lock:
            return self.busy, self.finished


# Kept for the tests and for anyone importing the old name.
HermesActivity = AgentActivity

ACTIVITY = AgentActivity()

"""Hermes plugin root.

Hermes loads this file as ``hermes_plugins.console`` and treats the repository
folder as the package, so the ``console/`` directory is a submodule and every
import inside it is relative. The chess game and the ``console`` CLI are also
usable without Hermes (``console chess``), and ``console install`` wires the
same game into Claude Code, Codex and OpenCode.
"""

from .console.hermes_plugin import register

__all__ = ["register"]

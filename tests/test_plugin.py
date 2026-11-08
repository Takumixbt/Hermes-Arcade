"""The Hermes plugin entry point, exercised with a stand-in for Hermes' context."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from console import hermes_plugin as plugin
from console.state import ACTIVITY

ROOT = Path(__file__).resolve().parent.parent


class FakeCtx:
    def __init__(self):
        self.commands = {}
        self.hooks = {}

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = (handler, description, args_hint)

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.fixture(autouse=True)
def _no_hotkey_thread(monkeypatch):
    monkeypatch.setattr(plugin, "_hotkey_watcher", lambda: None)


def test_register_adds_command_and_hooks():
    ctx = FakeCtx()
    plugin.register(ctx)
    assert "console" in ctx.commands
    assert {"pre_llm_call", "post_llm_call", "on_session_end"} <= set(ctx.hooks)


def test_hooks_drive_the_activity_flag():
    ctx = FakeCtx()
    plugin.register(ctx)
    ctx.hooks["pre_llm_call"](user_message="hi")
    assert ACTIVITY.snapshot() == (True, False)
    ctx.hooks["post_llm_call"](assistant_response="done")
    assert ACTIVITY.snapshot() == (False, True)
    ACTIVITY.acknowledge()


def test_hooks_never_inject_context_into_the_model():
    # pre_llm_call return values are treated as extra context by Hermes.
    ctx = FakeCtx()
    plugin.register(ctx)
    assert ctx.hooks["pre_llm_call"](user_message="hi") is None
    ACTIVITY.ended(False)


def test_register_refuses_a_hermes_without_slash_commands():
    with pytest.raises(RuntimeError):
        plugin.register(object())


def test_help_and_unknown_game():
    text = plugin.handle_console("help")
    assert "dino" in text and "Ctrl+O" in text
    assert "Unknown game" in plugin.handle_console("pong")


def test_gateway_gets_a_polite_refusal(monkeypatch):
    monkeypatch.setenv("HERMES_GATEWAY", "1")
    assert plugin.handle_console("") == plugin.CLI_ONLY


def test_no_terminal_no_game(monkeypatch):
    monkeypatch.delenv("HERMES_GATEWAY", raising=False)
    monkeypatch.setattr(plugin, "_prompt_toolkit_app", lambda: None)
    monkeypatch.setattr(sys, "stdin", None)
    assert plugin.handle_console("dino") == plugin.CLI_ONLY


def test_the_command_hands_the_game_to_the_terminal_layer(monkeypatch):
    calls = []
    monkeypatch.delenv("HERMES_GATEWAY", raising=False)
    monkeypatch.setattr(plugin, "_prompt_toolkit_app", lambda: None)

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(plugin, "_play_game", lambda name: calls.append(name) or "Left Console.")
    assert plugin.handle_console("  /DINO now ") == "Left Console."
    assert calls == ["dino"]


def test_only_one_console_at_a_time():
    assert plugin._playing.acquire(blocking=False)
    try:
        assert plugin._play_game("dino") == "Console is already open."
    finally:
        plugin._playing.release()


def test_loading_the_way_hermes_does():
    """Hermes imports the repo's __init__.py as a package named hermes_plugins.console."""
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.console", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("hermes_plugins", types.ModuleType("hermes_plugins"))
    sys.modules["hermes_plugins.console"] = module
    try:
        spec.loader.exec_module(module)
        assert callable(module.register)
    finally:
        sys.modules.pop("hermes_plugins", None)
        sys.modules.pop("hermes_plugins.console", None)
        for name in [n for n in sys.modules if n.startswith("hermes_plugins.console.")]:
            sys.modules.pop(name, None)

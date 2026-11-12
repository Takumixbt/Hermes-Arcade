"""The loopback bridge that lets a spawned game window use Hermes' model."""

from __future__ import annotations

import types

import pytest

from console import llm_bridge as bridge
from console.games.chess import opponents
from console.games.chess.rules import Board


class FakeLLM:
    def __init__(self, text: str = "e4", model: str = "fake-model") -> None:
        self.text = text
        self.model = model
        self.calls: list[tuple] = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return types.SimpleNamespace(text=self.text, model=self.model)


@pytest.fixture(autouse=True)
def _clean_bridge():
    bridge.stop()
    yield
    bridge.stop()


def _round_trip(llm=None):
    llm = llm or FakeLLM()
    env = bridge.start(llm)
    assert env is not None
    return llm, env


def test_bridge_round_trip_forwards_messages_and_limits():
    llm, env = _round_trip()
    result = bridge.complete(
        env[bridge.URL_ENV],
        env[bridge.TOKEN_ENV],
        [{"role": "user", "content": "play e4"}],
        provider="p",
        model="m",
        max_tokens=600,
        timeout=120,
        purpose="console chess move",
    )

    assert result == {"ok": True, "text": "e4", "model": "fake-model"}
    messages, kwargs = llm.calls[0]
    assert messages == [{"role": "user", "content": "play e4"}]
    assert kwargs == {"provider": "p", "model": "m", "max_tokens": 600, "timeout": 120, "purpose": "console chess move"}


def test_bridge_rejects_a_wrong_token():
    _llm, env = _round_trip()
    with pytest.raises(RuntimeError, match="unauthorized"):
        bridge.complete(env[bridge.URL_ENV], "x" * 32, [{"role": "user", "content": "move"}])


def test_bridge_rejects_a_remote_url(monkeypatch):
    monkeypatch.setenv(bridge.URL_ENV, "http://example.com:1234")
    monkeypatch.setenv(bridge.TOKEN_ENV, "x" * 32)
    assert bridge.config_from_env() is None


def test_bridge_needs_a_real_token(monkeypatch):
    monkeypatch.setenv(bridge.URL_ENV, "http://127.0.0.1:1234")
    monkeypatch.setenv(bridge.TOKEN_ENV, "short")
    assert bridge.config_from_env() is None


def test_bridge_rejects_an_invalid_port(monkeypatch):
    monkeypatch.setenv(bridge.URL_ENV, "http://127.0.0.1:not-a-port")
    monkeypatch.setenv(bridge.TOKEN_ENV, "x" * 32)
    assert bridge.config_from_env() is None


def test_a_model_opponent_plays_through_the_bridge(monkeypatch):
    _llm, env = _round_trip(FakeLLM("e4", "fake-model"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(opponents, "_LLM", None)

    opponent = opponents.ModelOpponent()
    move, note = opponent.choose(Board(), [])

    assert Board().san(move) == "e4"
    assert note == ""
    assert "fake-model" in opponent.label


def test_the_menu_lists_the_model_when_the_bridge_is_configured(monkeypatch):
    monkeypatch.setattr(opponents, "_LLM", None)
    monkeypatch.setattr(opponents, "HOST", "hermes")
    monkeypatch.setattr(opponents.engine, "find_stockfish", lambda: None)
    monkeypatch.setattr(opponents.shutil, "which", lambda _name: None)
    monkeypatch.setenv(bridge.URL_ENV, "http://127.0.0.1:1234")
    monkeypatch.setenv(bridge.TOKEN_ENV, "x" * 32)

    assert opponents.llm_available() is True
    assert opponents.available() == [("engine", "Built-in engine"), ("model", "Hermes model")]


def test_without_a_bridge_or_a_handle_the_model_is_not_offered(monkeypatch):
    monkeypatch.setattr(opponents, "_LLM", None)
    monkeypatch.delenv(bridge.URL_ENV, raising=False)
    monkeypatch.delenv(bridge.TOKEN_ENV, raising=False)

    with pytest.raises(RuntimeError, match="no Hermes model"):
        opponents.ModelOpponent()

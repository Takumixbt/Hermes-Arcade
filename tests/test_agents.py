"""The parts that let Claude Code, Codex and OpenCode be the opponent."""

import pytest

from console.games.chess import opponents
from console.games.chess.rules import Board


class FakeProc:
    def __init__(self, stdout="", stderr="", code=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, code


def test_a_cli_opponent_builds_the_non_interactive_command(monkeypatch):
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opponents, "_neutral_cwd", lambda: "/tmp/console")
    opp = opponents.CliOpponent("claude")
    argv = opp.argv("Best move?")
    assert argv[1:4] == ["-p", "Best move?", "--output-format"]
    assert argv[4] == "json"
    assert opp.label == "Claude Code"


def test_a_cli_model_can_be_chosen_by_environment(monkeypatch):
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opponents, "_neutral_cwd", lambda: "/tmp/console")
    monkeypatch.setenv("CONSOLE_CODEX_MODEL", "gpt-5.6-luna")
    argv = opponents.CliOpponent("codex").argv("move")
    assert "-m" in argv and "gpt-5.6-luna" in argv
    assert "--skip-git-repo-check" in argv and "--sandbox" in argv


def test_an_agent_reply_becomes_a_move(monkeypatch):
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opponents, "_neutral_cwd", lambda: "/tmp/console")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        return FakeProc(stdout="I would play **Nf3** here.\n")

    monkeypatch.setattr(opponents.subprocess, "run", fake_run)
    move, note = opponents.CliOpponent("opencode").choose(Board(), [])
    assert Board().san(move) == "Nf3" and note == ""
    assert "\n" not in seen["argv"][-1], "cmd.exe shims split arguments at newlines"
    assert seen["cwd"] == "/tmp/console"


def test_claude_reports_the_concrete_model_used_for_an_alias(monkeypatch):
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opponents, "_neutral_cwd", lambda: "/tmp/console")
    monkeypatch.setattr(
        opponents.subprocess,
        "run",
        lambda *a, **k: FakeProc(
            stdout=(
                '{"result":"Nf3","modelUsage":{"claude-opus-5-5":'
                '{"canonicalModel":"claude-opus-5-5"}}}'
            )
        ),
    )
    agent = opponents.CliOpponent("claude", model="opus")

    assert agent._ask([{"role": "user", "content": "move"}]) == "Nf3"
    assert agent.label == "Claude Code (claude-opus-5-5)"


def test_claude_catalog_includes_current_aliases_and_known_exact_ids(monkeypatch):
    from console.games.chess import models

    monkeypatch.setattr(models, "_claude_aliases", lambda: ["opus", "sonnet", "fable"])
    monkeypatch.setattr(models, "_claude_ids", lambda: ["claude-opus-5-5", "claude-sonnet-5-5"])

    assert models.claude_models()[0].models == (
        "opus", "sonnet", "fable", "claude-opus-5-5", "claude-sonnet-5-5"
    )


def test_refresh_uses_the_current_host_catalog(monkeypatch):
    from console.games.chess import models

    calls = []
    monkeypatch.setattr(models, "_MEMORY", {})
    monkeypatch.setattr(models, "_from_cache", lambda _kind: None)
    monkeypatch.setitem(
        models.BUILDERS,
        "codex",
        lambda: (calls.append("loaded") or [models.Group("", "Codex", ("account-model",))]),
    )

    models.for_opponent("codex")
    fresh = models.for_opponent("codex", refresh=True)

    assert calls == ["loaded", "loaded"]
    assert fresh[0].models == ("account-model",)


def test_a_failed_agent_falls_back_to_the_engine(monkeypatch):
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opponents, "_neutral_cwd", lambda: "/tmp/console")
    monkeypatch.setattr(opponents.subprocess, "run", lambda *a, **k: FakeProc(stderr="not logged in", code=1))
    move, note = opponents.CliOpponent("codex").choose(Board(), [])
    assert move in Board().legal_moves()
    assert "unavailable" in note and "exited 1" in note


def test_installed_agents_are_offered_in_the_menu(monkeypatch):
    monkeypatch.setattr(opponents, "_LLM", None)
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "claude" else None)
    monkeypatch.setattr(opponents.engine, "find_stockfish", lambda: None)
    assert ("claude", "Claude Code") in opponents.available()
    assert not any(key == "model" for key, _ in opponents.available())


@pytest.mark.parametrize("host", ("claude", "codex", "opencode"))
def test_a_host_only_sees_its_own_agent(monkeypatch, host):
    monkeypatch.setattr(opponents, "HOST", host)
    monkeypatch.setattr(opponents, "_LLM", FakeProc())
    monkeypatch.setattr(opponents.engine, "find_stockfish", lambda: None)
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}")

    options = opponents.available()
    assert [key for key, _label in options if key in opponents.AGENT_CLIS] == [host]
    assert not any(key in ("model", "api") for key, _label in options)


def test_hermes_only_sees_hermes_when_opened_from_hermes(monkeypatch):
    monkeypatch.setattr(opponents, "HOST", "hermes")
    monkeypatch.setattr(opponents, "_LLM", FakeProc())
    monkeypatch.setattr(opponents.engine, "find_stockfish", lambda: None)
    monkeypatch.setattr(opponents.shutil, "which", lambda _name: "/usr/bin/available")

    assert opponents.available() == [("engine", "Built-in engine"), ("model", "Hermes model")]


def test_ansi_and_chatter_are_ignored_when_parsing():
    move = opponents._extract(Board(), "\x1b[1mThe move is: **Nf3**\x1b[0m")
    assert move is not None and Board().san(move) == "Nf3"


def test_a_tagged_answer_wins_over_the_echoed_move_list():
    echoed = (
        "user\nPosition (FEN): ...\nLegal moves: e4, d4, Nf3, c4\n\n"
        "codex\n<move>Nf3</move>\ntokens used: 42"
    )
    move = opponents._extract(Board(), echoed)
    assert move is not None and Board().san(move) == "Nf3"


def test_codex_can_write_only_the_last_message(monkeypatch):
    monkeypatch.setattr(opponents.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opponents, "_neutral_cwd", lambda: "/tmp/console")
    argv = opponents.CliOpponent("codex").argv("move", "/tmp/answer.txt")
    assert argv[-3:-1] == ["-o", "/tmp/answer.txt"]


def test_the_menu_hides_strength_for_agent_opponents(monkeypatch):
    from console.games.chess.game import ChessGame

    monkeypatch.setattr(opponents, "_LLM", None)
    monkeypatch.setattr(opponents, "HOST", None)
    monkeypatch.setattr("console.games.chess.models.for_opponent", lambda _kind, refresh=False: [])
    monkeypatch.setattr(
        opponents, "available", lambda: [("engine", "Built-in engine"), ("claude", "Claude Code")]
    )
    game = ChessGame()
    game.opp_index = 0
    assert game._menu_rows() == ["opponent", "strength", "side"]
    game.opp_index = 1
    assert game._menu_rows() == ["opponent", "model", "side"]


# -- the API opponent --------------------------------------------------------------------------


def test_an_openai_compatible_endpoint_is_used(monkeypatch):
    monkeypatch.setenv("CONSOLE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CONSOLE_LLM_MODEL", "qwen3")
    monkeypatch.delenv("CONSOLE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CONSOLE_LLM_PROTOCOL", raising=False)
    assert opponents.api_configured()
    calls = {}

    def fake_post(url, body, headers, timeout):
        calls.update(url=url, body=body, headers=headers)
        return {"choices": [{"message": {"content": "e4"}}]}

    monkeypatch.setattr(opponents, "_post_json", fake_post)
    opp = opponents.ApiOpponent()
    move, note = opp.choose(Board(), [])
    assert Board().san(move) == "e4" and note == ""
    assert calls["url"] == "http://localhost:11434/v1/chat/completions"
    assert calls["body"]["model"] == "qwen3"
    assert "authorization" not in calls["headers"]


def test_anthropic_uses_the_messages_api(monkeypatch):
    monkeypatch.setenv("CONSOLE_LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("CONSOLE_LLM_API_KEY", "sk-ant")
    monkeypatch.setenv("CONSOLE_LLM_MODEL", "claude-sonnet-5-5")
    calls = {}

    def fake_post(url, body, headers, timeout):
        calls.update(url=url, body=body, headers=headers)
        return {"content": [{"type": "text", "text": "Nf3"}]}

    monkeypatch.setattr(opponents, "_post_json", fake_post)
    move, _ = opponents.ApiOpponent().choose(Board(), [])
    assert Board().san(move) == "Nf3"
    assert calls["url"].endswith("/messages")
    assert calls["headers"]["x-api-key"] == "sk-ant"
    assert calls["headers"]["anthropic-version"]


def test_an_api_opponent_needs_a_model(monkeypatch):
    monkeypatch.delenv("CONSOLE_LLM_MODEL", raising=False)
    monkeypatch.delenv("CONSOLE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert not opponents.api_configured()
    with pytest.raises(RuntimeError):
        opponents.ApiOpponent()

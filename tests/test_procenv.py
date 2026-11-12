"""The child processes get a working environment, but not the agent's secrets."""

from __future__ import annotations

from console.games.kart import agent
from console.procenv import child_env


def test_child_env_passes_the_os_and_drops_credentials(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("HERMES_SESSION_TOKEN", "session-secret")

    env = child_env()

    assert env["PATH"] == "/usr/bin"
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "HERMES_SESSION_TOKEN"):
        assert name not in env


def test_child_env_keeps_the_values_a_child_needs(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    monkeypatch.setenv("CONSOLE_SIXEL", "1")

    env = child_env(CONSOLE_HOST="hermes", PYTHONPATH="/repo")

    assert env["TERM"] == "xterm-256color"
    assert env["LANG"] == "en_GB.UTF-8"
    assert env["CONSOLE_SIXEL"] == "1"
    assert env["CONSOLE_HOST"] == "hermes"
    assert env["PYTHONPATH"] == "/repo"


def test_run_policy_does_not_hand_the_model_written_controller_our_keys(monkeypatch):
    seen = {}

    class Proc:
        stdout = '{"finished": false}'
        stderr = ""

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs["env"]
        return Proc()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    monkeypatch.setattr(agent.subprocess, "run", fake_run)

    result = agent.run_policy("def act(state):\n    return {'lane': 1, 'throttle': 1.0}\n")

    assert result == {"finished": False}
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "OPENAI_API_KEY" not in seen["env"]
    assert seen["env"]["PYTHONIOENCODING"] == "utf-8"
    assert str(agent._repo_root()) in seen["env"]["PYTHONPATH"]

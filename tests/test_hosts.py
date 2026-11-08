"""Opening a pane next to an agent, and the slash-command files Console installs."""

import json

import pytest

from console import hosts


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "TMUX", "WEZTERM_PANE", "CONSOLE_TERMINAL"):
        monkeypatch.delenv(name, raising=False)


def test_install_writes_the_claude_commands_and_is_idempotent(tmp_path):
    done, errors = hosts.install("claude", home=tmp_path)
    assert not errors
    path = tmp_path / ".claude" / "commands" / "console.md"
    assert path.exists() and hosts.MARKER in path.read_text(encoding="utf-8")
    assert "console open chess --host claude" in path.read_text(encoding="utf-8")
    assert (tmp_path / ".claude" / "commands" / "console-dino.md").exists()

    done, errors = hosts.install("claude", home=tmp_path)
    assert not errors and any("up to date" in line.lower() for line in done)


def test_install_never_clobbers_a_file_console_did_not_write(tmp_path):
    path = tmp_path / ".claude" / "commands" / "console.md"
    path.parent.mkdir(parents=True)
    path.write_text("# my own command\n", encoding="utf-8")

    done, errors = hosts.install("claude", home=tmp_path)
    assert errors and "not written by Console" in errors[0]
    assert path.read_text(encoding="utf-8") == "# my own command\n"

    done, errors = hosts.install("claude", home=tmp_path, force=True)
    assert not errors and "console open chess" in path.read_text(encoding="utf-8")


def test_dry_run_writes_nothing(tmp_path):
    done, errors = hosts.install("codex", home=tmp_path, dry_run=True)
    assert not errors and done and all(line.startswith("Would write") for line in done)
    assert not (tmp_path / ".codex").exists()


def test_codex_home_and_opencode_xdg_are_respected(tmp_path, monkeypatch):
    codex_home = tmp_path / "elsewhere"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    hosts.install("codex", home=tmp_path)
    codex_command = codex_home / "prompts" / "console.md"
    assert codex_command.exists()
    assert "console open chess --host codex" in codex_command.read_text(encoding="utf-8")

    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    hosts.install("opencode", home=tmp_path)
    opencode_command = xdg / "opencode" / "commands" / "console.md"
    assert opencode_command.exists()
    assert "console open chess --host opencode" in opencode_command.read_text(encoding="utf-8")


def test_doctor_marks_old_host_commands_out_of_date(tmp_path):
    hosts.install("opencode", home=tmp_path)
    command = hosts.targets("opencode", home=tmp_path)[0]
    command.path.write_text(command.text.replace("--host opencode", ""), encoding="utf-8")

    state, detail = hosts._command_status("opencode", home=tmp_path)
    assert state == "warn"
    assert "out of date" in detail
    assert "console install opencode" in detail


def test_project_install_stays_in_the_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    hosts.install("opencode", project=True)
    assert (tmp_path / ".opencode" / "commands" / "console.md").exists()


def test_hermes_and_terminal_print_instructions_instead_of_files(tmp_path):
    done, errors = hosts.install("hermes", home=tmp_path)
    assert not errors and any("hermes plugins install" in line for line in done)

    done, errors = hosts.install("terminal", home=tmp_path)
    assert not errors
    text = "\n".join(done)
    assert "ctrl+o" in text and "splitPane" in text and "tmux" in text


def test_windows_terminal_gets_a_vertical_split(monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", True)
    monkeypatch.setattr(hosts, "IS_MACOS", False)
    monkeypatch.setattr(hosts.shutil, "which", lambda name: r"C:\wt.exe" if name == "wt" else None)
    argv = ["python", "-m", "console", "chess"]
    command = hosts.terminal_command(argv)
    assert command[0] == r"C:\wt.exe"
    assert "sp" in command and command[-len(argv):] == argv


def test_windows_falls_back_to_a_new_window_without_windows_terminal(monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", True)
    monkeypatch.setattr(hosts, "IS_MACOS", False)
    monkeypatch.setattr(hosts.shutil, "which", lambda name: None)
    argv = ["python", "-m", "console", "chess"]
    assert hosts.terminal_command(argv) == ["cmd", "/c", "start", "", *argv]


def test_tmux_gets_a_split_window(monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", False)
    monkeypatch.setattr(hosts, "IS_MACOS", False)
    monkeypatch.setenv("TMUX", "1")
    monkeypatch.setattr(hosts.shutil, "which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    argv = ["python", "-m", "console", "chess"]
    command = hosts.terminal_command(argv)
    assert command[:3] == ["/usr/bin/tmux", "split-window", "-h"]


def test_macos_opens_terminal(monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", False)
    monkeypatch.setattr(hosts, "IS_MACOS", True)
    command = hosts.terminal_command(["python", "-m", "console", "chess"])
    assert command[0] == "osascript" and "Terminal" in command[2]


def test_no_terminal_means_a_printable_fallback(monkeypatch):
    monkeypatch.setattr(hosts, "terminal_command", lambda argv: None)
    command, argv = hosts.launch("chess")
    assert command is None and argv[-1] == "chess"
    assert "another terminal" in hosts.open_message("chess")


def test_hermes_status_is_summarized_without_returning_the_table():
    raw = """
    Model:      grok-4.5
    Provider:   xAI Grok OAuth (Premium+)
    Auth Providers
      xAI OAuth  ✗ not logged in
        Error: Invalid refresh token
    API-Key Providers
    """
    auth, model = hosts._hermes_status(raw)
    assert auth == "not logged in (provider credentials need refreshing)"
    assert model == "grok-4.5 via xAI Grok OAuth (Premium+); available models load from the active Hermes plugin"
    assert "Invalid refresh token" not in auth


def test_wt_action_splits_then_zooms_the_game_pane():
    action = hosts.wt_action("chess", "ctrl+o")
    assert action["keys"] == "ctrl+o"
    macro = action["command"]
    assert macro["action"] == "multipleActions"
    split, zoom = macro["actions"]
    assert split["action"] == "splitPane" and split["commandline"] == "console chess"
    assert split["split"] == "right"
    assert zoom["action"] == "togglePaneZoom"


def _write_settings(tmp_path, data):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    return path


def test_apply_wt_keybinding_adds_the_action_and_a_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", True)
    original = {"actions": [], "keybindings": [{"id": "Terminal.CopyToClipboard", "keys": "ctrl+c"}]}
    path = _write_settings(tmp_path, original)

    done, errors = hosts.apply_wt_keybinding(path)
    assert not errors and any("Bound ctrl+o" in line for line in done)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["actions"][0] == hosts.wt_action()
    # The rest of the user's settings survive untouched.
    assert written["keybindings"] == original["keybindings"]

    backup = path.with_name("settings.json.console.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == original


def test_apply_wt_keybinding_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", True)
    path = _write_settings(tmp_path, {"actions": [hosts.wt_action()], "keybindings": []})

    done, errors = hosts.apply_wt_keybinding(path)
    assert not errors and any("already bound" in line for line in done)
    assert json.loads(path.read_text(encoding="utf-8"))["actions"] == [hosts.wt_action()]


def test_apply_wt_keybinding_respects_a_key_the_user_already_uses(tmp_path, monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", True)
    # Windows Terminal rewrites `{keys: ctrl+o}` entries into an id + keybindings pair.
    path = _write_settings(
        tmp_path,
        {"actions": [], "keybindings": [{"id": "User.mine", "keys": "ctrl+o"}]},
    )

    done, errors = hosts.apply_wt_keybinding(path)
    assert not errors and any("already bound" in line for line in done)
    assert json.loads(path.read_text(encoding="utf-8"))["actions"] == []


def test_apply_wt_keybinding_creates_missing_actions_list(tmp_path, monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", True)
    path = _write_settings(tmp_path, {"defaultProfile": "{abc}"})

    done, errors = hosts.apply_wt_keybinding(path)
    assert not errors
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["defaultProfile"] == "{abc}" and written["actions"] == [hosts.wt_action()]


def test_apply_wt_keybinding_refuses_commented_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", True)
    path = tmp_path / "settings.json"
    text = '{\n  // my comment\n  "actions": []\n}\n'
    path.write_text(text, encoding="utf-8")

    done, errors = hosts.apply_wt_keybinding(path)
    assert errors and "plain JSON" in errors[0]
    assert path.read_text(encoding="utf-8") == text


def test_apply_wt_keybinding_only_touches_windows_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(hosts, "IS_WINDOWS", False)
    path = _write_settings(tmp_path, {"actions": []})

    done, errors = hosts.apply_wt_keybinding(path)
    assert errors and "tmux" in errors[0]
    assert json.loads(path.read_text(encoding="utf-8")) == {"actions": []}


def test_apply_wt_keybinding_without_settings_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(hosts, "_wt_settings_path", lambda: None)
    done, errors = hosts.apply_wt_keybinding()
    assert errors and "settings.json was not found" in errors[0]

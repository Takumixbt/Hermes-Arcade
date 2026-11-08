"""Where Console runs, and how each agent CLI is told to open it.

Hermes owns a Python TUI, so it can hand the terminal over to a game in place
(see ``hermes_plugin``). Claude Code, Codex and OpenCode own their terminal with
their own UI and offer no such hand-off, so Console opens *next to* them in a new
pane or window. ``console open`` does the spawning; ``console install`` writes the
small slash-command file each host reads, and ``console install terminal --apply``
binds one key in Windows Terminal that splits and zooms the game over the agent.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"

#: Files Console writes carry this line, so an unrelated file is never overwritten by accident.
MARKER = "<!-- console:managed -->"

HOSTS = ("claude", "codex", "opencode", "hermes", "terminal")

WT_SNIPPET = """\
{
  "keys": "ctrl+o",
  "command": {
    "action": "multipleActions",
    "actions": [
      {
        "action": "splitPane",
        "split": "right",
        "size": 0.5,
        "commandline": "console chess"
      },
      {
        "action": "togglePaneZoom"
      }
    ]
  }
}"""

TMUX_SNIPPET = """\
# ~/.tmux.conf - No prefix key, opens chess in a full-screen popup over the agent
bind-key -n C-o display-popup -E -w 90% -h 90% 'console chess'"""


# -- opening a game ----------------------------------------------------------------------------


def console_argv(game: str, host: str | None = None) -> list[str]:
    """The standalone command, run with the same Python that owns the install."""
    argv = [sys.executable, "-m", "console", game]
    if host:
        argv += ["--host", host]
    return argv


def quote_command(argv: list[str]) -> str:
    """A copy-pasteable command for the platform Console is running on."""
    return subprocess.list2cmdline(argv) if IS_WINDOWS else shlex.join(argv)


def _wt_argv() -> list[str] | None:
    wt = shutil.which("wt")
    if wt is None:
        return None
    # -w 0: the current Windows Terminal window, or a new one if there is none.
    return [wt, "-w", "0", "sp", "-V", "--size", "0.45", "--title", "Console", "--"]


def _posix_terminal(argv: list[str]) -> list[str] | None:
    if os.environ.get("TMUX"):
        tmux = shutil.which("tmux")
        if tmux:
            return [tmux, "split-window", "-h", "-c", os.getcwd(), shlex.join(argv)]
    if os.environ.get("WEZTERM_PANE"):
        wezterm = shutil.which("wezterm")
        if wezterm:
            return [wezterm, "cli", "split-pane", "--right", "--", *argv]
    wanted = os.environ.get("CONSOLE_TERMINAL", "").strip()
    if wanted:
        return [*shlex.split(wanted), "-e", *argv]
    for name, build in (
        ("wezterm", lambda p: [p, "start", "--", *argv]),
        ("kitty", lambda p: [p, "--", *argv]),
        ("alacritty", lambda p: [p, "-e", *argv]),
        ("gnome-terminal", lambda p: [p, "--", *argv]),
        ("konsole", lambda p: [p, "-e", *argv]),
        ("xfce4-terminal", lambda p: [p, "--execute", *argv]),
        ("foot", lambda p: [p, *argv]),
        ("x-terminal-emulator", lambda p: [p, "-e", *argv]),
    ):
        path = shutil.which(name)
        if path:
            return build(path)
    return None


def terminal_command(argv: list[str]) -> list[str] | None:
    """A command that opens ``argv`` in a new pane or window, or None if unsure."""
    if IS_WINDOWS:
        wt = _wt_argv()
        if wt is not None:
            return [*wt, *argv]
        # The blank first argument is the window title that ``start`` expects.
        return ["cmd", "/c", "start", "", *argv]
    if IS_MACOS:
        script = 'tell application "Terminal" to do script "%s"' % shlex.join(argv).replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", script]
    return _posix_terminal(argv)


def launch(game: str, host: str | None = None) -> tuple[list[str] | None, list[str]]:
    """Open ``game`` next to the caller. Returns (spawned command, fallback argv)."""
    argv = console_argv(game, host)
    command = terminal_command(argv)
    if command is None:
        return None, argv
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    if host:
        # The game reads this to show only the host's own models.
        kwargs["env"] = {**os.environ, "CONSOLE_HOST": host}
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **kwargs,
        )
    except OSError:
        return None, argv
    return command, argv


def open_message(game: str, host: str | None = None) -> str:
    command, argv = launch(game, host)
    if command is None:
        return f"Could not find a terminal to split. Run this in another terminal:\n  {quote_command(argv)}"
    return f"Opened {game} in a new pane."


# -- the terminal hotkey -----------------------------------------------------------------------


def _wt_settings_path() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    for candidate in (
        Path(local) / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json",
        Path(local) / "Microsoft" / "Windows Terminal" / "settings.json",
        Path(local) / "Packages" / "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe" / "LocalState" / "settings.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def wt_action(game: str = "chess", keys: str = "ctrl+o") -> dict:
    """Split, then zoom the game pane, so it fills the window over the running agent."""
    return {
        "keys": keys,
        "command": {
            "action": "multipleActions",
            "actions": [
                {"action": "splitPane", "split": "right", "size": 0.5, "commandline": f"console {game}"},
                {"action": "togglePaneZoom"},
            ],
        },
    }


def apply_wt_keybinding(
    settings: Path | None = None, game: str = "chess", keys: str = "ctrl+o"
) -> tuple[list[str], list[str]]:
    """Add the key to Windows Terminal's actions, keeping a backup. Settings must be plain JSON."""
    path = Path(settings) if settings is not None else _wt_settings_path()
    if path is None or not path.is_file():
        return [], ["Windows Terminal settings.json was not found. Run `console install terminal` and paste the snippet."]
    if not IS_WINDOWS:
        return [], ["This only patches Windows Terminal. Use the tmux line from `console install terminal`."]
    try:
        original = path.read_text(encoding="utf-8")
        data = json.loads(original)
    except (OSError, ValueError) as exc:
        return [], [f"Could not read {path} as plain JSON ({exc}). Paste the snippet from `console install terminal` by hand."]
    if not isinstance(data, dict):
        return [], [f"{path} is not a settings object."]
    actions = data.get("actions")
    if not isinstance(actions, list):
        actions = []
        data["actions"] = actions
    legacy = data.get("keybindings")
    bound = [a for a in [*actions, *(legacy if isinstance(legacy, list) else [])] if isinstance(a, dict) and a.get("keys") == keys]
    if bound:
        return [f"{keys} is already bound in {path}. Nothing to do."], []
    actions.append(wt_action(game, keys))
    backup = path.with_name(path.name + ".console.bak")
    try:
        backup.write_text(original, encoding="utf-8")
        path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    except OSError as exc:
        return [], [f"Could not write {path}: {exc}"]
    return [f"Bound {keys} to Console {game} in {path}", f"Backup: {backup}"], []


# -- installing the slash commands --------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    path: Path
    text: str


def _home(home: Path | None) -> Path:
    return Path(home) if home is not None else Path.home()


def _claude_targets(home: Path, project: bool) -> list[Target]:
    root = Path.cwd() / ".claude" if project else Path(os.environ.get("CLAUDE_CONFIG_DIR") or _home(home) / ".claude")
    return [
        Target(root / "commands" / "console.md", _claude_command("chess", "Play chess against Claude Code, choosing which Claude model", "claude")),
        Target(root / "commands" / "console-dino.md", _claude_command("dino", "Open Chrome's dino in Console, in a new terminal pane", "claude")),
    ]


def _codex_targets(home: Path, _project: bool) -> list[Target]:
    root = Path(os.environ.get("CODEX_HOME") or _home(home) / ".codex")
    return [
        Target(root / "prompts" / "console.md", _codex_command("chess", "codex")),
        Target(root / "prompts" / "console-dino.md", _codex_command("dino", "codex")),
    ]


def _opencode_targets(home: Path, project: bool) -> list[Target]:
    if project:
        root = Path.cwd() / ".opencode"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        root = (Path(xdg) if xdg else _home(home) / ".config") / "opencode"
    return [
        Target(root / "commands" / "console.md", _opencode_command("chess", "opencode")),
        Target(root / "commands" / "console-dino.md", _opencode_command("dino", "opencode")),
    ]


def _claude_command(game: str, description: str, host: str) -> str:
    return f"""---
description: {description}
allowed-tools: Bash(console:*)
---
{MARKER}
!`console open {game} --host {host}`

If no pane opened, report the command and the error to the user; they may need to run `console open {game}` in their own terminal. Do not wait for the game or try to play it through the shell.
"""


def _opencode_command(game: str, host: str) -> str:
    title = "Console chess" if game == "chess" else "Console's dino game"
    return f"""---
description: Open {title} in a new terminal pane
---
{MARKER}
!`console open {game} --host {host}`

If no pane opened, report the command and the error to the user; they may need to run `console open {game}` in their own terminal. Do not wait for the game or try to play it through the shell.
"""


def _codex_command(game: str, host: str) -> str:
    title = "Console chess" if game == "chess" else "Console's dino game"
    return f"""---
description: Open {title} in a new terminal pane
argument-hint: ""
---
{MARKER}
Run this one shell command now, then reply with one short sentence:

    console open {game} --host {host}

It only opens a terminal pane, so it needs no approval and changes no files. Do not plan, do not ask what to do next, do not wait for the game, and do not try to play it through the shell.
"""


def targets(host: str, home: Path | None = None, project: bool = False) -> list[Target]:
    home = _home(home)
    if host == "claude":
        return _claude_targets(home, project)
    if host == "codex":
        return _codex_targets(home, project)
    if host == "opencode":
        return _opencode_targets(home, project)
    return []


def install(
    host: str,
    *,
    home: Path | None = None,
    project: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    """Write the host's command files. Returns (done, errors); never raises for a file conflict."""
    if host == "hermes":
        return (
            [
                "Inside Hermes, install the plugin instead of a command file:",
                "  hermes plugins install Takumixbt/Console --enable",
                "Then /console (chess) or Ctrl+O opens it in place, even while the agent works.",
            ],
            [],
        )
    if host == "terminal":
        return (
            [
                "Windows Terminal: run `console install terminal --apply` (writes a backup), or paste this into the \"actions\" list in settings.json:",
                WT_SNIPPET,
                "",
                "tmux:",
                TMUX_SNIPPET,
            ],
            [],
        )
    if host == "all":
        done: list[str] = []
        errors: list[str] = []
        for one in ("claude", "codex", "opencode"):
            d, e = install(one, home=home, project=project, force=force, dry_run=dry_run)
            done += d
            errors += e
        return done, errors
    if host not in HOSTS:
        return [], [f"Unknown host {host!r}. Choose from: {', '.join(HOSTS)}, all."]

    done: list[str] = []
    errors: list[str] = []
    for target in targets(host, home=home, project=project):
        try:
            existing = target.path.read_text(encoding="utf-8") if target.path.exists() else None
        except OSError as exc:
            errors.append(f"{target.path}: {exc}")
            continue
        if existing == target.text:
            done.append(f"Already up to date: {target.path}")
            continue
        if existing is not None and MARKER not in existing and not force:
            errors.append(f"{target.path} exists and was not written by Console. Re-run with --force to replace it.")
            continue
        if dry_run:
            done.append(f"Would write: {target.path}")
            continue
        try:
            target.path.parent.mkdir(parents=True, exist_ok=True)
            target.path.write_text(target.text, encoding="utf-8")
        except OSError as exc:
            errors.append(f"{target.path}: {exc}")
            continue
        done.append(f"Wrote: {target.path}")
    return done, errors


def describe(host: str, home: Path | None = None, project: bool = False) -> str:
    if host == "hermes":
        return "Hermes plugin: hermes plugins install Takumixbt/Console --enable"
    if host == "terminal":
        return "a Windows Terminal or tmux keybinding (printed, no files)"
    if host == "all":
        host = "claude, codex, opencode"
    return ", ".join(str(t.path) for t in targets(host, home=home, project=project)) or host


# -- diagnosing the hosts ----------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    """One host, checked. ``state`` is 'ok', 'warn' or 'missing'."""

    host: str
    state: str
    version: str
    checks: tuple[tuple[str, str], ...]

    def get(self, label: str) -> str:
        for name, value in self.checks:
            if name == label:
                return value
        return ""


def _probe(binary: str, args: list[str], timeout: float = 25.0) -> str:
    """Run a CLI and return stdout+stderr, or "" when it is absent or unhappy."""
    path = shutil.which(binary)
    if path is None:
        return ""
    argv = [path, *args]
    if IS_WINDOWS and path.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c", *argv]
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


def _one_line(text: str, limit: int = 70) -> str:
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line[:limit]
    return ""


def _auth(host: str) -> str:
    if host == "claude":
        try:
            data = json.loads(_probe("claude", ["auth", "status"]))
        except ValueError:
            return "unknown"
        if isinstance(data, dict) and data.get("loggedIn"):
            return f"logged in ({data.get('authMethod') or 'oauth'})"
        return "NOT logged in - run `claude login`"
    if host == "codex":
        raw = _probe("codex", ["login", "status"]).lower()
        if "logged in" in raw:
            return "logged in"
        return "NOT logged in - run `codex login`" if raw else "unknown"
    if host == "opencode":
        raw = _probe("opencode", ["auth", "list"])
        rows = [line for line in raw.splitlines() if line.strip() and "default" not in line.lower()]
        return f"{len(rows)} credential(s) stored" if rows else "no credentials found - run `opencode auth login`"
    if host == "hermes":
        raw = _probe("hermes", ["status", "--all"], timeout=45)
        return _hermes_status(raw)[0]
    return "unknown"


def _hermes_status(raw: str) -> tuple[str, str]:
    """Summarize Hermes' redacted status without returning its table or secrets."""
    values: dict[str, str] = {}
    for line in (raw or "").splitlines():
        match = re.match(r"^\s*(Model|Provider):\s*(.*?)\s*$", line, re.I)
        if match:
            values[match.group(1).lower()] = match.group(2)

    provider = values.get("provider", "")
    model = values.get("model", "")
    aliases = {
        "xai": ("xAI OAuth", "Grok"),
        "openai codex": ("OpenAI Codex",),
        "nous": ("Nous Portal",),
        "anthropic": ("Anthropic",),
        "qwen": ("Qwen OAuth",),
        "minimax": ("MiniMax OAuth", "MiniMax"),
    }
    section = raw.split("Auth Providers", 1)[-1].split("API-Key Providers", 1)[0]
    candidates = next(
        (names for prefix, names in aliases.items() if prefix in provider.lower()),
        (),
    )
    auth = "unknown"
    for line in section.splitlines():
        if any(name.lower() in line.lower() for name in candidates):
            lower = line.lower()
            if "not logged in" in lower or "invalid refresh token" in lower or "invalid_grant" in lower:
                auth = "not logged in (provider credentials need refreshing)"
            elif "logged in" in lower:
                auth = "logged in"
            break
    if auth == "unknown" and provider:
        auth = "managed by Hermes; run `hermes status` for auth details"

    current = f"{model} via {provider}" if model and provider else model or provider
    model_check = f"{current}; available models load from the active Hermes plugin" if current else (
        "available models load from the active Hermes plugin"
    )
    return auth, model_check


def _plugin_state() -> tuple[str, str]:
    raw = _probe("hermes", ["plugins", "list"], timeout=40)
    for line in raw.splitlines():
        low = line.lower()
        if "console" in low:
            if "not enabled" in low or "disabled" in low:
                return "warn", "plugin installed but not enabled - run `hermes plugins enable console`"
            if "enabled" in low:
                return "ok", "plugin enabled"
            return "warn", _one_line(line)
    return "warn", "plugin not installed - run `hermes plugins install Takumixbt/Console --enable`"


def _command_status(host: str, home: Path | None = None) -> tuple[str, str]:
    """Check that every installed command file matches this version's host-scoped template."""
    files = targets(host, home=home)
    present = 0
    current = 0
    first = ""
    for target in files:
        if not first:
            first = str(target.path)
        try:
            text = target.path.read_text(encoding="utf-8")
        except OSError:
            continue
        present += 1
        if text == target.text:
            current += 1
    if files and current == len(files):
        return "ok", f"installed and current ({first})"
    if not present:
        return "missing", f"not installed - run `console install {host}`"
    if present < len(files):
        return "warn", f"partly installed ({present}/{len(files)}) - run `console install {host}`"
    return "warn", f"out of date ({current}/{len(files)} current) - run `console install {host}`"


def doctor(refresh: bool = False) -> list[Report]:
    """Check each host: is the CLI there, is it signed in, what models can it play, are we installed."""
    from .games.chess import models as catalogs

    reports: list[Report] = []
    for host in ("claude", "codex", "opencode", "hermes"):
        checks: list[tuple[str, str]] = []
        path = shutil.which(host)
        version = _one_line(_probe(host, ["--version"])) if path else ""
        checks.append(("binary", path or "NOT FOUND on PATH"))
        if version:
            checks.append(("version", version))
        if not path:
            reports.append(Report(host, "missing", "", tuple(checks)))
            continue
        hermes_model_check = ""
        if host == "hermes":
            auth, hermes_model_check = _hermes_status(_probe("hermes", ["status", "--all"], timeout=45))
        else:
            auth = _auth(host)
        checks.append(("auth", auth))
        if host == "hermes":
            checks.append(("models", hermes_model_check))
        else:
            groups = catalogs.for_opponent(host, refresh=refresh)
            total = sum(len(group.models) for group in groups)
            sample = [model for group in groups for model in group.models][:3]
            checks.append(
                (
                    "models",
                    f"{total}" + (f" ({', '.join(sample)}{', ...' if total > len(sample) else ''})" if sample else ""),
                )
            )
        if host == "hermes":
            state, text = _plugin_state()
            checks.append(("plugin", text))
        else:
            command_state, command_text = _command_status(host)
            if command_state == "ok":
                checks.append(("command", command_text))
                state = "ok"
            elif command_state == "warn":
                state = "warn"
                checks.append(("command", command_text))
            else:
                state = "warn"
                checks.append(("command", command_text))
        if auth.lower().startswith("not logged in") or (host != "hermes" and total == 0):
            state = "warn"
        reports.append(Report(host, state, version, tuple(checks)))

    settings = _wt_settings_path()
    key_checks: list[tuple[str, str]] = []
    if settings is None:
        key_checks.append(("Windows Terminal", "settings.json not found (use the tmux snippet)"))
    else:
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        bound = any(
            isinstance(entry, dict) and entry.get("keys") == "ctrl+o"
            for entry in [*(data.get("actions") or []), *(data.get("keybindings") or [])]
        )
        key_checks.append(("settings", str(settings)))
        key_checks.append(("ctrl+o", "bound to Console chess" if bound else "not bound - run `console install terminal --apply`"))
    reports.append(Report("terminal", "ok" if key_checks[-1][1].startswith("bound") else "warn", "", tuple(key_checks)))
    return reports


__all__ = [
    "HOSTS",
    "MARKER",
    "TMUX_SNIPPET",
    "WT_SNIPPET",
    "Report",
    "apply_wt_keybinding",
    "console_argv",
    "describe",
    "doctor",
    "install",
    "launch",
    "open_message",
    "quote_command",
    "targets",
    "terminal_command",
]

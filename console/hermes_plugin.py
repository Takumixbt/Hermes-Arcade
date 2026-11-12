"""Hermes Agent plugin: ``/console`` and ``/race``.

``/console [game]`` opens chess, the kart racer or the dino inside the terminal
Hermes is running in. Hermes is the one host that can hand the terminal over in
place: Hermes runs plugin slash commands on a worker thread while prompt_toolkit
owns the screen and the keyboard on the main thread, so the game is started
through prompt_toolkit's own ``run_in_terminal``: it pauses Hermes' UI, hands over
the terminal, and repaints Hermes afterwards.

When Hermes has no prompt_toolkit terminal to draw on (the TUI, the gateway), the
game opens in its own window instead, as a child process with a minimal
environment. While you play, agent output is held back and shown when you leave.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading

from .catalog import DEFAULT_GAME
from .procenv import child_env
from .state import ACTIVITY, home_dir, set_home

CLI_ONLY = (
    "Console draws in the terminal, so it only works in Hermes' command line. "
    "Open Hermes in Windows Terminal (or another sixel terminal) and type /console."
)
HELP = (
    "Console games: {games}. Usage: /console [game|probe]  (default: {default}). "
    "Esc returns to Hermes."
)

_playing = threading.Lock()


def _prompt_toolkit_app():
    try:
        from prompt_toolkit.application import get_app_or_none

        app = get_app_or_none()
    except Exception:
        return None
    if app is not None and getattr(app, "_is_running", False):
        return app
    return None


def _focus_window(game: str) -> bool:
    """Bring an already-open game window to the front instead of refusing."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []
    prefix = f"Console - {game}".lower()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.strip().lower().startswith(prefix):
            found.append(hwnd)
            return False
        return True

    try:
        user32.EnumWindows(_visit, 0)
        if not found:
            return False
        user32.ShowWindow(found[0], 9)  # SW_RESTORE
        user32.SetForegroundWindow(found[0])
        return True
    except Exception:
        return False


def _play_game(name: str) -> str:
    """Run a game to completion on a real terminal. Never raises into Hermes."""
    from .registry import get_game
    from .runner import play

    if not _playing.acquire(blocking=False):
        if _focus_window(name):
            return f"Console is already open - brought {name} to the front."
        # A stuck first attempt must not lock the player out: open a fresh window.
        return _spawn_window(name)
    try:
        return play(get_game(name))
    except KeyError as exc:
        return str(exc.args[0])
    except Exception as exc:  # a game bug must not take Hermes down
        return f"Console hit an error ({type(exc).__name__}): {exc}"
    finally:
        _playing.release()


def _run_with_terminal_handoff(func):
    """Call ``func`` with prompt_toolkit's UI suspended. Returns its result."""
    app = _prompt_toolkit_app()
    if app is None:
        return func()
    from prompt_toolkit.application import run_in_terminal

    async def _go():
        return await run_in_terminal(func, in_executor=True)

    loop = app.loop
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        # Already on the UI thread: schedule it and return at once.
        asyncio.ensure_future(_go())
        return None
    return asyncio.run_coroutine_threadsafe(_go(), loop).result()


def parse_args(raw_args) -> str:
    text = raw_args if isinstance(raw_args, str) else ""
    first = text.strip().split()[0].lower().lstrip("/") if text.strip() else ""
    return first or DEFAULT_GAME


def _probe_report() -> str:
    """What the plugin thinks the terminal can do, and the evidence for it."""
    from .terminal import Terminal

    try:
        with Terminal() as t:
            caps = t.caps
            cols, rows = t.size()
    except Exception as exc:  # a probe must never take the chat down
        return f"Console probe failed: {type(exc).__name__}: {exc}"
    env = os.environ
    raw = caps.raw.encode("latin-1", "ignore").hex() or "(none)"
    import ctypes
    hwnd = ctypes.windll.kernel32.GetConsoleWindow() if os.name == "nt" else 0
    cls = "(none)"
    if hwnd:
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
    lines = [
        f"process: pid {os.getpid()}  console-class {cls}",
        f"sixel: {'YES' if caps.sixel else 'no'}   (decided by: {caps.source})",
        f"terminal: {cols}x{rows} chars, cell {caps.cell_w}x{caps.cell_h} px",
        f"TERM={env.get('TERM', '')}  TERM_PROGRAM={env.get('TERM_PROGRAM', '')}",
        f"WT_SESSION={env.get('WT_SESSION', '(unset)')[:12]}  "
        f"CONSOLE_SIXEL={env.get('CONSOLE_SIXEL', '')}",
        f"DA1 reply: {raw}",
        "force sixel: CONSOLE_SIXEL=1   force text: CONSOLE_SIXEL=0",
    ]
    return "\n".join(lines)


def _spawn_window(game: str) -> str:
    """Run the game in its own terminal, because the agent host has no screen.

    In Hermes 0.23 a plugin command executes in the agent host, which owns no
    prompt_toolkit app and often no console at all. Spawning a dedicated window is
    the only way the player sees pixels, and it needs no setup from them.
    """
    import shutil
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, "-m", "console", game]
    # A window opened from Hermes stays a Hermes window: no agent-CLI opponents
    # and no raw API opponent. It gets the OS, Python and the terminal, never the
    # agent's provider keys. CONSOLE_HOME keeps it reading the same score file.
    env = child_env(
        PYTHONPATH=str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        CONSOLE_HOST="hermes",
        CONSOLE_HOME=str(home_dir()),
    )
    wt = shutil.which("wt") or shutil.which("wt.exe")
    if not wt:
        candidate = os.path.expanduser(r"~\AppData\Local\Microsoft\WindowsApps\wt.exe")
        wt = candidate if os.path.exists(candidate) else None
    try:
        if wt:
            subprocess.Popen(
                [wt, "-w", "0", "nt", "--title", f"Console - {game}", "--"] + cmd,
                cwd=str(root), env=env, close_fds=True,
            )
            return f"Console {game} is opening in a new Windows Terminal tab."
        subprocess.Popen(cmd, cwd=str(root), env=env, close_fds=True)
        return f"Console {game} is opening in a new window."
    except Exception as exc:  # never take the chat down over a failed spawn
        return f"Could not open Console {game}: {type(exc).__name__}: {exc}"


def handle_console(raw_args: str = "", *_args, **_kwargs) -> str:
    """Slash-command handler for ``/console``."""
    from .catalog import GAMES
    from . import registry  # noqa: F401  (registers the built-in games)

    name = parse_args(raw_args)
    if name in {"help", "-h", "--help", "?"}:
        return HELP.format(games=", ".join(sorted(GAMES)), default=DEFAULT_GAME)
    if name in {"probe", "caps"}:
        if os.environ.get("HERMES_GATEWAY"):
            return CLI_ONLY
        if _prompt_toolkit_app() is None and not (sys.stdin and sys.stdin.isatty()):
            return CLI_ONLY
        return _run_with_terminal_handoff(_probe_report) or "probe finished."
    if name not in GAMES:
        return f"Unknown game {name!r}. Available: {', '.join(sorted(GAMES))}."
    if os.environ.get("HERMES_GATEWAY") or _prompt_toolkit_app() is None:
        # The TUI and the gateway host have no prompt_toolkit terminal to draw on,
        # even when stdin looks like a tty. Spawning a window is the only path that
        # puts pixels in front of the player, and it never blocks this process.
        return _spawn_window(name)
    return _run_with_terminal_handoff(lambda: _play_game(name)) or "Left Console."


def handle_race(raw_args: str = "", *_args, **_kwargs) -> str:
    """Slash-command handler for ``/race``: a model writes the controller, then races."""
    from .games.kart import agent

    llm = agent.get_llm()
    if llm is None:
        return "Racing a model needs Hermes's model handle (open Arcade from Hermes)."
    target = (raw_args or "").strip()
    provider = model = ""
    if target:
        if "/" in target:
            provider, model = target.split("/", 1)
        else:
            model = target
    result = agent.ask_model(llm, provider=provider, model=model)
    return agent.report(result)


# -- Hermes activity hooks ------------------------------------------------------


def _turn_started(**_kwargs) -> None:
    ACTIVITY.started()


def _turn_finished(**_kwargs) -> None:
    ACTIVITY.ended(completed=True)


def _session_over(**_kwargs) -> None:
    ACTIVITY.ended(completed=False)


def register(ctx) -> None:
    """Hermes ``register(ctx)`` entry point."""
    if not hasattr(ctx, "register_command"):
        raise RuntimeError("Console needs a Hermes build with plugin slash commands (ctx.register_command).")
    try:
        from .games.chess import opponents
        from .games.kart import agent as kart_agent

        opponents.set_llm(ctx.llm)  # lets chess ask the model Hermes is running
        opponents.set_host("hermes")  # and lists only Hermes' own providers
        kart_agent.set_llm(ctx.llm)  # and lets /race ask a model for a controller
    except Exception:
        pass  # an older Hermes without ctx.llm: chess still works against the engine
    try:
        set_home(ctx.state.data_dir)  # scores and the model cache live with the plugin
    except Exception:
        pass  # no ctx.state (tests, older Hermes): keep the standalone location
    ACTIVITY.host = "Hermes"
    description = "Hermes Arcade: chess, kart and the dino in this terminal. Esc returns."
    try:
        ctx.register_command("console", handle_console, description=description, args_hint="[chess|kart|dino|probe]")
    except TypeError:
        ctx.register_command("console", handle_console, description=description)
    try:
        ctx.register_command("race", handle_race,
                             description="Ask a model to write a kart controller, then race it",
                             args_hint="[provider/model]")
    except Exception:
        pass  # an older Hermes without a second command slot still has /console
    for hook, fn in (
        ("pre_llm_call", _turn_started),
        ("pre_tool_call", _turn_started),
        ("subagent_start", _turn_started),
        ("post_llm_call", _turn_finished),
        ("on_session_end", _session_over),
        ("on_session_reset", _session_over),
    ):
        try:
            ctx.register_hook(hook, fn)
        except Exception:
            pass  # an older Hermes without this hook only loses the status line

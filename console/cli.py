"""Standalone Console CLI: ``console`` / ``python -m console``."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .catalog import DEFAULT_GAME


def _open(argv: list[str], prog: str) -> int:
    from .hosts import HOSTS, console_argv, open_message, quote_command

    parser = argparse.ArgumentParser(prog=f"{prog} open", description="Open a game in a new terminal pane.")
    parser.add_argument("game", nargs="?", default=DEFAULT_GAME, help=f"which game (default: {DEFAULT_GAME})")
    parser.add_argument("--host", choices=HOSTS[:4], help="which agent you are opening it from (limits the opponent list)")
    parser.add_argument("--print", dest="print_only", action="store_true", help="print the command instead of opening a pane")
    args = parser.parse_args(argv)
    from .registry import GAMES

    if args.game not in GAMES:
        print(f"Unknown game {args.game!r}. Available: {', '.join(sorted(GAMES))}.", file=sys.stderr)
        return 2
    if args.print_only:
        print(quote_command(console_argv(args.game, args.host)))
        return 0
    print(open_message(args.game, args.host))
    return 0


def _doctor(argv: list[str], prog: str) -> int:
    import json as _json

    from .hosts import doctor

    parser = argparse.ArgumentParser(prog=f"{prog} doctor", description="Check every agent host Console supports.")
    parser.add_argument("--refresh", action="store_true", help="ignore the cached model lists")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    reports = doctor(refresh=args.refresh)
    if args.json:
        print(
            _json.dumps(
                [
                    {"host": r.host, "state": r.state, "version": r.version, "checks": dict(r.checks)}
                    for r in reports
                ],
                indent=2,
            )
        )
        return 0 if all(r.state != "missing" for r in reports) else 1
    for report in reports:
        print(f"{report.host:9} {report.state:8} {report.version}")
        for label, value in report.checks:
            print(f"  {label:9} {value}")
    return 0 if all(r.state != "missing" for r in reports) else 1


def _install(argv: list[str], prog: str) -> int:
    from .hosts import HOSTS, describe, install

    parser = argparse.ArgumentParser(
        prog=f"{prog} install",
        description="Install the /console slash command for an agent CLI (or print a terminal keybinding).",
    )
    parser.add_argument("host", nargs="?", help=f"one of: {', '.join(HOSTS)}, all")
    parser.add_argument("--list", action="store_true", help="show the hosts and the files they use")
    parser.add_argument("--project", action="store_true", help="write into this repository instead of your user config")
    parser.add_argument("--apply", action="store_true", help="with 'terminal': add the Ctrl+O key to Windows Terminal now")
    parser.add_argument("--force", action="store_true", help="replace a file Console did not write")
    parser.add_argument("--dry-run", action="store_true", help="show what would be written")
    args = parser.parse_args(argv)

    if args.list or not args.host:
        for host in (*HOSTS, "all"):
            print(f"{host:9}  {describe(host, project=args.project)}")
        return 0 if args.list else 2

    done, errors = install(args.host, project=args.project, force=args.force, dry_run=args.dry_run)
    if args.host == "terminal" and args.apply and not args.dry_run:
        from .hosts import apply_wt_keybinding

        done, errors = apply_wt_keybinding()
    for line in done:
        print(line)
    for line in errors:
        print(line, file=sys.stderr)
    return 1 if errors else 0


def main(argv: list[str] | None = None, prog: str = "console") -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["open"]:
        return _open(argv[1:], prog)
    if argv[:1] == ["install"]:
        return _install(argv[1:], prog)
    if argv[:1] == ["doctor"]:
        return _doctor(argv[1:], prog)

    from .registry import GAMES, get_game  # imports the built-in games

    parser = argparse.ArgumentParser(
        prog=prog,
        description="Play chess (or Chrome's dino) in your terminal, against an agent, Stockfish or the built-in engine.",
    )
    parser.add_argument("game", nargs="?", default=DEFAULT_GAME, help=f"which game (default: {DEFAULT_GAME})")
    parser.add_argument("--host", choices=("claude", "codex", "opencode", "hermes"), help="limit the opponents to this agent's own models")
    parser.add_argument("--list", action="store_true", help="list the available games")
    parser.add_argument("--demo", action="store_true", help="let the dino play itself")
    parser.add_argument("--debug", action="store_true", help="show fps and encode time under the picture")
    parser.add_argument("--version", action="version", version=f"console {__version__}")
    args = parser.parse_args(argv)

    if args.host:
        import os

        from .games.chess import opponents

        os.environ["CONSOLE_HOST"] = args.host
        opponents.set_host(args.host)

    if args.list:
        for name in sorted(GAMES):
            print(name)
        return 0
    if args.debug:
        import os

        os.environ["CONSOLE_DEBUG"] = "1"
    try:
        if args.demo:
            from .games.dino.demo import DemoDino

            game = DemoDino.create()
        else:
            game = get_game(args.game)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2

    from .runner import play

    message = play(game)
    if message and message != "Left Console.":
        print(message)
    return 0

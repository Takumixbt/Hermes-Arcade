"""Who you play against: the built-in engine, Stockfish, a coding agent, or an API model.

An opponent turns a position into a move. ``choose`` blocks (a model call can
take seconds), so the game runs it on a worker thread.

Three kinds of model opponent, all behind the same interface:

* ``ModelOpponent``  - the model Hermes is running, through the plugin's ``ctx.llm``
* ``CliOpponent``    - Claude Code, Codex or OpenCode in non-interactive mode, using
                       the login that is already on this machine (no API key to set up)
* ``ApiOpponent``    - any OpenAI-compatible or Anthropic endpoint, configured by env
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from dataclasses import dataclass

from . import engine
from .rules import Board, uci

# The Hermes plugin hands over ``ctx.llm`` here when it loads. Other hosts have none.
_LLM = None


def set_llm(llm) -> None:
    global _LLM
    _LLM = llm


def llm_available() -> bool:
    return _LLM is not None


# -- which host opened the game ----------------------------------------------------------------

#: ``claude``/``codex``/``opencode``/``hermes``, or None when you ran ``console`` yourself.
#: Set from ``--host`` (the slash commands pass it) or from ``CONSOLE_HOST``.
HOST: str | None = None

_HOST_NAMES = {"claude": "Claude Code", "codex": "Codex", "opencode": "OpenCode", "hermes": "Hermes"}


def set_host(host: str | None) -> None:
    global HOST
    text = (host or "").strip().lower()
    HOST = text if text in _HOST_NAMES else None


def get_host() -> str | None:
    return HOST


def host_name() -> str:
    return _HOST_NAMES.get(HOST or "", "")


def set_host_from_env() -> None:
    """Honour ``CONSOLE_HOST`` for launchers that cannot pass ``--host``."""
    set_host(os.environ.get("CONSOLE_HOST"))


#: The plugin's spawned window pins Hermes this way.
set_host_from_env()


# -- the models Hermes has -------------------------------------------------------------------


class Catalog:
    """Every provider Hermes has credentials for, with its models, plus what Hermes is running now."""

    def __init__(self, providers=None, current=("", "")) -> None:
        self.providers: list[tuple[str, str, list[str]]] = providers or []  # (slug, name, [model ids])
        self.current = current  # (provider slug, model id)


_CATALOG: Catalog | None = None
_CATALOG_LOCK = threading.Lock()


def _models_dev() -> dict:
    """The models.dev snapshot Hermes already caches. A data file, not a core import."""
    home = os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")
    for path in (os.path.join(home, "models_dev_cache.json"),
                 os.path.join(os.path.expanduser("~"), ".hermes", "models_dev_cache.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _candidate_providers(cfg: dict, current_provider: str) -> list[str]:
    """Providers this install is configured for — Hermes-native only.

    Earlier this also scanned models.dev for any provider whose API-key env var
    happened to be set, which filled the chess menu with services Hermes was never
    configured to run. The picker now offers exactly what config.yaml declares.
    """
    slugs: list[str] = []
    if current_provider:
        slugs.append(current_provider)
    providers = cfg.get("providers")
    if isinstance(providers, dict):
        slugs.extend(str(k) for k in providers)
    custom = cfg.get("custom_providers")
    if isinstance(custom, list):
        for entry in custom:
            if isinstance(entry, dict):
                slug = entry.get("name") or entry.get("id") or entry.get("slug")
                if slug:
                    slugs.append(str(slug))
    seen, out = set(), []
    for slug in slugs:
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def load_catalog(refresh: bool = False) -> Catalog:
    """Build the model menu from config plus Hermes's models.dev cache.

    Everything here is data the user already owns: config.yaml and the public
    models.dev snapshot. No Hermes internals are imported, so upgrades cannot
    break the picker.
    """
    global _CATALOG
    with _CATALOG_LOCK:
        if _CATALOG is not None and not refresh:
            return _CATALOG
    providers: list[tuple[str, str, list[str]]] = []
    current = ("", "")
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
        model_cfg = cfg.get("model") or {}
        if isinstance(model_cfg, dict):
            current = (str(model_cfg.get("provider") or ""),
                       str(model_cfg.get("default") or model_cfg.get("model") or ""))
        cache = _models_dev()
        for slug in _candidate_providers(cfg, current[0]):
            meta = cache.get(slug) or {}
            name = str(meta.get("name") or slug)
            models = [mid for mid, m in (meta.get("models") or {}).items()
                      if not isinstance(m, dict) or m.get("tool_call", True)]
            if slug == current[0] and current[1] and current[1] not in models:
                models.insert(0, current[1])
            if models:
                providers.append((slug, name, sorted(models)[:60]))
        if current[1]:
            providers.insert(0, ("hermes", "Hermes (current model)", [current[1]]))
    except Exception:
        pass  # not inside Hermes: the menu still offers the engine and the CLIs
    catalog = Catalog(providers, current)
    with _CATALOG_LOCK:
        _CATALOG = catalog
    return catalog


class Opponent:
    label = "Opponent"

    def choose(self, board: Board, history: list[str]) -> tuple[int, str]:
        """Return ``(move, note)``; ``note`` is shown to the player (may be empty)."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class EngineOpponent(Opponent):
    def __init__(self, level_index: int) -> None:
        self.level = engine.LEVELS[level_index]
        self.label = f"Engine ({self.level.name})"
        self.rng = random.Random()

    def choose(self, board, history):
        return engine.best_move(board, self.level, self.rng), ""


class StockfishOpponent(Opponent):
    # Stockfish "Skill Level" 0..20 and thinking time for each of our four strengths
    SKILL = [(1, 100), (6, 250), (12, 600), (20, 1500)]

    def __init__(self, level_index: int) -> None:
        path = engine.find_stockfish()
        if path is None:
            raise RuntimeError("Stockfish not found")
        self.sf = engine.Stockfish(path)
        self.skill, self.ms = self.SKILL[level_index]
        self.label = f"Stockfish ({engine.LEVELS[level_index].name})"

    def choose(self, board, history):
        m = self.sf.best_move(board, self.skill, self.ms)
        if m is None:
            raise RuntimeError("Stockfish returned no move")
        return m, ""

    def close(self) -> None:
        self.sf.close()


# -- asking a model for a move -----------------------------------------------------------------

SYSTEM = (
    "You are a strong chess player. You will be given the current position and the complete list of legal "
    "moves. Choose the best move for the side to move. Reply with exactly one move taken from the list, in "
    "the same notation, wrapped in <move> and </move> tags, and nothing else."
)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _history_text(history: list[str]) -> str:
    out = []
    for i, san in enumerate(history):
        out.append(f"{i // 2 + 1}. {san}" if i % 2 == 0 else san)
    return " ".join(out) or "(game start)"


def _prompt(board: Board, history: list[str]) -> str:
    legal = board.legal_moves()
    san = [board.san(m, legal) for m in legal]
    side = "White" if board.turn > 0 else "Black"
    return (
        f"Position (FEN): {board.fen()}\n"
        f"You play {side}.\n"
        f"Moves so far: {_history_text(history)}\n"
        f"Legal moves: {', '.join(san)}\n"
        "Your move:"
    )


def _extract(board: Board, reply: str) -> int | None:
    """First legal move in the reply (SAN or UCI); models like to add words."""
    text = _ANSI.sub("", reply or "").strip()
    # A tagged answer is unambiguous even when the CLI echoed the prompt first.
    tagged = re.search(r"<\s*move\s*>(.*?)<\s*/\s*move\s*>", text, re.I | re.S)
    if tagged:
        m = board.find_move(tagged.group(1).strip())
        if m is not None:
            return m
    m = board.find_move(text.splitlines()[0]) if text else None
    if m is not None:
        return m
    for tok in re.findall(r"[A-Za-z0-9=+#\-]+", text):
        m = board.find_move(tok)
        if m is not None:
            return m
    return None


class _RemoteOpponent(Opponent):
    """Shared retry/fallback loop: ask, parse a legal move, try again, then let the engine move."""

    ATTEMPTS = 3
    missing = "Model unavailable"

    def __init__(self, fallback_level: int = 2) -> None:
        self.fallback = EngineOpponent(fallback_level)

    def _ask(self, messages) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def _gave_up(self, exc: Exception) -> str:
        detail = str(exc).strip()
        return f"{self.missing} ({detail or type(exc).__name__}); the engine moved instead."

    def choose(self, board, history):
        legal = board.legal_moves()
        san = [board.san(m, legal) for m in legal]
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(board, history)}]
        for _attempt in range(self.ATTEMPTS):
            try:
                reply = self._ask(messages)
            except Exception as exc:
                move, _ = self.fallback.choose(board, history)
                return move, self._gave_up(exc)
            move = _extract(board, reply)
            if move is not None:
                return move, ""
            messages.append({"role": "assistant", "content": (reply or "")[:200]})
            messages.append(
                {"role": "user", "content": f"That is not a legal move. Legal moves: {', '.join(san)}. Reply with one of them."}
            )
        move, _ = self.fallback.choose(board, history)
        return move, "The model kept answering with illegal moves; the engine moved for it."


class ModelOpponent(_RemoteOpponent):
    """Asks the model Hermes is running, through ``ctx.llm``."""

    def __init__(self, fallback_level: int = 2, provider: str = "", model: str = "") -> None:
        super().__init__(fallback_level)
        if _LLM is None:
            raise RuntimeError("no Hermes model available (open Console from Hermes)")
        self.provider = provider
        self.model = model
        self.label = f"Hermes model ({model})" if model else "Hermes model"

    def _ask(self, messages) -> str:
        kwargs = {"max_tokens": 600, "timeout": 120, "purpose": "console chess move"}
        model = self.model or os.environ.get("CONSOLE_CHESS_MODEL")
        provider = self.provider or os.environ.get("CONSOLE_CHESS_PROVIDER")
        if model:
            kwargs["model"] = model
        if provider:
            kwargs["provider"] = provider
        result = _LLM.complete(messages, **kwargs)
        self.label = f"Hermes model ({result.model})"
        return result.text or ""

    def _gave_up(self, exc: Exception) -> str:
        hint = " (to pick a model, allow it under plugins.entries.hermes-arcade-tui.llm in config.yaml)" if "Trust" in type(exc).__name__ else ""
        return f"Model unavailable ({type(exc).__name__}){hint}; the engine moved instead."


# -- coding agents already installed on this machine --------------------------------------------

@dataclass(frozen=True)
class AgentCli:
    """How to ask one agent CLI for a single, non-interactive answer."""

    key: str
    label: str
    binary: str
    model_env: str


AGENT_CLIS: dict[str, AgentCli] = {
    "claude": AgentCli("claude", "Claude Code", "claude", "CONSOLE_CLAUDE_MODEL"),
    "codex": AgentCli("codex", "Codex", "codex", "CONSOLE_CODEX_MODEL"),
    "opencode": AgentCli("opencode", "OpenCode", "opencode", "CONSOLE_OPENCODE_MODEL"),
}

_CWD: str | None = None
_CWD_LOCK = threading.Lock()


def _neutral_cwd() -> str:
    """An empty folder, so an agent answering a chess move cannot edit the player's project."""
    global _CWD
    with _CWD_LOCK:
        if _CWD is None or not os.path.isdir(_CWD):
            _CWD = tempfile.mkdtemp(prefix="console-chess-")
        return _CWD


def _subprocess_argv(argv: list[str]) -> list[str]:
    # Windows ships these tools as .cmd shims, which need a shell.
    if os.name == "nt" and argv and argv[0].lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *argv]
    return argv


class CliOpponent(_RemoteOpponent):
    """Your opponent is the coding agent you already use, called in print mode."""

    TIMEOUT = 240

    def __init__(self, key: str, fallback_level: int = 2, model: str = "") -> None:
        super().__init__(fallback_level)
        cli = AGENT_CLIS.get(key)
        if cli is None:
            raise RuntimeError(f"unknown agent {key!r}")
        self.cli = cli
        path = shutil.which(cli.binary)
        if path is None:
            raise RuntimeError(f"{cli.label} is not on PATH")
        self.path = path
        self.model = (model or os.environ.get(cli.model_env) or "").strip()
        self.label = f"{cli.label} ({self.model})" if self.model else cli.label
        self.missing = f"{cli.label} unavailable"

    def argv(self, prompt: str, last_message: str | None = None) -> list[str]:
        """The command line for one question. Kept separate so it can be tested without running it."""
        if self.cli.key == "claude":
            argv = [self.path, "-p", prompt, "--output-format", "json"]
            if self.model:
                argv += ["--model", self.model]
        elif self.cli.key == "codex":
            argv = [
                self.path, "exec",
                "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                "-C", _neutral_cwd(),
            ]
            if self.model:
                argv += ["-m", self.model]
            if last_message:
                argv += ["-o", last_message]  # only the final answer, not the echoed transcript
            argv.append(prompt)
        else:  # opencode
            argv = [self.path, "run", "--format", "default"]
            if self.model:
                argv += ["--model", self.model]
            argv.append(prompt)
        return _subprocess_argv(argv)

    def _ask(self, messages) -> str:
        # One line, because Windows ships these tools as .cmd shims and cmd.exe
        # splits an argument at its newlines. The transcript keeps retries honest.
        prompt = " ".join(
            [SYSTEM, *(m["content"] for m in messages[1:])]
        )
        prompt = re.sub(r"\s+", " ", prompt).strip()
        last_message = None
        if self.cli.key == "codex":
            fd, last_message = tempfile.mkstemp(prefix="console-codex-", suffix=".txt")
            os.close(fd)
        try:
            proc = subprocess.run(
                self.argv(prompt, last_message),
                cwd=_neutral_cwd(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.TIMEOUT,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                raise RuntimeError(f"{self.cli.label} exited {proc.returncode}" + (f": {detail[-1][:120]}" if detail else ""))
            if last_message:
                try:
                    with open(last_message, encoding="utf-8", errors="replace") as fh:
                        answer = fh.read().strip()
                    if answer:
                        return answer
                except OSError:
                    pass
            output = proc.stdout or ""
            if self.cli.key == "claude":
                # Claude family aliases can resolve differently by account,
                # plan, and CLI version. Report the model Claude actually ran.
                try:
                    result = json.loads(output)
                except ValueError:
                    return output
                if isinstance(result, dict):
                    usage = result.get("modelUsage")
                    if isinstance(usage, dict) and usage:
                        actual = next(iter(usage))
                        details = usage.get(actual)
                        canonical = details.get("canonicalModel") if isinstance(details, dict) else None
                        self.label = f"{self.cli.label} ({canonical or actual})"
                    return str(result.get("result") or output)
            return output
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"{self.cli.label} took longer than {self.TIMEOUT}s") from None
        finally:
            if last_message:
                try:
                    os.unlink(last_message)
                except OSError:
                    pass


# -- any API endpoint --------------------------------------------------------------------------


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _api_model() -> str:
    return _env("CONSOLE_LLM_MODEL") or _env("CONSOLE_MODEL") or _env("ANTHROPIC_MODEL") or _env("OPENAI_MODEL")


def _api_key(protocol: str) -> str:
    key = _env("CONSOLE_LLM_API_KEY")
    if key:
        return key
    return _env("ANTHROPIC_API_KEY") if protocol == "anthropic" else _env("OPENAI_API_KEY")


def _api_base(protocol: str) -> str:
    base = _env("CONSOLE_LLM_BASE_URL")
    if base:
        return base
    return "https://api.anthropic.com/v1" if protocol == "anthropic" else "https://api.openai.com/v1"


def _api_protocol() -> str:
    given = _env("CONSOLE_LLM_PROTOCOL").lower()
    if given in ("anthropic", "openai"):
        return given
    if "anthropic" in _api_base("openai").lower():
        return "anthropic"
    if _env("ANTHROPIC_API_KEY") and not _env("OPENAI_API_KEY") and not _env("CONSOLE_LLM_BASE_URL"):
        return "anthropic"
    return "openai"


def api_configured() -> bool:
    """An API opponent needs a model, and either a key or a custom (local) base URL."""
    if not _api_model():
        return False
    protocol = _api_protocol()
    return bool(_api_key(protocol) or _env("CONSOLE_LLM_BASE_URL"))


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ApiOpponent(_RemoteOpponent):
    """Any OpenAI-compatible endpoint (OpenRouter, Groq, Ollama, LM Studio, vLLM...) or Anthropic."""

    TIMEOUT = 120

    def __init__(self, fallback_level: int = 2, model: str = "") -> None:
        super().__init__(fallback_level)
        self.protocol = _api_protocol()
        self.model = (model or _api_model()).strip()
        self.base = _api_base(self.protocol).rstrip("/")
        self.key = _api_key(self.protocol)
        if not self.model:
            raise RuntimeError("set CONSOLE_LLM_MODEL to use an API model")
        if not self.key and not _env("CONSOLE_LLM_BASE_URL"):
            raise RuntimeError("set CONSOLE_LLM_API_KEY (or CONSOLE_LLM_BASE_URL for a local server)")
        self.label = f"API ({self.model})"
        self.missing = "API model unavailable"

    def _ask(self, messages) -> str:
        if self.protocol == "anthropic":
            body = {
                "model": self.model,
                "max_tokens": 600,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": "\n\n".join(m["content"] for m in messages[1:])}],
            }
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
            data = _post_json(f"{self.base}/messages", body, headers, self.TIMEOUT)
            blocks = data.get("content") or []
            return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        body = {"model": self.model, "max_tokens": 600, "messages": messages}
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        data = _post_json(f"{self.base}/chat/completions", body, headers, self.TIMEOUT)
        choices = data.get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):  # some providers return content blocks
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""


# -- the menu ----------------------------------------------------------------------------------


def available() -> list[tuple[str, str]]:
    """(key, menu text) for each opponent that can be used right now.

    When Console knows which host opened it, only that host's own models are
    offered: OpenCode shows OpenCode's lineup, Hermes shows Hermes', and you are
    never offered an agent you did not open the game from.
    """
    out = [("engine", "Built-in engine")]
    if engine.find_stockfish():
        out.append(("stockfish", "Stockfish"))
    if HOST in AGENT_CLIS:
        known = AGENT_CLIS[HOST]
        suffix = "" if shutil.which(known.binary) else " (not on PATH)"
        out.append((known.key, known.label + suffix))
        return out
    if HOST == "hermes":
        out.append(("model", "Hermes model" if llm_available() else "Hermes model (needs the plugin)"))
        return out
    # No host: you ran `console` yourself, so everything installed is fair game.
    if llm_available():
        out.append(("model", "Hermes model"))
    for known in AGENT_CLIS.values():
        if shutil.which(known.binary):
            out.append((known.key, known.label))
    if api_configured():
        out.append(("api", "API model"))
    return out


def make(kind: str, level_index: int, provider: str = "", model: str = "") -> Opponent:
    if kind == "stockfish":
        return StockfishOpponent(level_index)
    if kind == "model":
        return ModelOpponent(provider=provider, model=model)
    if kind in AGENT_CLIS:
        return CliOpponent(kind, model=model)
    if kind == "api":
        return ApiOpponent(model=model)
    return EngineOpponent(level_index)


__all__ = [
    "AGENT_CLIS",
    "ApiOpponent",
    "Catalog",
    "CliOpponent",
    "EngineOpponent",
    "HOST",
    "ModelOpponent",
    "Opponent",
    "StockfishOpponent",
    "api_configured",
    "available",
    "get_host",
    "host_name",
    "load_catalog",
    "make",
    "set_host",
    "set_host_from_env",
    "set_llm",
    "llm_available",
    "uci",
]

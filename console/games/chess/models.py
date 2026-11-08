"""The models each host can actually play as.

Every agent CLI keeps its own list, in its own place, so this module asks each
one rather than shipping a guess:

* Claude Code has no catalogue command; the ids come from the models this
  machine's own config has used (``~/.claude/settings.json`` and ``.claude.json``)
  plus the aliases its ``--model`` help text advertises.
* Codex renders its catalogue as JSON: ``codex debug models``.
* OpenCode prints ``provider/model`` lines: ``opencode models``.
* Hermes is asked through the plugin's ``ctx.llm`` (see ``opponents.load_catalog``).
* A plain API endpoint is asked for ``GET /models``.

Results are cached in memory and on disk, because two of those calls take
seconds and the menu must stay responsive.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ...state import home_dir

CACHE_TTL = 6 * 3600.0  # seconds; a new model shows up on the next launch after this
CACHE_FILE = "chess-models.json"

_LOCK = threading.Lock()
_MEMORY: dict[str, list["Group"]] = {}


@dataclass(frozen=True)
class Group:
    """One provider's models. ``slug`` is empty for a host with a single flat list."""

    slug: str
    name: str
    models: tuple[str, ...] = ()
    default: bool = False  # "whatever this host is running" rather than a named model


# -- small helpers -----------------------------------------------------------------------------


def _run(binary: str, args: list[str], timeout: float = 30.0) -> str:
    """Run a CLI and return stdout, or "" if it is missing, slow or unhappy."""
    path = shutil.which(binary)
    if path is None:
        return ""
    argv = [path, *args]
    # Windows ships these tools as .cmd shims, which need cmd.exe in between.
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
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
    return proc.stdout or ""


def _version_key(name: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", name))


def _squash(seq) -> tuple[str, ...]:
    """Drop duplicates and empties, keeping the first spelling."""
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        text = (item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out)


def _json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _home(*parts: str) -> Path:
    return Path.home().joinpath(*parts)


# -- Claude Code -------------------------------------------------------------------------------

#: Advertised in ``claude --help``; used when the help text cannot be read.
CLAUDE_ALIASES = ("fable", "opus", "sonnet", "haiku")


def _claude_ids() -> list[str]:
    """Model ids this machine's Claude Code config already names."""
    ids: list[str] = []
    settings = _json_file(_home(".claude", "settings.json"))
    if isinstance(settings, dict):
        for key in ("model",):
            if isinstance(settings.get(key), str):
                ids.append(settings[key])
        for value in (settings.get("modelSettings") or {}):
            ids.append(value)
        env = settings.get("env") or {}
        if isinstance(env, dict):
            ids.extend(value for value in env.values() if isinstance(value, str))
    claude_json = _json_file(_home(".claude.json"))
    if isinstance(claude_json, dict):
        for project in (claude_json.get("projects") or {}).values():
            if isinstance(project, dict):
                ids.extend((project.get("lastModelUsage") or {}).keys())
        features = claude_json.get("cachedGrowthBookFeatures") or {}
        if isinstance(features, dict):
            auto = features.get("tengu_auto_mode_config") or {}
            if isinstance(auto, dict):
                ids.extend((auto.get("modelByMainModel") or {}).keys())
                ids.extend((auto.get("severityByModel") or {}).keys())
    return [text for text in ids if text.startswith("claude-")]


def _claude_aliases() -> list[str]:
    help_text = _run("claude", ["--help"], timeout=20)
    found: list[str] = []
    for match in re.finditer(r"\(e\.g\.([^)]*)\)", help_text, re.I):
        found.extend(re.findall(r"'([a-z]+)'", match.group(1)))
    return found or list(CLAUDE_ALIASES)


def claude_models() -> list[Group]:
    aliases = _claude_aliases()
    ids = sorted(_squash(_claude_ids()), key=_version_key, reverse=True)
    # Keep the CLI's family aliases so users can select the newest version
    # their account allows; also expose exact IDs from this account's history.
    models = _squash([*aliases, *ids])
    return [Group("", "Claude Code", models)] if models else []


# -- Codex -------------------------------------------------------------------------------------


def _codex_config_model() -> str:
    text = ""
    try:
        text = _home(".codex", "config.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else ""


def codex_models() -> list[Group]:
    data = None
    raw = _run("codex", ["debug", "models"], timeout=30)
    if raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
    models: list[str] = []
    if isinstance(data, dict):
        for entry in data.get("models") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("visibility") or "").lower() == "hide":
                continue  # internal routing models the picker never shows
            slug = entry.get("slug")
            if isinstance(slug, str) and slug:
                models.append(slug)
    if not models:
        models = [m for m in (_codex_config_model(),) if m]  # at least what you are running
    return [Group("", "Codex", _squash(models))] if models else []


# -- OpenCode ----------------------------------------------------------------------------------


def opencode_models() -> list[Group]:
    raw = _run("opencode", ["models"], timeout=60)
    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text or "/" not in text or " " in text:
            continue
        provider, _, _model = text.partition("/")
        if not provider:
            continue
        if provider not in buckets:
            buckets[provider] = []
            order.append(provider)
        buckets[provider].append(text)
    return [
        Group(provider, provider[:1].upper() + provider[1:], _squash(buckets[provider]))
        for provider in order
        if buckets[provider]
    ]


# -- a plain API endpoint ----------------------------------------------------------------------


def api_models() -> list[Group]:
    from . import opponents  # local import: opponents imports this module

    if not opponents.api_configured():
        return []
    base = opponents._api_base(opponents._api_protocol()).rstrip("/")
    key = opponents._api_key(opponents._api_protocol())
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"} if "anthropic" in base else (
        {"authorization": f"Bearer {key}"} if key else {}
    )
    try:
        request = urllib.request.Request(f"{base}/models", headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return []
    rows = data.get("data") if isinstance(data, dict) else None
    models = [row.get("id") for row in (rows or []) if isinstance(row, dict)]
    return [Group("", "API", _squash(models))] if models else []


# -- Hermes ------------------------------------------------------------------------------------


def hermes_models() -> list[Group]:
    from . import opponents  # local import: opponents imports this module

    catalog = opponents.load_catalog()
    groups = [Group("", "Hermes default", (), default=True)]
    for slug, name, models in catalog.providers:
        groups.append(Group(slug, name, _squash(models)))
    return groups


# -- the cache ---------------------------------------------------------------------------------


BUILDERS = {
    "claude": claude_models,
    "codex": codex_models,
    "opencode": opencode_models,
    "api": api_models,
    "model": hermes_models,
}

MODEL_KINDS = tuple(BUILDERS)


def _cache_path() -> Path:
    return home_dir() / CACHE_FILE


def _read_cache() -> dict:
    data = _json_file(_cache_path())
    return data if isinstance(data, dict) else {}


def _write_cache(kind: str, groups: list[Group]) -> None:
    data = _read_cache()
    data[kind] = {
        "time": time.time(),
        "groups": [
            {"slug": g.slug, "name": g.name, "models": list(g.models), "default": g.default} for g in groups
        ],
    }
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a missing cache only costs a slow first menu


def _from_cache(kind: str) -> list[Group] | None:
    entry = _read_cache().get(kind)
    if not isinstance(entry, dict):
        return None
    try:
        if time.time() - float(entry.get("time") or 0) > CACHE_TTL:
            return None
        groups = [
            Group(str(g.get("slug") or ""), str(g.get("name") or ""), tuple(g.get("models") or ()), bool(g.get("default")))
            for g in entry.get("groups") or []
            if isinstance(g, dict)
        ]
    except (TypeError, ValueError):
        return None
    return groups or None


def for_opponent(kind: str, refresh: bool = False) -> list[Group]:
    """The model list behind one opponent. Never raises; an empty list means "ask the host"."""
    builder = BUILDERS.get(kind)
    if builder is None:
        return []
    with _LOCK:
        if not refresh and kind in _MEMORY:
            return _MEMORY[kind]
    if not refresh:
        cached = _from_cache(kind)
        if cached is not None:
            with _LOCK:
                _MEMORY[kind] = cached
            return cached
    if kind == "model" and refresh:
        # Hermes keeps its own in-memory view of the account's provider picker.
        # Refresh that too when the caller explicitly asks for a live catalog.
        from . import opponents

        opponents.load_catalog(refresh=True)
    try:
        groups = builder()
    except Exception:
        groups = []
    with _LOCK:
        _MEMORY[kind] = groups
    if groups:
        _write_cache(kind, groups)
    return groups


def clear_cache() -> None:
    with _LOCK:
        _MEMORY.clear()
    try:
        _cache_path().unlink()
    except OSError:
        pass


__all__ = [
    "BUILDERS",
    "CLAUDE_ALIASES",
    "Group",
    "MODEL_KINDS",
    "api_models",
    "claude_models",
    "clear_cache",
    "codex_models",
    "for_opponent",
    "hermes_models",
    "opencode_models",
]

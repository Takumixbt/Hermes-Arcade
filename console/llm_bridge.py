"""A loopback bridge between a spawned game window and the plugin's ``ctx.llm``.

Hermes runs a plugin command with the LLM handle in the plugin host, while
``/console`` draws the game in a separate process. A live client object cannot
cross that process boundary, so the game asks the host over loopback HTTP.

The bridge is deliberately small and locked down:

* it binds to ``127.0.0.1`` only, on a random port;
* every request needs a fresh random bearer token, compared in constant time;
* it exposes one operation, ``POST /complete``, with a bounded JSON body;
* it forwards only messages and the usual completion limits, never files or
  environment variables;
* the child gets the URL and token, never a provider key. ``/race``'s
  model-written controller gets neither: only the game window receives them.

The server lives in the plugin host process and dies with it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger("console.llm_bridge")

#: Passed to the spawned game window, and to nothing else.
URL_ENV = "CONSOLE_LLM_BRIDGE_URL"
TOKEN_ENV = "CONSOLE_LLM_BRIDGE_TOKEN"

_MAX_BODY = 512 * 1024
_MAX_TEXT = 200 * 1024
_ROLES = frozenset({"system", "user", "assistant"})


class _State:
    llm: Any = None
    token: str = ""
    url: str = ""
    server: ThreadingHTTPServer | None = None
    lock = threading.Lock()


_STATE = _State()
_CALL_LOCK = threading.Lock()


def _bridge_env_locked() -> dict[str, str] | None:
    if _STATE.server is None:
        return None
    return {URL_ENV: _STATE.url, TOKEN_ENV: _STATE.token}


def start(llm) -> dict[str, str] | None:
    """Start the bridge for ``llm`` and return the child's environment.

    Safe to call more than once: a running server is reused and its LLM handle
    is refreshed. Returns ``None`` when there is no model or the port cannot be
    opened, in which case the game falls back to its engine.
    """
    if llm is None:
        return None
    with _STATE.lock:
        _STATE.llm = llm
        existing = _bridge_env_locked()
        if existing is not None:
            return existing
        token = secrets.token_urlsafe(32)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(token))
        except OSError as exc:
            logger.warning("Console LLM bridge could not listen on loopback: %s", exc)
            _STATE.llm = None
            return None
        server.daemon_threads = True
        _STATE.server = server
        _STATE.token = token
        _STATE.url = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, name="console-llm-bridge", daemon=True).start()
        return _bridge_env_locked()


def stop() -> None:
    """Stop the bridge. Used by tests and by a host that is tearing down."""
    with _STATE.lock:
        server = _STATE.server
        _STATE.server = None
        _STATE.token = ""
        _STATE.url = ""
        _STATE.llm = None
    if server is not None:
        server.shutdown()
        server.server_close()


def config_from_env(environ=None) -> tuple[str, str] | None:
    """``(url, token)`` from the child's environment, or ``None``.

    Only a loopback HTTP URL is accepted, so a tampered environment cannot make
    the child send its token to another host.
    """
    env = os.environ if environ is None else environ
    url = (env.get(URL_ENV) or "").strip()
    token = (env.get(TOKEN_ENV) or "").strip()
    if not url or not token or len(token) < 16 or any(ch.isspace() for ch in token):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    if not port:
        return None
    return url.rstrip("/"), token


def complete(
    url: str,
    token: str,
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    """Ask the host's model for a completion. Raises ``RuntimeError`` on failure."""
    payload: dict[str, Any] = {"messages": messages}
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)
    if timeout is not None:
        payload["timeout"] = float(timeout)
    if purpose:
        payload["purpose"] = str(purpose)[:200]
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/complete",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    http_timeout = max(30.0, float(timeout or 120.0) + 30.0)
    try:
        with urllib.request.urlopen(request, timeout=http_timeout) as response:
            data = json.loads(response.read(_MAX_TEXT + 8192).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = str(json.loads(exc.read(4096).decode("utf-8")).get("error") or "")
        except Exception:
            pass
        raise RuntimeError(detail or f"Console LLM bridge returned HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(f"Console LLM bridge unavailable ({type(exc).__name__})") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        detail = data.get("error") if isinstance(data, dict) else ""
        raise RuntimeError(str(detail or "Console LLM bridge refused the request"))
    return data


def _handler(expected_token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ConsoleBridge"
        sys_version = ""

        def log_message(self, *_args) -> None:  # no request lines in the host log
            return

        def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
            try:
                self.connection.settimeout(15)
            except OSError:
                pass
            if urllib.parse.urlsplit(self.path).path != "/complete":
                self._reply(404, {"ok": False, "error": "not found"})
                return
            if not self._authorized(expected_token):
                self._reply(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            if length <= 0 or length > _MAX_BODY:
                self._reply(413, {"ok": False, "error": "request body too large"})
                return
            try:
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8"))
                messages = _messages(payload.get("messages"))
                kwargs = _limits(payload)
            except ValueError as exc:
                self._reply(400, {"ok": False, "error": str(exc) or "invalid request"})
                return
            llm = _STATE.llm
            if llm is None:
                self._reply(503, {"ok": False, "error": "no model is attached"})
                return
            try:
                with _CALL_LOCK:
                    result = llm.complete(messages, **kwargs)
                text = getattr(result, "text", None)
                used = getattr(result, "model", None)
                if text is None and isinstance(result, dict):
                    text = result.get("text") or result.get("content") or ""
                    used = result.get("model") or ""
                self._reply(200, {"ok": True, "text": (text or "")[: _MAX_TEXT], "model": used or ""})
            except Exception as exc:  # a provider failure is the game's status line
                logger.warning("Console LLM bridge call failed: %s: %s", type(exc).__name__, exc)
                self._reply(502, {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]})

        def _authorized(self, expected: str) -> bool:
            header = self.headers.get("Authorization") or ""
            prefix = "Bearer "
            if not header.startswith(prefix):
                return False
            return secrets.compare_digest(header[len(prefix) :], expected)

        def _reply(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the game closed its window mid-call

    return Handler


def _messages(value) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValueError("messages must be a non-empty list")
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each message must be an object")
        role = item.get("role")
        content = item.get("content")
        if role not in _ROLES or not isinstance(content, str) or len(content) > _MAX_BODY // 2:
            raise ValueError("each message needs a role and text content")
        out.append({"role": role, "content": content})
    return out


def _limits(payload: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    provider = payload.get("provider")
    model = payload.get("model")
    if provider is not None:
        if not isinstance(provider, str) or len(provider) > 200:
            raise ValueError("invalid provider")
        kwargs["provider"] = provider
    if model is not None:
        if not isinstance(model, str) or len(model) > 200:
            raise ValueError("invalid model")
        kwargs["model"] = model
    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 4096:
            raise ValueError("invalid max_tokens")
        kwargs["max_tokens"] = max_tokens
    timeout = payload.get("timeout")
    if timeout is not None:
        if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
            raise ValueError("invalid timeout")
        kwargs["timeout"] = timeout
    purpose = payload.get("purpose")
    if purpose is not None:
        if not isinstance(purpose, str) or len(purpose) > 200:
            raise ValueError("invalid purpose")
        kwargs["purpose"] = purpose
    return kwargs

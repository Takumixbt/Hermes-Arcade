"""Ask a model for a kart controller, then race it on a fixed seed.

A model turn costs seconds and the race runs at 60 fps, so the model never plays
frame by frame: it writes one ``act(state)`` function, the engine runs it at full
speed, and the score comes back as a number. Same seed, same policy, same result,
so two models can be compared honestly.

The policy runs in a child process with a wall-clock cap, so a broken or hostile
controller cannot take the agent down with it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROMPT = '''You are writing the controller for a kart in a side-view race game.

The engine calls your function 60 times per second. The race is 1500 metres.

Write ONLY Python, no prose, no imports, in exactly this shape:

def act(state):
    return {"lane": 1, "throttle": 1.0}

state is a dict:
  distance  float  metres travelled
  speed     float  pixels per second, max 320 (about 410 with a boost)
  lane      float  your current lane, 0..2 (0 is the top lane)
  rank      int    1..4, your position
  next_pad  tuple or None: (kind, lane, metres_ahead) where kind is "boost" or "oil"
  rivals    list of (metres, lane) for the other three karts
  t         float  seconds since the start

Rules:
- lane is an integer 0, 1 or 2; throttle is -1.0 (brake) to 1.0 (full)
- avoid "oil" pads, aim for "boost" pads, stay in a clear lane
- no imports, no file or network access, no random, no global state
- keep it deterministic: same state must always give the same action

Reply with one fenced python code block and nothing else.
'''

_FENCE = re.compile(r"```(?:python|py)?\s*(.*?)```", re.S)
_DEF = re.compile(r"^def\s+act\s*\(", re.M)

_LLM = None


def set_llm(llm) -> None:
    """Hermes hands the plugin its LLM client once, at register time."""
    global _LLM
    _LLM = llm


def get_llm():
    return _LLM


def extract_policy(text: str) -> str:
    """Pull the policy out of a model reply; accept a bare function too."""
    if not text:
        raise ValueError("empty model reply")
    for block in _FENCE.findall(text):
        if "def act(" in block:
            return _strip(block)
    idx = _DEF.search(text)
    if idx:
        return _strip(text[idx.start():])
    raise ValueError("no 'def act(state)' found in the reply")


def _strip(code: str) -> str:
    lines = code.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("def act(")), None)
    if start is None:
        raise ValueError("no 'def act(state)' found")
    body = lines[start:]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body).rstrip() + "\n"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_policy(source: str, seconds: float = 90.0, seed: int = 7,
               timeout: float = 180.0, repo: Path | None = None) -> dict:
    """Run a policy in a child process and return the race result.

    The child imports this checkout and calls the same ``simulate()`` the
    interactive game uses, so the score is the game's own physics.
    """
    root = Path(repo) if repo else _repo_root()
    runner = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        "from console.games.kart.game import KartGame\n"
        "try:\n"
        "    exec(compile(POLICY, '<policy>', 'exec'), globals())\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'error': f'policy did not compile: {type(exc).__name__}: {exc}'}))\n"
        "    raise SystemExit(0)\n"
        "if 'act' not in globals():\n"
        "    print(json.dumps({'error': 'policy did not define act(state)'}))\n"
        "    raise SystemExit(0)\n"
        f"result = KartGame().simulate(act, seconds={seconds!r}, seed={seed!r})\n"
        "print(json.dumps(result))\n"
    )
    runner = f"POLICY = {source!r}\n" + runner
    with tempfile.TemporaryDirectory(prefix="kart-policy-") as tmp:
        path = Path(tmp) / "run_policy.py"
        path.write_text(runner, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True, text=True, timeout=timeout, env=env, cwd=str(root),
            )
        except subprocess.TimeoutExpired:
            return {"error": f"policy exceeded {timeout:.0f}s"}
    out = (proc.stdout or "").strip().splitlines()
    for line in reversed(out):
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    err = (proc.stderr or "").strip().splitlines()
    return {"error": err[-1] if err else "policy produced no result"}


def ask_model(llm, provider: str = "", model: str = "", seconds: float = 90.0,
              seed: int = 7, timeout: float = 180.0) -> dict:
    """Prompt a model for a policy, run it, and report the race.

    ``llm`` is Hermes's plugin ``ctx.llm``. Failures come back as ``error`` in the
    dict rather than raising, because a slash command must not blow up the chat.
    """
    kwargs = {"max_tokens": 900, "timeout": 150, "purpose": "kart policy"}
    if model:
        kwargs["model"] = model
    if provider:
        kwargs["provider"] = provider
    try:
        reply = llm.complete([{"role": "user", "content": PROMPT}], **kwargs)
    except Exception as exc:
        return {"error": f"model unavailable ({type(exc).__name__}): {exc}"}
    text = getattr(reply, "text", "") or ""
    used = getattr(reply, "model", model or "current model")
    try:
        source = extract_policy(text)
    except ValueError as exc:
        return {"error": f"{exc}", "model": used, "reply": text[:400]}
    result = run_policy(source, seconds=seconds, seed=seed, timeout=timeout)
    result["model"] = used
    result["policy"] = source
    result["seed"] = seed
    return result


def report(result: dict) -> str:
    """One short paragraph a chat can show."""
    if result.get("error"):
        return f"Kart policy failed: {result['error']}"
    place = result.get("place", "?")
    return (
        f"{result.get('model', 'model')} drove {result.get('distance_m', 0):.0f} m "
        f"in {result.get('time_s', 0):.1f}s, finishing {place}, "
        f"average {result.get('speed_avg', 0):.0f} m/s "
        f"(seed {result.get('seed', 7)})."
    )

"""The dino port must reproduce real Chrome 154, frame for frame.

The fixtures were recorded by ``tools/chrome_trace.mjs`` (Chrome headless under
a fake clock and seeded ``Math.random``, driven by a scripted bot). Every canvas
call and a snapshot of the game state are hashed for each frame; the fixtures
keep one hash per 100-frame block. Regenerate with the two tools if Chrome's
game ever changes.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import replay_trace  # noqa: E402

FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("chrome154_*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_matches_chrome(path):
    fx = json.loads(path.read_text())
    replay = replay_trace.Replay(fx)
    init = replay.init_lines()
    assert init == fx["init"]["init"], "boot-time canvas calls differ from Chrome"
    frames, states, _ = replay.run()
    got = replay_trace.block_hashes(frames, states)
    bad = [i * fx["block"] for i, (a, b) in enumerate(zip(got, fx["blockHashes"])) if a != b]
    assert not bad, f"diverged from Chrome in the 100-frame blocks starting at frames {bad[:5]}"
    assert len(got) == len(fx["blockHashes"])


def test_fixtures_exist():
    assert len(FIXTURES) >= 3

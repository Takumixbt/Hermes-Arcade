"""Replay a Chrome trace through the Python port and compare it frame by frame.

    python tools/replay_trace.py trace.json [--first-diff-detail]

The trace comes from ``tools/chrome_trace.mjs``. Each frame's canvas calls and
a snapshot of the game state are hashed in both worlds; the port is a faithful
translation exactly when every hash matches.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from console.gfx.canvas import Canvas  # noqa: E402
from console.games.dino.jsrt import Scheduler  # noqa: E402
from console.games.dino.runner import Runner  # noqa: E402
from console.games.dino.sprites import load_sheet  # noqa: E402


class Mulberry32:
    """Same generator as the harness's seeded Math.random."""

    def __init__(self, seed: int) -> None:
        self.a = seed & 0xFFFFFFFF
        self.count = 0

    def __call__(self) -> float:
        self.count += 1
        self.a = (self.a + 0x6D2B79F5) & 0xFFFFFFFF
        a = self.a
        t = _imul(a ^ (a >> 15), 1 | a)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


def _imul(a: int, b: int) -> int:
    return (a * b) & 0xFFFFFFFF


def fmt(n: float) -> str:
    if isinstance(n, float) and math.isnan(n):
        return "NaN"
    n = float(n)
    if n == 0:
        n = 0.0
    return f"{n:.5f}"


def fnv(text: str, seed: int) -> str:
    h = seed
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


def hash2(text: str) -> str:
    return fnv(text, 2166136261) + fnv(text, 3266489917)


def canon(entry: tuple) -> str:
    kind = entry[0]
    if kind == "draw":
        return "d|" + "|".join(fmt(v) for v in entry[1:10])
    if kind == "clear":
        return "c|" + "|".join(fmt(v) for v in entry[1:5])
    if kind == "fillrect":
        return "f|" + "|".join(fmt(v) for v in entry[1:5])
    raise ValueError(kind)


def snapshot(sched: Scheduler, runner: Runner, rng: Mulberry32) -> str:
    r = runner
    t = r.t_rex
    h = r.horizon
    nm = h.night_mode
    dm = r.distance_meter
    o = h.obstacles
    values = [
        sched.now, r.distance_ran, r.current_speed, r.running_time, r.time, r.invert_timer,
        int(r.playing), int(r.crashed), int(r.paused), int(r.activated), int(r.playing_intro), int(r.inverted),
        int(r.html_inverted),
        t.x_pos, t.y_pos, t.jump_velocity, int(t.status), int(t.jumping), int(t.ducking), int(t.speed_drop), t.blink_count, t.current_frame, t.timer,
        len(o), o[0].x_pos if o else -1, o[-1].x_pos if o else -1, o[0].size if o else 0,
        nm.opacity, nm.current_phase, nm.x_pos, len(h.clouds), h.horizon_lines[0].x_pos[0], h.horizon_lines[0].x_pos[1],
        len(dm.high_score), r.highest_score, rng.count,
    ]
    return "|".join(fmt(v) for v in values)


KEYS = {"keydown": "on_key_down", "keyup": "on_key_up"}


class Replay:
    def __init__(self, trace: dict, keep_detail: bool = False) -> None:
        self.trace = trace
        self.rng = Mulberry32(trace["seed"])
        # The error page consumes one random number before the game boots.
        extra = trace["init"]["randCount"] - self._expected_boot_randoms()
        for _ in range(extra):
            self.rng()
        self.sched = Scheduler(self.rng)
        self.canvas = Canvas(load_sheet(), 600, 150)
        self.canvas.log = []
        self.sched.now = trace["start"]
        self.runner = Runner(self.canvas, self.sched)
        self.runner.start()
        self.keep_detail = keep_detail

    @staticmethod
    def _expected_boot_randoms() -> int:
        return 7  # cloud gap + y, two stars (x, y each), blink delay

    def init_lines(self) -> list[str]:
        lines = [canon(e) for e in self.canvas.log]
        self.canvas.log = []
        self.runner.initialize_high_score(self.trace.get("highScore", 0))
        return lines

    def run(self) -> tuple[list[str], list[str], dict]:
        events: dict[int, list] = {}
        for f, kind, code in self.trace["events"]:
            events.setdefault(f, []).append((kind, code))
        frames, states = [], []
        details = {}
        for f in range(self.trace["frames"]):
            self.canvas.log = []
            for kind, code in events.get(f, []):
                getattr(self.runner, KEYS[kind])(code)
            self.sched.now += self.trace["dt"]
            self.runner.frame(self.sched.now)
            lines = [canon(e) for e in self.canvas.log]
            frames.append(hash2("\n".join(lines)) + ":" + str(len(lines)))
            state = snapshot(self.sched, self.runner, self.rng)
            states.append(hash2(state))
            if self.keep_detail:
                details[f] = (lines, state)
        return frames, states, details


BLOCK = 100


def block_hashes(frames: list[str], states: list[str]) -> list[str]:
    out = []
    for i in range(0, len(frames), BLOCK):
        out.append(hash2("\n".join(frames[i : i + BLOCK] + states[i : i + BLOCK])))
    return out


def make_fixture(trace_path: Path, out_path: Path) -> None:
    """Shrink a full trace into a committed fixture (hashes per 100-frame block)."""
    trace = json.loads(trace_path.read_text())
    replay = Replay(trace)
    replay.init_lines()
    frames, states, _ = replay.run()
    if frames != trace["frameHashes"] or states != trace["stateHashes"]:
        raise SystemExit("refusing to write a fixture from a trace that does not match")
    keep = {k: trace[k] for k in ("chrome", "seed", "botSeed", "mistake", "frames", "highScore", "dt", "start", "events")}
    keep["init"] = {"randCount": trace["init"]["randCount"], "init": trace["init"]["init"]}
    keep["block"] = BLOCK
    keep["blockHashes"] = block_hashes(frames, states)
    out_path.write_text(json.dumps(keep, separators=(",", ":")))
    print(f"wrote {out_path} ({out_path.stat().st_size // 1024} KiB)")


def main(argv: list[str]) -> int:
    if argv[1] == "--make-fixture":
        make_fixture(Path(argv[2]), Path(argv[3]))
        return 0
    path = Path(argv[1])
    trace = json.loads(path.read_text())
    replay = Replay(trace, keep_detail="--detail" in argv)
    init = replay.init_lines()
    chrome_init = trace["init"]["init"]
    ok = True
    if init != chrome_init:
        ok = False
        print(f"INIT MISMATCH: python {len(init)} lines, chrome {len(chrome_init)}")
        for i, (a, b) in enumerate(zip(init, chrome_init)):
            if a != b:
                print(f"  first diff at {i}:\n   py  {a}\n   chr {b}")
                break
    else:
        print(f"init ok ({len(init)} canvas calls)")
    frames, states, details = replay.run()
    bad = [i for i in range(len(frames)) if frames[i] != trace["frameHashes"][i] or states[i] != trace["stateHashes"][i]]
    if not bad:
        print(f"PARITY OK: {len(frames)} frames, every canvas call and state snapshot matches Chrome")
        return 0 if ok else 1
    first = bad[0]
    print(f"MISMATCH at frame {first} ({len(bad)} of {len(frames)} frames differ)")
    print(f"  draws: py {frames[first]} chrome {trace['frameHashes'][first]}")
    print(f"  state: py {states[first]} chrome {trace['stateHashes'][first]}")
    chrome_detail = trace.get("detail", {})
    if str(first) in chrome_detail and first in details:
        c = chrome_detail[str(first)]
        py_lines, py_state = details[first]
        print("  --- state (now|dist|speed|runT|time|invT|playing|crashed|paused|activated|intro|inverted|htmlInv|trex x,y,vel,status,jumping,ducking,drop,blink,frame,timer|n,first,last,size|night op,phase,x|clouds|hl0,hl1|hs len|highest|rand)")
        print("  py :", py_state)
        print("  chr:", c["state"])
        print("  --- draws")
        import difflib
        for line in difflib.unified_diff(py_lines, c["log"], "python", "chrome", lineterm="", n=2):
            print("  ", line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

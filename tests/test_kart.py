"""Kart physics and scoring, with no terminal involved."""

from __future__ import annotations

import console  # noqa: F401  (import the package the plugin ships)

from console.games.kart.game import (
    DT,
    LANES,
    RACE_METRES,
    PX_PER_METRE,
    KartGame,
)


def _full_throttle(state):
    return {"lane": 1, "throttle": 1.0}


def test_frame_shape_and_palette():
    game = KartGame()
    game.state.phase = "racing"
    game.state.countdown = 0.0
    game.handle_key("right", True, 0.0)
    for i in range(120):
        game.tick(i * 1000.0 / 60.0)
    frame = game.frame(2000.0)
    assert frame.width == 600 and frame.height == 150
    assert len(frame.pixels) == 600 * 150
    assert len(frame.palette) == 256


def test_race_finishes_and_is_deterministic():
    a = KartGame().simulate(_full_throttle, seconds=120, seed=11)
    b = KartGame().simulate(_full_throttle, seconds=120, seed=11)
    assert a == b
    assert a["finished"] is True
    assert a["distance_m"] >= RACE_METRES - 1
    assert a["time_s"] > 0


def test_lane_is_clamped_and_eased():
    game = KartGame()
    game.state.phase = "racing"
    game.state.countdown = 0.0
    for _ in range(40):
        game.handle_key("up", True, 0.0)
    assert game._pending_lane == 0
    game.tick(0.0)
    game.tick(1000.0)
    assert 0.0 <= game.player.lane <= 2.0
    assert LANES[0] < LANES[1] < LANES[2]


def test_speed_needs_throttle():
    sitting = KartGame().simulate(lambda s: {"lane": 1, "throttle": 0.0}, seconds=10, seed=5)
    moving = KartGame().simulate(_full_throttle, seconds=10, seed=5)
    assert moving["distance_m"] > sitting["distance_m"] + 10


def test_boost_pad_is_consumed_once():
    game = KartGame()
    game.state.phase = "racing"
    game.state.countdown = 0.0
    pad = next(p for p in game.pads if p.kind == "boost")
    game.player.dist = pad.dist - 1
    game.player.lane = float(pad.lane)
    game.state.elapsed = 1.0
    game.step(DT)
    assert pad.used is True
    assert game.state.boost_timer > 0
    game.step(DT)
    assert game.state.boost_timer <= 1.1

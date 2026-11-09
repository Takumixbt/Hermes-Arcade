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


def test_coin_is_collected_once_and_scores():
    game = KartGame()
    game.state.phase = "racing"
    game.state.countdown = 0.0
    coin = game.coins[0]
    game.player.dist = coin.dist
    game.player.lane = float(coin.lane)
    game.state.elapsed = 1.0
    game.step(DT)
    assert coin.taken is True
    assert game.state.score == 40 and game.state.coins == 1
    game.step(DT)
    assert game.state.score == 40


def test_drift_then_release_grants_a_boost():
    game = KartGame()
    game.state.phase = "racing"
    game.state.countdown = 0.0
    game.player.speed = 220.0
    game.handle_key("left", True, 0.0)
    for _ in range(8):
        game.step(DT)
    assert game.state.drift > 0.4
    game.handle_key("left", False, 0.0)
    game.step(DT)
    assert game.state.boost_timer > 0


def test_slipstream_detects_a_rival_just_ahead():
    game = KartGame()
    game.player.dist = 1000.0
    rival = game.rivals[0]
    rival.dist = 1040.0
    rival.lane = game.player.lane
    assert game._draft(game.player) is True
    rival.dist = 1300.0
    assert game._draft(game.player) is False


def test_each_seed_picks_one_stable_theme():
    assert len({KartGame(seed=i).theme_name for i in range(3)}) == 3
    assert KartGame(seed=7).theme_name == KartGame(seed=7).theme_name

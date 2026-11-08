"""Behaviour of the dino wrapper: pictures, night mode, intro, pause, scores."""

import pytest

from console.games.dino.demo import DemoDino
from console.games.dino.game import DinoGame, _invert_lut

DT = 1000 / 60


def run(game, seconds, t=0.0, press=None):
    """Tick ``game`` for ``seconds``; ``press`` maps frame index -> list of (key, pressed)."""
    frames = int(seconds * 60)
    for i in range(frames):
        t += DT
        for key, pressed in (press or {}).get(i, []):
            game.handle_key(key, pressed, t)
        game.tick(t)
    return t


def test_first_frame_is_the_waiting_dino():
    g = DinoGame(seed=1)
    g.tick(1000.0)
    f = g.frame(1000.0)
    # Before the first jump Chrome shows only the 44px container.
    assert f.width == 44 and f.height == 150
    assert len(f.pixels) == 44 * 150
    assert f.page == 255
    ink = {83, 255}
    assert ink <= set(f.pixels)


def test_container_grows_to_full_width_after_first_jump():
    g = DinoGame(seed=1)
    t = run(g, 0.5, 1000.0)
    g.handle_key("space", True, t)
    g.handle_key("space", False, t)
    widths = []
    for _ in range(120):
        t += DT
        g.tick(t)
        widths.append(g.frame(t).width)
    assert widths[0] == 44
    assert widths[-1] == 600
    assert widths == sorted(widths), "the container only ever grows during the intro"


def test_night_mode_inverts_the_page():
    assert _invert_lut(1.0)[255] == 0 and _invert_lut(1.0)[83] == 172
    assert _invert_lut(0.0)[83] == 83
    mid = _invert_lut(0.5)
    assert abs(mid[255] - 128) <= 1 and abs(mid[0] - 128) <= 1  # halfway through the fade: mid grey


def test_full_game_runs_without_error_and_scores():
    g = DemoDino.create()
    t = run(g, 90.0)
    assert g.runner.distance_ran > 0
    f = g.frame(t)
    assert f.width in range(44, 601)
    assert max(f.pixels) <= 255


def test_high_score_is_reported_on_game_over():
    seen = []
    g = DinoGame(seed=3, on_high_score=seen.append)
    t = run(g, 0.2, 1000.0)
    g.handle_key("space", True, t)
    g.handle_key("space", False, t)
    # run without ever jumping again: the first cactus ends the game
    t = run(g, 30.0, t)
    assert g.runner.crashed
    assert seen and seen[-1] > 0
    assert g.high_score == seen[-1]


def _crash(g, t):
    """Start a run and never jump, until the first cactus hits."""
    t = run(g, 0.2, t)
    g.handle_key("space", True, t)
    g.handle_key("space", False, t)
    while not g.runner.crashed:
        t += DT
        g.tick(t)
    return t


def test_cannot_restart_straight_after_game_over():
    g = DinoGame(seed=3)
    t = _crash(g, 1000.0)
    g.handle_key("space", True, t + 100)
    g.handle_key("space", False, t + 100)
    assert g.runner.crashed, "Chrome ignores the jump key for 1200 ms after a crash"
    t = run(g, 1.5, t)
    g.handle_key("space", True, t)
    g.handle_key("space", False, t)
    assert not g.runner.crashed


def test_enter_restarts_immediately():
    g = DinoGame(seed=3)
    t = _crash(g, 1000.0)
    g.handle_key("enter", True, t)
    g.handle_key("enter", False, t)
    assert not g.runner.crashed


def test_pause_and_resume_like_a_blurred_tab():
    g = DemoDino.create()
    t = run(g, 8.0)
    assert g.runner.playing
    g.pause(t)
    assert not g.runner.playing and g.runner.paused
    before = g.runner.distance_ran
    for _ in range(300):  # no autopilot here: Chrome resumes a paused game on a jump key-up
        t += DT
        DinoGame.tick(g, t)
    assert g.runner.distance_ran == before, "nothing moves while paused"
    g.resume(t + 5000)
    assert g.runner.playing and not g.runner.paused


def test_unknown_keys_are_ignored():
    g = DinoGame(seed=1)
    g.tick(0.0)
    g.handle_key("x", True, 10.0)
    g.handle_key("left", True, 10.0)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_same_seed_same_game(seed):
    a, b = DemoDino(seed=seed), DemoDino(seed=seed)
    ta = run(a, 20.0)
    tb = run(b, 20.0)
    assert a.frame(ta).pixels == b.frame(tb).pixels

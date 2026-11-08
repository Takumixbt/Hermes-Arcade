"""The game loop. Port of the ``Runner`` class in Chromium's ``offline.ts``.

Everything that touches a browser (DOM, CSS, audio, touch, gamepad, the
accessibility mode) is gone; what is left is the game itself, statement for
statement. Three bits of the original's CSS are modelled explicitly because
they are visible on screen:

* the 400 ms ``intro`` animation that widens the 44 px container to 600 px
  (``intro_anim_start``; ``start_game`` fires when it ends),
* the ``inverted`` class on ``<html>`` that turns the page black (night mode),
  exposed as ``html_inverted``.
"""

from __future__ import annotations

import math
from typing import Callable

from .constants import (
    FPS,
    GAMEOVER_CLEAR_TIME,
    INTRO_ANIMATION_MS,
    KEY_DUCK,
    KEY_JUMP,
    KEY_RESTART,
    SPRITE,
    Dimensions,
    RunnerConfig,
)
from .distance_meter import DistanceMeter
from .game_over_panel import GameOverPanel
from .horizon import Horizon
from .jsrt import Scheduler
from .physics import check_for_collision
from .trex import Status as TrexStatus
from .trex import Trex


class Runner:
    def __init__(
        self,
        ctx,
        sched: Scheduler,
        on_high_score: Callable[[int], None] | None = None,
        on_sound: Callable[[str], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.sched = sched
        self.config = RunnerConfig()
        self.dimensions = Dimensions()
        self.on_high_score = on_high_score
        self.on_sound = on_sound

        self.t_rex: Trex = None  # type: ignore[assignment]
        self.distance_meter: DistanceMeter = None  # type: ignore[assignment]
        self.game_over_panel: GameOverPanel | None = None
        self.horizon: Horizon = None  # type: ignore[assignment]

        self.ms_per_frame = 1000 / FPS
        self.time = 0.0
        self.distance_ran = 0.0
        self.running_time = 0.0
        self.current_speed = self.config.speed
        self.raq_id = 0
        self.play_count = 0

        self.activated = False
        self.playing = False
        self.playing_intro = False
        self.crashed = False
        self.paused = False
        self.inverted = False
        self.is_dark_mode = False
        self.update_pending = False
        self.highest_score = 0
        self.sync_highest_score = False
        self.invert_timer = 0.0
        self.invert_trigger = False

        # Browser state the original keeps in the DOM.
        self.html_inverted = False
        self.intro_anim_start: float | None = None
        self.visibility_listeners = False

    # -- setup ------------------------------------------------------------

    def start(self) -> None:
        """Runner.init(): build the world and run the first update."""
        self.set_speed()
        self.ctx.fill_style = "#f7f7f7"
        self.ctx.fill()
        self.horizon = Horizon(self.ctx, SPRITE, self.dimensions, self.config.gap_coefficient, self)
        self.distance_meter = DistanceMeter(self.ctx, SPRITE["textSprite"], self.dimensions.width, self)
        self.t_rex = Trex(self.ctx, SPRITE["tRex"], self)
        self.update()

    def set_speed(self, new_speed: float | None = None) -> None:
        speed = new_speed or self.current_speed
        if self.dimensions.width < 600:  # never true here; kept for parity
            self.current_speed = speed
        elif new_speed:
            self.current_speed = new_speed

    def initialize_high_score(self, high_score: float) -> None:
        self.sync_highest_score = True
        high_score = math.ceil(high_score)
        if high_score < self.highest_score:
            if self.on_high_score:
                self.on_high_score(self.highest_score)
            return
        self.highest_score = high_score
        self.distance_meter.set_high_score(self.highest_score)

    def save_high_score(self, distance_ran: float, reset_score: bool = False) -> None:
        self.highest_score = math.ceil(distance_ran)
        self.distance_meter.set_high_score(self.highest_score)
        if self.sync_highest_score and self.on_high_score:
            self.on_high_score(self.highest_score)

    # -- frame driving ----------------------------------------------------

    def frame(self, now: float) -> None:
        """One display refresh: CSS animation events first, then rAF callbacks."""
        if self.intro_anim_start is not None and now >= self.intro_anim_start + INTRO_ANIMATION_MS:
            self.sched.now = now
            self.intro_anim_start = None
            self.start_game()
        self.sched.run_frame(now)

    def container_width(self, now: float) -> float:
        """Width of ``.runner-container`` (what is visible of the canvas)."""
        from .constants import CONTAINER_INITIAL_WIDTH

        if not self.activated:
            return CONTAINER_INITIAL_WIDTH
        if self.intro_anim_start is not None:
            from .cssmath import ease_out

            t = (now - self.intro_anim_start) / INTRO_ANIMATION_MS
            if t < 1:
                eased = ease_out(max(0.0, t))
                return CONTAINER_INITIAL_WIDTH + (self.dimensions.width - CONTAINER_INITIAL_WIDTH) * eased
        return self.dimensions.width

    # -- the original, statement for statement -----------------------------

    def play_intro(self) -> None:
        if not self.activated and not self.crashed:
            self.playing_intro = True
            self.t_rex.playing_intro = True
            # CSS: container width 44px -> 600px over .4s, then 'animationend'.
            self.intro_anim_start = self.sched.get_time_stamp()
            self.set_play_status(True)
            self.activated = True
        elif self.crashed:
            self.restart()

    def start_game(self) -> None:
        self.running_time = 0
        self.playing_intro = False
        self.t_rex.playing_intro = False
        self.play_count += 1
        self.visibility_listeners = True

    def clear_canvas(self) -> None:
        self.ctx.clear_rect(0, 0, self.dimensions.width, self.dimensions.height)

    def update(self, _now: float | None = None) -> None:
        self.update_pending = False

        now = self.sched.get_time_stamp()
        delta_time = now - (self.time or now)
        self.time = now

        if self.playing:
            self.clear_canvas()

            if self.t_rex.jumping:
                self.t_rex.update_jump(delta_time)

            self.running_time += delta_time
            has_obstacles = self.running_time > self.config.clear_time

            # First jump triggers the intro.
            if self.t_rex.jump_count == 1 and not self.playing_intro:
                self.play_intro()

            # The horizon doesn't move until the intro is over.
            if self.playing_intro:
                self.horizon.update(0, self.current_speed, has_obstacles, False)
            elif not self.crashed:
                show_night_mode = self.is_dark_mode != self.inverted
                delta_time = 0 if not self.activated else delta_time
                self.horizon.update(delta_time, self.current_speed, has_obstacles, show_night_mode)

            # Check for collisions.
            first_obstacle = self.horizon.obstacles[0] if self.horizon.obstacles else None
            collision = has_obstacles and first_obstacle is not None and check_for_collision(first_obstacle, self.t_rex)

            if not collision:
                self.distance_ran += self.current_speed * delta_time / self.ms_per_frame

                if self.current_speed < self.config.max_speed:
                    self.current_speed += self.config.acceleration
            else:
                self.game_over()

            play_achievement_sound = self.distance_meter.update(delta_time, math.ceil(self.distance_ran))

            if play_achievement_sound:
                self.play_sound("reached")

            # Night mode.
            if self.invert_timer > self.config.invert_fade_duration:
                self.invert_timer = 0
                self.invert_trigger = False
                self.invert(False)
            elif self.invert_timer:
                self.invert_timer += delta_time
            else:
                actual_distance = self.distance_meter.get_actual_distance(math.ceil(self.distance_ran))

                if actual_distance > 0:
                    self.invert_trigger = not (actual_distance % self.config.invert_distance)

                    if self.invert_trigger and self.invert_timer == 0:
                        self.invert_timer += delta_time
                        self.invert(False)

        if self.playing or (not self.activated and self.t_rex.blink_count < self.config.max_blink_count):
            self.t_rex.update(delta_time)
            self.schedule_next_update()

    # -- input ------------------------------------------------------------

    def on_key_down(self, key_code: int) -> None:
        if not self.crashed and not self.paused:
            if key_code in KEY_JUMP:
                if not self.playing:
                    self.set_play_status(True)
                    self.update()

                if not self.t_rex.jumping and not self.t_rex.ducking:
                    self.play_sound("press")
                    self.t_rex.start_jump(self.current_speed)
            elif self.playing and key_code in KEY_DUCK:
                if self.t_rex.jumping:
                    # Speed drop, activated only when jump key is not pressed.
                    self.t_rex.set_speed_drop()
                elif not self.t_rex.jumping and not self.t_rex.ducking:
                    # Duck.
                    self.t_rex.set_duck(True)

    def on_key_up(self, key_code: int) -> None:
        is_jump_key = key_code in KEY_JUMP

        if self.is_running() and is_jump_key:
            self.t_rex.end_jump()
        elif key_code in KEY_DUCK:
            self.t_rex.speed_drop = False
            self.t_rex.set_duck(False)
        elif self.crashed:
            # Check that enough time has elapsed before allowing jump key to restart.
            delta_time = self.sched.get_time_stamp() - self.time

            if key_code in KEY_RESTART or (delta_time >= self.config.gameover_clear_time and key_code in KEY_JUMP):
                self.handle_game_over_keys()
        elif self.paused and is_jump_key:
            # Reset the jump state
            self.t_rex.reset()
            self.play()

    def handle_game_over_keys(self) -> None:
        if self.distance_meter.has_clicked_on_high_score() and self.highest_score:
            pass  # unreachable from a keyboard: the key 'click' is at (0, 0)
        else:
            self.distance_meter.cancel_high_score_flashing()
            self.restart()

    # -- state machine ----------------------------------------------------

    def schedule_next_update(self) -> None:
        if not self.update_pending:
            self.update_pending = True
            self.raq_id = self.sched.request_animation_frame(self.update)

    def is_running(self) -> bool:
        return bool(self.raq_id)

    def game_over(self) -> None:
        self.play_sound("hit")
        self.stop()
        self.crashed = True
        self.distance_meter.achievement = False

        self.t_rex.update(100, TrexStatus.CRASHED)

        # Game over panel.
        if not self.game_over_panel:
            self.game_over_panel = GameOverPanel(self.ctx, SPRITE["textSprite"], SPRITE["restart"], self.dimensions, self)
        self.game_over_panel.draw()

        # Update the high score.
        if self.distance_ran > self.highest_score:
            self.save_high_score(self.distance_ran)

        # Reset the time clock.
        self.time = self.sched.get_time_stamp()

    def stop(self) -> None:
        self.set_play_status(False)
        self.paused = True
        self.sched.cancel_animation_frame(self.raq_id)
        self.raq_id = 0

    def play(self) -> None:
        if not self.crashed:
            self.set_play_status(True)
            self.paused = False
            self.t_rex.update(0, TrexStatus.RUNNING)
            self.time = self.sched.get_time_stamp()
            self.update()

    def restart(self) -> None:
        if not self.raq_id:
            self.play_count += 1
            self.running_time = 0
            self.set_play_status(True)
            self.paused = False
            self.crashed = False
            self.distance_ran = 0
            self.set_speed(self.config.speed)
            self.time = self.sched.get_time_stamp()
            self.clear_canvas()
            self.distance_meter.reset()
            self.horizon.reset()
            self.t_rex.reset()
            self.play_sound("press")
            self.invert(True)
            self.update()
            self.game_over_panel.reset()  # type: ignore[union-attr]

    def set_play_status(self, is_playing: bool) -> None:
        self.playing = is_playing

    def on_visibility_change(self, hidden: bool) -> None:
        """``blur``/``focus``/``visibilitychange``, registered in startGame()."""
        if not self.visibility_listeners:
            return
        if hidden:
            self.stop()
        elif not self.crashed:
            self.t_rex.reset()
            self.play()

    def play_sound(self, name: str) -> None:
        if self.on_sound:
            self.on_sound(name)

    def invert(self, reset: bool) -> None:
        if reset:
            self.html_inverted = False
            self.invert_timer = 0
            self.inverted = False
        else:
            self.html_inverted = self.invert_trigger
            self.inverted = self.html_inverted

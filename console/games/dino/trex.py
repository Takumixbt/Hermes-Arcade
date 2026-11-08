"""The T-Rex. Port of Chromium's ``trex.ts`` (desktop, 1x, normal speed)."""

from __future__ import annotations

import math
from enum import IntEnum

from .constants import (
    BLINK_TIMING,
    DEFAULT_HEIGHT,
    TREX_COLLISION_DUCKING,
    TREX_COLLISION_RUNNING,
    CollisionBox,
    SpritePosition,
    TrexConfig,
)
from .jsrt import js_round


class Status(IntEnum):
    CRASHED = 0
    DUCKING = 1
    JUMPING = 2
    RUNNING = 3
    WAITING = 4


ANIM_FRAMES = {
    Status.WAITING: ([44, 0], 1000 / 3),
    Status.RUNNING: ([88, 132], 1000 / 12),
    Status.CRASHED: ([220], 1000 / 60),
    Status.JUMPING: ([0], 1000 / 60),
    Status.DUCKING: ([264, 323], 1000 / 8),
}

_DUCKING_BOXES = [CollisionBox(*b) for b in TREX_COLLISION_DUCKING]
_RUNNING_BOXES = [CollisionBox(*b) for b in TREX_COLLISION_RUNNING]


class Trex:
    def __init__(self, ctx, sprite_pos: SpritePosition, runner) -> None:
        self.ctx = ctx
        self.sprite_pos = sprite_pos
        self.runner = runner
        self.sched = runner.sched
        self.config = TrexConfig()

        self.playing_intro = False
        self.x_pos = 0
        self.y_pos = 0
        self.jump_count = 0
        self.ducking = False
        self.blink_count = 0
        self.jumping = False
        self.speed_drop = False

        self.x_initial_pos = 0
        self.current_frame = 0
        self.current_anim_frames: list[int] = []
        self.blink_delay = 0
        self.anim_start_time = 0.0
        self.timer = 0.0
        self.ms_per_frame = 1000 / 60
        self.status = Status.WAITING
        self.jump_velocity = 0.0
        self.reached_min_height = False
        self.flashing = False

        self.ground_y_pos = DEFAULT_HEIGHT - self.config.height - runner.config.bottom_pad
        self.y_pos = self.ground_y_pos
        self.min_jump_height = self.ground_y_pos - self.config.min_jump_height

        self.draw(0, 0)
        self.update(0, Status.WAITING)

    def update(self, delta_time: float, status: Status | None = None) -> None:
        self.timer += delta_time

        if status is not None:
            self.status = status
            self.current_frame = 0
            self.ms_per_frame = ANIM_FRAMES[status][1]
            self.current_anim_frames = ANIM_FRAMES[status][0]

            if status == Status.WAITING:
                self.anim_start_time = self.sched.get_time_stamp()
                self.set_blink_delay()

        # Game intro animation, T-rex moves in from the left.
        if self.playing_intro and self.x_pos < self.config.start_x_pos:
            self.x_pos += js_round((self.config.start_x_pos / self.config.intro_duration) * delta_time)
            self.x_initial_pos = self.x_pos

        if self.status == Status.WAITING:
            self.blink(self.sched.get_time_stamp())
        else:
            self.draw(self.current_anim_frames[self.current_frame], 0)

        # Update the frame position.
        if not self.flashing and self.timer >= self.ms_per_frame:
            self.current_frame = 0 if self.current_frame == len(self.current_anim_frames) - 1 else self.current_frame + 1
            self.timer = 0

        # Speed drop becomes duck if the down key is still being pressed.
        if self.speed_drop and self.y_pos == self.ground_y_pos:
            self.speed_drop = False
            self.set_duck(True)

    def draw(self, x: int, y: int) -> None:
        source_x = x
        source_y = y
        source_width = self.config.width_duck if (self.ducking and self.status != Status.CRASHED) else self.config.width
        source_height = self.config.height
        output_height = source_height
        output_width = self.config.width

        source_x += self.sprite_pos.x
        source_y += self.sprite_pos.y

        # Ducking.
        if self.ducking and self.status != Status.CRASHED:
            self.ctx.draw_image(
                source_x, source_y, source_width, source_height, self.x_pos, self.y_pos, self.config.width_duck, output_height
            )
        else:
            # Crashed whilst ducking. Trex is standing up so needs adjusting.
            if self.ducking and self.status == Status.CRASHED:
                self.x_pos += 1
            self.ctx.draw_image(
                source_x, source_y, source_width, source_height, self.x_pos, self.y_pos, output_width, output_height
            )
        self.ctx.global_alpha = 1

    def set_blink_delay(self) -> None:
        # Math.ceil(Math.random() * BLINK_TIMING)
        self.blink_delay = math.ceil(self.sched.random() * BLINK_TIMING)

    def blink(self, time: float) -> None:
        delta_time = time - self.anim_start_time

        if delta_time >= self.blink_delay:
            self.draw(self.current_anim_frames[self.current_frame], 0)

            if self.current_frame == 1:
                # Set new random delay to blink.
                self.set_blink_delay()
                self.anim_start_time = time
                self.blink_count += 1

    def start_jump(self, speed: float) -> None:
        if not self.jumping:
            self.update(0, Status.JUMPING)
            # Tweak the jump velocity based on the speed.
            self.jump_velocity = self.config.initial_jump_velocity - (speed / 10)
            self.jumping = True
            self.reached_min_height = False
            self.speed_drop = False

    def end_jump(self) -> None:
        if self.reached_min_height and self.jump_velocity < self.config.drop_velocity:
            self.jump_velocity = self.config.drop_velocity

    def update_jump(self, delta_time: float) -> None:
        ms_per_frame = ANIM_FRAMES[self.status][1]
        frames_elapsed = delta_time / ms_per_frame

        # Speed drop makes Trex fall faster.
        if self.speed_drop:
            self.y_pos += js_round(self.jump_velocity * self.config.speed_drop_coefficient * frames_elapsed)
        else:
            self.y_pos += js_round(self.jump_velocity * frames_elapsed)

        self.jump_velocity += self.config.gravity * frames_elapsed

        # Minimum height has been reached.
        if self.y_pos < self.min_jump_height or self.speed_drop:
            self.reached_min_height = True

        # Reached max height.
        if self.y_pos < self.config.max_jump_height or self.speed_drop:
            self.end_jump()

        # Back down at ground level. Jump completed.
        if self.y_pos > self.ground_y_pos:
            self.reset()
            self.jump_count += 1

    def set_speed_drop(self) -> None:
        self.speed_drop = True
        self.jump_velocity = 1

    def set_duck(self, is_ducking: bool) -> None:
        if is_ducking and self.status != Status.DUCKING:
            self.update(0, Status.DUCKING)
            self.ducking = True
        elif self.status == Status.DUCKING:
            self.update(0, Status.RUNNING)
            self.ducking = False

    def reset(self) -> None:
        self.x_pos = self.x_initial_pos
        self.y_pos = self.ground_y_pos
        self.jump_velocity = 0
        self.jumping = False
        self.ducking = False
        self.update(0, Status.RUNNING)
        self.speed_drop = False
        self.jump_count = 0

    def get_collision_boxes(self) -> list[CollisionBox]:
        return _DUCKING_BOXES if self.ducking else _RUNNING_BOXES

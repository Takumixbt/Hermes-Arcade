"""Cacti and pterodactyls. Port of Chromium's ``obstacle.ts``."""

from __future__ import annotations

from .constants import FPS, MAX_GAP_COEFFICIENT, MAX_OBSTACLE_LENGTH, CollisionBox, Dimensions, ObstacleType, SpritePosition
from .jsrt import js_floor, js_round


class Obstacle:
    def __init__(
        self,
        ctx,
        type_config: ObstacleType,
        sprite_pos: SpritePosition,
        dimensions: Dimensions,
        gap_coefficient: float,
        speed: float,
        x_offset: float,
        runner,
    ) -> None:
        self.ctx = ctx
        self.sched = runner.sched
        self.type_config = type_config
        self.sprite_pos = sprite_pos

        self.collision_boxes: list[CollisionBox] = []
        self.following_obstacle_created = False
        self.gap = 0
        self.jump_alerted = False
        self.remove = False
        self.width = 0
        self.y_pos = 0
        self.speed_offset = 0.0
        self.current_frame = 0
        self.timer = 0.0

        self.gap_coefficient = gap_coefficient
        self.size = self.sched.get_random_num(1, MAX_OBSTACLE_LENGTH)
        self.x_pos = dimensions.width + x_offset
        self._init(speed)

    def _init(self, speed: float) -> None:
        self.clone_collision_boxes()

        # Only allow sizing if we're at the right speed.
        if self.size > 1 and self.type_config.multiple_speed > speed:
            self.size = 1

        self.width = self.type_config.width * self.size

        # Check if obstacle can be positioned at various heights.
        y_positions = self.type_config.y_pos
        if len(y_positions) > 1:
            self.y_pos = y_positions[self.sched.get_random_num(0, len(y_positions) - 1)]
        else:
            self.y_pos = y_positions[0]

        self.draw()

        # Make collision box adjustments, Central box is adjusted to the size as one box.
        if self.size > 1:
            self.collision_boxes[1].width = self.width - self.collision_boxes[0].width - self.collision_boxes[2].width
            self.collision_boxes[2].x = self.width - self.collision_boxes[2].width

        # For obstacles that go at a different speed from the horizon.
        if self.type_config.speed_offset:
            self.speed_offset = self.type_config.speed_offset if self.sched.random() > 0.5 else -self.type_config.speed_offset

        # Gap is the distance to the next obstacle.
        self.gap = self.get_gap(self.gap_coefficient, speed)

    def draw(self) -> None:
        source_width = self.type_config.width
        source_height = self.type_config.height

        # X position in sprite.
        source_x = (source_width * self.size) * (0.5 * (self.size - 1)) + self.sprite_pos.x

        # Animation frames.
        if self.current_frame > 0:
            source_x += source_width * self.current_frame

        self.ctx.draw_image(
            source_x,
            self.sprite_pos.y,
            source_width * self.size,
            source_height,
            self.x_pos,
            self.y_pos,
            self.type_config.width * self.size,
            self.type_config.height,
        )

    def update(self, delta_time: float, speed: float) -> None:
        if not self.remove:
            if self.type_config.speed_offset:
                speed += self.speed_offset
            self.x_pos -= js_floor((speed * FPS / 1000) * delta_time)

            # Update frame
            if self.type_config.num_frames:
                self.timer += delta_time
                if self.timer >= self.type_config.frame_rate:
                    self.current_frame = 0 if self.current_frame == self.type_config.num_frames - 1 else self.current_frame + 1
                    self.timer = 0

            self.draw()

            if not self.is_visible():
                self.remove = True

    def get_gap(self, gap_coefficient: float, speed: float) -> int:
        min_gap = js_round(self.width * speed + self.type_config.min_gap * gap_coefficient)
        max_gap = js_round(min_gap * MAX_GAP_COEFFICIENT)
        return self.sched.get_random_num(min_gap, max_gap)

    def is_visible(self) -> bool:
        return self.x_pos + self.width > 0

    def clone_collision_boxes(self) -> None:
        self.collision_boxes = [CollisionBox(*b) for b in self.type_config.collision_boxes]

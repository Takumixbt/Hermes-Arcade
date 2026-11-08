"""The score read-out. Port of Chromium's ``distance_meter.ts`` (LTR, 1x)."""

from __future__ import annotations

from .constants import (
    METER_ACHIEVEMENT_DISTANCE,
    METER_COEFFICIENT,
    METER_DEST_WIDTH,
    METER_FLASH_DURATION,
    METER_FLASH_ITERATIONS,
    METER_HEIGHT,
    METER_MAX_DISTANCE_UNITS,
    METER_WIDTH,
    SpritePosition,
)
from .jsrt import js_round


def _parse_digit(ch: str) -> int | None:
    return int(ch) if ch.isdigit() else None


class DistanceMeter:
    def __init__(self, ctx, sprite_pos: SpritePosition, canvas_width: int, runner) -> None:
        self.ctx = ctx
        self.sched = runner.sched
        self.sprite_pos = sprite_pos
        self.canvas_width = canvas_width

        self.achievement = False
        self.x = 0
        self.y = 5
        self.max_score = 0
        self.high_score = "0"
        self.digits: list[str] = []
        self.default_string = ""
        self.flash_timer = 0.0
        self.flash_iterations = 0
        self.flashing_raf_id: int | None = None
        self.high_score_bounds: dict | None = None
        self.high_score_flashing = False
        self.max_score_units = METER_MAX_DISTANCE_UNITS

        self._init(canvas_width)

    def _init(self, width: int) -> None:
        max_distance_str = ""
        self.calc_x_pos(width)
        self.max_score = self.max_score_units
        for i in range(self.max_score_units):
            self.draw(i, 0)
            self.default_string += "0"
            max_distance_str += "9"
        self.max_score = int(max_distance_str)

    def calc_x_pos(self, canvas_width: int) -> None:
        self.x = canvas_width - (METER_DEST_WIDTH * (self.max_score_units + 1))

    def draw(self, digit_pos: int, value: int, high_score: bool = False) -> None:
        source_width = METER_WIDTH
        source_height = METER_HEIGHT
        source_x = METER_WIDTH * value
        source_y = 0

        target_x = digit_pos * METER_DEST_WIDTH
        target_y = self.y
        target_width = METER_WIDTH
        target_height = METER_HEIGHT

        source_x += self.sprite_pos.x
        source_y += self.sprite_pos.y

        ctx = self.ctx
        ctx.save()
        high_score_x = self.x - (self.max_score_units * 2) * METER_WIDTH
        ctx.translate(high_score_x if high_score else self.x, self.y)
        ctx.draw_image(source_x, source_y, source_width, source_height, target_x, target_y, target_width, target_height)
        ctx.restore()

    def get_actual_distance(self, distance: float) -> int:
        return js_round(distance * METER_COEFFICIENT) if distance else 0

    def update(self, delta_time: float, distance: float) -> bool:
        paint = True
        play_sound = False

        if not self.achievement:
            distance = self.get_actual_distance(distance)
            # Score has gone beyond the initial digit count.
            if distance > self.max_score and self.max_score_units == METER_MAX_DISTANCE_UNITS:
                self.max_score_units += 1
                self.max_score = int(str(self.max_score) + "9")

            if distance > 0:
                # Achievement unlocked.
                if distance % METER_ACHIEVEMENT_DISTANCE == 0:
                    # Flash score and play sound.
                    self.achievement = True
                    self.flash_timer = 0
                    play_sound = True

                # Create a string representation of the distance with leading 0.
                distance_str = (self.default_string + str(distance))[-self.max_score_units :]
                self.digits = list(distance_str)
            else:
                self.digits = list(self.default_string)
        else:
            # Control flashing of the score on reaching achievement.
            if self.flash_iterations <= METER_FLASH_ITERATIONS:
                self.flash_timer += delta_time

                if self.flash_timer < METER_FLASH_DURATION:
                    paint = False
                elif self.flash_timer > METER_FLASH_DURATION * 2:
                    self.flash_timer = 0
                    self.flash_iterations += 1
            else:
                self.achievement = False
                self.flash_iterations = 0
                self.flash_timer = 0

        # Draw the digits if not flashing.
        if paint:
            for i in range(len(self.digits) - 1, -1, -1):
                self.draw(i, int(self.digits[i]))

        self.draw_high_score()
        return play_sound

    def draw_high_score(self) -> None:
        if len(self.high_score) > 0:
            self.ctx.save()
            self.ctx.global_alpha = 0.8
            for i in range(len(self.high_score) - 1, -1, -1):
                character = self.high_score[i]
                pos = _parse_digit(character)
                if pos is None:
                    if character == "H":
                        pos = 10
                    elif character == "I":
                        pos = 11
                    else:
                        continue
                self.draw(i, pos, True)
            self.ctx.restore()

    def set_high_score(self, distance: float) -> None:
        distance = self.get_actual_distance(distance)
        high_score_str = (self.default_string + str(distance))[-self.max_score_units :]
        self.high_score = "HI " + high_score_str

    def has_clicked_on_high_score(self) -> bool:
        """Keyboard 'clicks' land at (0, 0), which never hits the bounds."""
        self.high_score_bounds = self.get_high_score_bounds()
        b = self.high_score_bounds
        x = y = 0
        return b["x"] <= x <= b["x"] + b["width"] and b["y"] <= y <= b["y"] + b["height"]

    def get_high_score_bounds(self) -> dict:
        return {
            "x": (self.x - (self.max_score_units * 2) * METER_WIDTH) - 4,
            "y": self.y,
            "width": METER_WIDTH * (len(self.high_score) + 1) + 4,
            "height": METER_HEIGHT + (4 * 2),
        }

    def clear_high_score_bounds(self) -> None:
        b = self.high_score_bounds
        ctx = self.ctx
        ctx.save()
        ctx.fill_style = "#fff"
        ctx.rect(b["x"], b["y"], b["width"], b["height"])
        ctx.fill()
        ctx.restore()

    def is_high_score_flashing(self) -> bool:
        return self.high_score_flashing

    def cancel_high_score_flashing(self) -> None:
        if self.flashing_raf_id:
            self.sched.cancel_animation_frame(self.flashing_raf_id)
        self.flash_iterations = 0
        self.flash_timer = 0
        self.high_score_flashing = False
        self.clear_high_score_bounds()
        self.draw_high_score()

    def reset(self) -> None:
        self.update(0, 0)
        self.achievement = False


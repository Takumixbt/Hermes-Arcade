"""Ground, clouds, night sky and obstacle spawning.

Ports of Chromium's ``horizon.ts``, ``horizon_line.ts``, ``cloud.ts`` and
``night_mode.ts``. The statement order matches the originals on purpose: the
draw order and the sequence of ``Math.random()`` calls decide what a frame
looks like, and the test-suite checks both against a real Chrome.
"""

from __future__ import annotations

from .constants import (
    BG_CLOUD_SPEED,
    CLOUD_FREQUENCY,
    CLOUD_HEIGHT,
    CLOUD_MAX_GAP,
    CLOUD_MAX_SKY_LEVEL,
    CLOUD_MIN_GAP,
    CLOUD_MIN_SKY_LEVEL,
    CLOUD_WIDTH,
    FPS,
    HORIZON_LINE,
    MAX_CLOUDS,
    NIGHT_FADE_SPEED,
    NIGHT_HEIGHT,
    NIGHT_MOON_SPEED,
    NIGHT_NUM_STARS,
    NIGHT_PHASES,
    NIGHT_STAR_MAX_Y,
    NIGHT_STAR_SIZE,
    NIGHT_STAR_SPEED,
    NIGHT_WIDTH,
    OBSTACLE_TYPES,
    SPRITE,
    Dimensions,
    SpritePosition,
)
from .jsrt import js_ceil, js_floor, js_round
from .obstacle import Obstacle


class HorizonLine:
    def __init__(self, ctx, line_config: dict, runner) -> None:
        self.ctx = ctx
        self.bump_threshold = 0.5
        self.sched = runner.sched
        self.sprite_pos = SpritePosition(line_config["sourceX"], line_config["sourceY"])
        self.dimensions = Dimensions(line_config["width"], line_config["height"])
        self.source_x_pos = [self.sprite_pos.x, self.sprite_pos.x + self.dimensions.width]
        self.x_pos = [0, self.dimensions.width]
        self.y_pos = line_config["yPos"]
        self.source_dimensions = Dimensions(line_config["width"], line_config["height"])
        self.draw()

    def get_random_type(self) -> int:
        return self.dimensions.width if self.sched.random() > self.bump_threshold else 0

    def draw(self) -> None:
        sd, d = self.source_dimensions, self.dimensions
        self.ctx.draw_image(self.source_x_pos[0], self.sprite_pos.y, sd.width, sd.height, self.x_pos[0], self.y_pos, d.width, d.height)
        self.ctx.draw_image(self.source_x_pos[1], self.sprite_pos.y, sd.width, sd.height, self.x_pos[1], self.y_pos, d.width, d.height)

    def update_x_pos(self, pos: int, increment: int) -> None:
        line1 = pos
        line2 = 1 if pos == 0 else 0

        self.x_pos[line1] -= increment
        self.x_pos[line2] = self.x_pos[line1] + self.dimensions.width

        if self.x_pos[line1] <= -self.dimensions.width:
            self.x_pos[line1] += self.dimensions.width * 2
            self.x_pos[line2] = self.x_pos[line1] - self.dimensions.width
            self.source_x_pos[line1] = self.get_random_type() + self.sprite_pos.x

    def update(self, delta_time: float, speed: float) -> None:
        increment = js_floor(speed * (FPS / 1000) * delta_time)
        self.update_x_pos(0 if self.x_pos[0] <= 0 else 1, increment)
        self.draw()

    def reset(self) -> None:
        self.x_pos[0] = 0
        self.x_pos[1] = self.dimensions.width


class Cloud:
    def __init__(self, ctx, sprite_pos: SpritePosition, container_width: int, runner) -> None:
        self.ctx = ctx
        self.sched = runner.sched
        self.x_pos = container_width
        self.sprite_pos = sprite_pos
        self.remove = False
        self.y_pos = 0
        self.gap = self.sched.get_random_num(CLOUD_MIN_GAP, CLOUD_MAX_GAP)
        self.init()

    def init(self) -> None:
        self.y_pos = self.sched.get_random_num(CLOUD_MAX_SKY_LEVEL, CLOUD_MIN_SKY_LEVEL)
        self.draw()

    def draw(self) -> None:
        self.ctx.save()
        self.ctx.draw_image(self.sprite_pos.x, self.sprite_pos.y, CLOUD_WIDTH, CLOUD_HEIGHT, self.x_pos, self.y_pos, CLOUD_WIDTH, CLOUD_HEIGHT)
        self.ctx.restore()

    def update(self, speed: float) -> None:
        if not self.remove:
            self.x_pos -= js_ceil(speed)
            self.draw()

            # Mark as removeable if no longer in the canvas.
            if not self.is_visible():
                self.remove = True

    def is_visible(self) -> bool:
        return self.x_pos + CLOUD_WIDTH > 0


class _Star:
    __slots__ = ("x", "y", "source_y")

    def __init__(self, x: float, y: float, source_y: int) -> None:
        self.x = x
        self.y = y
        self.source_y = source_y


class NightMode:
    def __init__(self, ctx, sprite_pos: SpritePosition, container_width: int, runner) -> None:
        self.ctx = ctx
        self.sched = runner.sched
        self.sprite_pos = sprite_pos
        self.container_width = container_width
        self.x_pos = 0.0
        self.y_pos = 30
        self.current_phase = 0
        self.opacity = 0.0
        self.stars: list[_Star] = [None] * NIGHT_NUM_STARS  # type: ignore[list-item]
        self.draw_stars = False
        self.place_stars()

    def update(self, activated: bool) -> None:
        # Moon phase.
        if activated and self.opacity == 0:
            self.current_phase += 1
            if self.current_phase >= len(NIGHT_PHASES):
                self.current_phase = 0

        # Fade in / out.
        if activated and (self.opacity < 1 or self.opacity == 0):
            self.opacity += NIGHT_FADE_SPEED
        elif self.opacity > 0:
            self.opacity -= NIGHT_FADE_SPEED

        # Set moon positioning.
        if self.opacity > 0:
            self.x_pos = self.update_x_pos(self.x_pos, NIGHT_MOON_SPEED)

            # Update stars.
            if self.draw_stars:
                for i in range(NIGHT_NUM_STARS):
                    star = self.stars[i]
                    star.x = self.update_x_pos(star.x, NIGHT_STAR_SPEED)
            self.draw()
        else:
            self.opacity = 0
            self.place_stars()
        self.draw_stars = True

    def update_x_pos(self, current_pos: float, speed: float) -> float:
        if current_pos < -NIGHT_WIDTH:
            current_pos = self.container_width
        else:
            current_pos -= speed
        return current_pos

    def draw(self) -> None:
        moon_source_width = NIGHT_WIDTH * 2 if self.current_phase == 3 else NIGHT_WIDTH
        moon_source_height = NIGHT_HEIGHT
        phase_x = NIGHT_PHASES[self.current_phase]
        moon_source_x = self.sprite_pos.x + phase_x
        moon_output_width = moon_source_width
        star_size = NIGHT_STAR_SIZE
        star_source_x = SPRITE["star"].x

        ctx = self.ctx
        ctx.save()
        ctx.global_alpha = self.opacity

        # Stars.
        if self.draw_stars:
            for star in self.stars:
                ctx.draw_image(star_source_x, star.source_y, star_size, star_size, js_round(star.x), star.y, NIGHT_STAR_SIZE, NIGHT_STAR_SIZE)

        # Moon.
        ctx.draw_image(
            moon_source_x, self.sprite_pos.y, moon_source_width, moon_source_height, js_round(self.x_pos), self.y_pos, moon_output_width, NIGHT_HEIGHT
        )

        ctx.global_alpha = 1
        ctx.restore()

    # Do star placement.
    def place_stars(self) -> None:
        segment_size = js_round(self.container_width / NIGHT_NUM_STARS)
        for i in range(NIGHT_NUM_STARS):
            x = self.sched.get_random_num(segment_size * i, segment_size * (i + 1))
            y = self.sched.get_random_num(0, NIGHT_STAR_MAX_Y)
            self.stars[i] = _Star(x, y, SPRITE["star"].y + NIGHT_STAR_SIZE * i)

    def reset(self) -> None:
        self.current_phase = 0
        self.opacity = 0
        self.update(False)


class Horizon:
    def __init__(self, ctx, sprite_pos: dict, dimensions: Dimensions, gap_coefficient: float, runner) -> None:
        self.ctx = ctx
        self.runner = runner
        self.sched = runner.sched
        self.dimensions = dimensions
        self.gap_coefficient = gap_coefficient
        self.sprite_pos = sprite_pos
        self.cloud_frequency = CLOUD_FREQUENCY
        self.cloud_speed = BG_CLOUD_SPEED
        self.obstacle_types = OBSTACLE_TYPES

        self.obstacles: list[Obstacle] = []
        self.obstacle_history: list[str] = []
        self.clouds: list[Cloud] = []
        self.horizon_lines: list[HorizonLine] = []

        # Cloud
        self.add_cloud()

        # Horizon
        self.horizon_lines.append(HorizonLine(ctx, HORIZON_LINE, runner))

        self.night_mode = NightMode(ctx, sprite_pos["moon"], dimensions.height, runner)

    def update(self, delta_time: float, current_speed: float, update_obstacles: bool, show_night_mode: bool) -> None:
        for line in self.horizon_lines:
            line.update(delta_time, current_speed)

        self.night_mode.update(show_night_mode)
        self.update_clouds(delta_time, current_speed)

        if update_obstacles:
            self.update_obstacles(delta_time, current_speed)

    def _update_background_el(self, el_speed: float, arr: list, max_bg_el: int, add_fn, frequency: float) -> None:
        num_elements = len(arr)

        if not num_elements:
            add_fn()
            return

        for i in range(num_elements - 1, -1, -1):
            arr[i].update(el_speed)

        last_el = arr[-1]

        # Check for adding a new element.
        if num_elements < max_bg_el and (self.dimensions.width - last_el.x_pos) > last_el.gap and frequency > self.sched.random():
            add_fn()

    def update_clouds(self, delta_time: float, speed: float) -> None:
        el_speed = self.cloud_speed / 1000 * delta_time * speed
        self._update_background_el(el_speed, self.clouds, MAX_CLOUDS, self.add_cloud, self.cloud_frequency)
        self.clouds = [c for c in self.clouds if not c.remove]

    def update_obstacles(self, delta_time: float, current_speed: float) -> None:
        # Obstacles, move to Horizon layer.
        updated_obstacles = list(self.obstacles)

        for obstacle in self.obstacles:
            obstacle.update(delta_time, current_speed)

            # Clean up existing obstacles.
            if obstacle.remove:
                updated_obstacles.pop(0)
        self.obstacles = updated_obstacles

        if len(self.obstacles) > 0:
            last_obstacle = self.obstacles[-1]

            if (
                last_obstacle
                and not last_obstacle.following_obstacle_created
                and last_obstacle.is_visible()
                and (last_obstacle.x_pos + last_obstacle.width + last_obstacle.gap) < self.dimensions.width
            ):
                self.add_new_obstacle(current_speed)
                last_obstacle.following_obstacle_created = True
        else:
            # Create new obstacles.
            self.add_new_obstacle(current_speed)

    def remove_first_obstacle(self) -> None:
        self.obstacles.pop(0)

    def add_new_obstacle(self, current_speed: float) -> None:
        # The alt-game "collectable" is never in the pool, so every entry counts.
        obstacle_count = len(self.obstacle_types) - 1
        obstacle_type_index = self.sched.get_random_num(0, obstacle_count) if obstacle_count > 0 else 0
        obstacle_type = self.obstacle_types[obstacle_type_index]

        # Check for multiples of the same type of obstacle. Also check obstacle is available at current speed.
        if (obstacle_count > 0 and self.duplicate_obstacle_check(obstacle_type.type)) or current_speed < obstacle_type.min_speed:
            self.add_new_obstacle(current_speed)
        else:
            obstacle_sprite_pos = self.sprite_pos[obstacle_type.type]

            self.obstacles.append(
                Obstacle(
                    self.ctx,
                    obstacle_type,
                    obstacle_sprite_pos,
                    self.dimensions,
                    self.gap_coefficient,
                    current_speed,
                    obstacle_type.width,
                    self.runner,
                )
            )

            self.obstacle_history.insert(0, obstacle_type.type)

            if len(self.obstacle_history) > 1:
                del self.obstacle_history[self.runner.config.max_obstacle_duplication :]

    def duplicate_obstacle_check(self, next_obstacle_type: str) -> bool:
        duplicate_count = 0

        for obstacle in self.obstacle_history:
            duplicate_count = duplicate_count + 1 if obstacle == next_obstacle_type else 0
        return duplicate_count >= self.runner.config.max_obstacle_duplication

    def reset(self) -> None:
        self.obstacles = []
        for line in self.horizon_lines:
            line.reset()
        self.night_mode.reset()

    def add_cloud(self) -> None:
        self.clouds.append(Cloud(self.ctx, self.sprite_pos["cloud"], self.dimensions.width, self.runner))

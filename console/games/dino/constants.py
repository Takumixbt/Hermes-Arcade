"""Configuration and sprite data, transcribed from Chromium's dino game.

Source: ``components/neterror/resources/dino_game/`` (``offline.ts``,
``trex.ts``, ``obstacle.ts``, ``offline_sprite_definitions.ts`` and friends).
Only the 1x ("ldpi") desktop configuration is kept: no HiDPI, no mobile, no
alt-game modes, no audio cues.
"""

from __future__ import annotations

from dataclasses import dataclass

FPS = 60
MS_PER_FRAME = 1000 / FPS

DEFAULT_WIDTH = 600
DEFAULT_HEIGHT = 150

# Runner config: defaultBaseConfig merged with normalModeConfig.
CLEAR_TIME = 3000
GAMEOVER_CLEAR_TIME = 1200
BOTTOM_PAD = 10
MAX_BLINK_COUNT = 3
MAX_CLOUDS = 6
MAX_OBSTACLE_LENGTH = 3
MAX_OBSTACLE_DUPLICATION = 2
MAX_GAP_COEFFICIENT = 1.5
INVERT_FADE_DURATION = 12000
SPEED = 6
ACCELERATION = 0.001
GAP_COEFFICIENT = 0.6
INVERT_DISTANCE = 700
MAX_SPEED = 13

# Horizon
BG_CLOUD_SPEED = 0.2
CLOUD_FREQUENCY = 0.5


@dataclass(frozen=True)
class SpritePosition:
    x: int
    y: int


# spriteDefinitionByType.original.ldpi
SPRITE = {
    "cactusLarge": SpritePosition(332, 2),
    "cactusSmall": SpritePosition(228, 2),
    "cloud": SpritePosition(86, 2),
    "horizon": SpritePosition(2, 54),
    "moon": SpritePosition(484, 2),
    "pterodactyl": SpritePosition(134, 2),
    "restart": SpritePosition(2, 68),
    "textSprite": SpritePosition(655, 2),
    "tRex": SpritePosition(848, 2),
    "star": SpritePosition(645, 2),
}

# lines: [{sourceX: 2, sourceY: 52, width: 600, height: 12, yPos: 127}]
HORIZON_LINE = {"sourceX": 2, "sourceY": 52, "width": 600, "height": 12, "yPos": 127}


@dataclass
class CollisionBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class ObstacleType:
    type: str
    width: int
    height: int
    y_pos: tuple
    multiple_speed: float
    min_gap: float
    min_speed: float
    collision_boxes: tuple
    speed_offset: float = 0.0
    num_frames: int = 0
    frame_rate: float = 0.0


# Obstacle table. The original also lists an alt-game 'collectable' last; with
# alt games disabled Horizon.addNewObstacle excludes it, so it is omitted here.
OBSTACLE_TYPES = (
    ObstacleType(
        type="cactusSmall",
        width=17,
        height=35,
        y_pos=(105,),
        multiple_speed=4,
        min_gap=120,
        min_speed=0,
        collision_boxes=((0, 7, 5, 27), (4, 0, 6, 34), (10, 4, 7, 14)),
    ),
    ObstacleType(
        type="cactusLarge",
        width=25,
        height=50,
        y_pos=(90,),
        multiple_speed=7,
        min_gap=120,
        min_speed=0,
        collision_boxes=((0, 12, 7, 38), (8, 0, 7, 49), (13, 10, 10, 38)),
    ),
    ObstacleType(
        type="pterodactyl",
        width=46,
        height=40,
        y_pos=(100, 75, 50),
        multiple_speed=999,
        min_gap=150,
        min_speed=8.5,
        collision_boxes=((15, 15, 16, 5), (18, 21, 24, 6), (2, 14, 4, 3), (6, 10, 4, 7), (10, 8, 6, 9)),
        speed_offset=0.8,
        num_frames=2,
        frame_rate=1000 / 6,
    ),
)

# Trex
TREX_COLLISION_DUCKING = ((1, 18, 55, 25),)
TREX_COLLISION_RUNNING = (
    (22, 0, 17, 16),
    (1, 18, 30, 9),
    (10, 35, 14, 8),
    (1, 24, 29, 5),
    (5, 30, 21, 4),
    (9, 34, 15, 4),
)
BLINK_TIMING = 7000


@dataclass
class TrexConfig:
    drop_velocity: float = -5
    flash_off: float = 175
    flash_on: float = 100
    height: int = 47
    height_duck: int = 25
    intro_duration: float = 1500
    speed_drop_coefficient: float = 3
    sprite_width: int = 262
    start_x_pos: int = 50
    width: int = 44
    width_duck: int = 59
    gravity: float = 0.6
    max_jump_height: float = 30
    min_jump_height: float = 30
    initial_jump_velocity: float = -10


# NightMode
NIGHT_PHASES = (140, 120, 100, 60, 40, 20, 0)
NIGHT_FADE_SPEED = 0.035
NIGHT_HEIGHT = 40
NIGHT_MOON_SPEED = 0.25
NIGHT_NUM_STARS = 2
NIGHT_STAR_SIZE = 9
NIGHT_STAR_SPEED = 0.3
NIGHT_STAR_MAX_Y = 70
NIGHT_WIDTH = 20

# Cloud
CLOUD_HEIGHT = 14
CLOUD_MAX_GAP = 400
CLOUD_MAX_SKY_LEVEL = 30
CLOUD_MIN_GAP = 100
CLOUD_MIN_SKY_LEVEL = 71
CLOUD_WIDTH = 46

# DistanceMeter
METER_WIDTH = 10
METER_HEIGHT = 13
METER_DEST_WIDTH = 11
METER_MAX_DISTANCE_UNITS = 5
METER_ACHIEVEMENT_DISTANCE = 100
METER_COEFFICIENT = 0.025
METER_FLASH_DURATION = 1000 / 4
METER_FLASH_ITERATIONS = 3

# GameOverPanel
RESTART_ANIM_DURATION = 875
LOGO_PAUSE_DURATION = 875
RESTART_FRAMES = (0, 36, 72, 108, 144, 180, 216, 252)
RESTART_MS_PER_FRAME = RESTART_ANIM_DURATION / 8
PANEL = {"textX": 0, "textY": 13, "textWidth": 191, "textHeight": 11, "restartWidth": 36, "restartHeight": 32}

# Input (keyCodes in offline.ts)
KEY_JUMP = (38, 32)
KEY_DUCK = (40,)
KEY_RESTART = (13,)

# CSS: `.offline .runner-container { width: 44px }` and the intro animation.
CONTAINER_INITIAL_WIDTH = 44
INTRO_ANIMATION_MS = 400
INVERT_TRANSITION_MS = 1500


@dataclass
class Dimensions:
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT


@dataclass
class RunnerConfig:
    clear_time: float = CLEAR_TIME
    gameover_clear_time: float = GAMEOVER_CLEAR_TIME
    bottom_pad: int = BOTTOM_PAD
    max_blink_count: int = MAX_BLINK_COUNT
    max_clouds: int = MAX_CLOUDS
    max_obstacle_length: int = MAX_OBSTACLE_LENGTH
    max_obstacle_duplication: int = MAX_OBSTACLE_DUPLICATION
    invert_fade_duration: float = INVERT_FADE_DURATION
    speed: float = SPEED
    acceleration: float = ACCELERATION
    gap_coefficient: float = GAP_COEFFICIENT
    invert_distance: int = INVERT_DISTANCE
    max_speed: float = MAX_SPEED
    speed_drop_coefficient: float = 3

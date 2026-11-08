"""Kart: a side-view race against the computer or a model-written controller.

The track scrolls right-to-left; you hold a lane and manage speed. Rivals are
simple lane-weaving racers with their own pace, boost pads and oil slicks change
the race, and a finished race is scored by distance and position.

Two ways to race:

* **Play** - up/down change lane, right accelerates, left brakes, space starts.
* **Bench** - ``simulate(policy)`` runs the same physics headless at 60 fps with a
  fixed seed, so a model can write ``def act(state) -> {"lane": ..., "throttle": ...}``
  and be scored deterministically against the same track.

The picture is drawn procedurally, so it needs no sprite art.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ...catalog import register
from ...gfx.canvas import Canvas
from ..base import Frame, Game

WIDTH, HEIGHT = 600, 150
FPS = 60.0
DT = 1.0 / FPS

ROAD_TOP, ROAD_BOTTOM = 96, 148
LANES = (104, 120, 136)
KART_W, KART_H = 32, 18

MAX_SPEED = 320.0        # px per second
ACCEL = 190.0
BRAKE = 340.0
DRAG = 26.0
RACE_METRES = 1500.0
PX_PER_METRE = 10.0

# palette indices used by the art (index -> colour)
SKY, SKY_LOW = 170, 171
HILL_FAR, HILL_NEAR = 182, 186
GRASS, GRASS_DARK = 95, 93
ROAD, ROAD_DARK = 45, 42
LINE, KERB_A, KERB_B = 245, 200, 246
PLAYER, PLAYER_DARK = 201, 202
RIVAL_A, RIVAL_A_DARK = 205, 206
RIVAL_B, RIVAL_B_DARK = 210, 211
RIVAL_C, RIVAL_C_DARK = 214, 215
BOOST, BOOST_DARK = 226, 227
OIL, OIL_EDGE = 30, 32
INK, INK_SOFT = 250, 120

PALETTE = [(g, g, g) for g in range(256)]
PALETTE[SKY] = (126, 196, 240)
PALETTE[SKY_LOW] = (176, 219, 245)
PALETTE[HILL_FAR] = (150, 186, 160)
PALETTE[HILL_NEAR] = (96, 150, 112)
PALETTE[GRASS] = (74, 132, 82)
PALETTE[GRASS_DARK] = (58, 112, 66)
PALETTE[ROAD] = (66, 68, 74)
PALETTE[ROAD_DARK] = (56, 58, 64)
PALETTE[LINE] = (238, 238, 232)
PALETTE[KERB_A] = (206, 74, 62)
PALETTE[KERB_B] = (242, 240, 234)
PALETTE[PLAYER] = (214, 72, 58)
PALETTE[PLAYER_DARK] = (150, 40, 32)
PALETTE[RIVAL_A] = (66, 120, 220)
PALETTE[RIVAL_A_DARK] = (40, 76, 160)
PALETTE[RIVAL_B] = (86, 190, 110)
PALETTE[RIVAL_B_DARK] = (44, 128, 70)
PALETTE[RIVAL_C] = (226, 190, 60)
PALETTE[RIVAL_C_DARK] = (160, 128, 34)
PALETTE[BOOST] = (250, 208, 60)
PALETTE[BOOST_DARK] = (196, 150, 28)
PALETTE[OIL] = (34, 34, 40)
PALETTE[OIL_EDGE] = (18, 18, 22)
PALETTE[INK] = (250, 250, 250)
PALETTE[INK_SOFT] = (176, 176, 176)
PALETTE[255] = (255, 255, 255)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class Racer:
    name: str
    dist: float = 0.0
    lane: float = 1.0          # 0..2, fractional while changing
    target_lane: int = 1
    speed: float = 0.0
    base_speed: float = 250.0
    body: int = RIVAL_A
    dark: int = RIVAL_A_DARK
    lane_timer: float = 0.0
    finished: bool = False
    finish_time: float = 0.0

    @property
    def x(self) -> float:
        return 90.0


@dataclass
class Pad:
    dist: float
    lane: int
    kind: str = "boost"        # boost | oil
    used: bool = False


@dataclass
class RaceState:
    phase: str = "countdown"   # countdown | racing | finished
    countdown: float = 3.2
    elapsed: float = 0.0
    scroll: float = 0.0
    distance: float = 0.0
    rank: int = 1
    message: str = ""
    boost_timer: float = 0.0
    oil_timer: float = 0.0


@register("kart")
class KartGame(Game):
    name = "kart"
    title = "Kart"
    width = WIDTH
    height = HEIGHT

    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.canvas = Canvas(_empty_sheet(), WIDTH, HEIGHT)
        self.player = Racer("You", lane=1.0, body=PLAYER, dark=PLAYER_DARK, base_speed=0.0)
        self.rivals = [
            Racer("Vex", lane=0.0, body=RIVAL_A, dark=RIVAL_A_DARK, base_speed=286.0),
            Racer("Nyx", lane=1.0, body=RIVAL_B, dark=RIVAL_B_DARK, base_speed=300.0),
            Racer("Orion", lane=2.0, body=RIVAL_C, dark=RIVAL_C_DARK, base_speed=278.0),
        ]
        self.state = RaceState()
        self.pads: list[Pad] = []
        self._build_track()
        self._last_ms: float | None = None
        self._pending_lane = 1
        self._throttle = 0.0
        self.roadside: list[tuple[float, int, int]] = [
            (self.rng.uniform(0, 900), self.rng.randint(0, 8), self.rng.choice((HILL_FAR, HILL_NEAR)))
            for _ in range(26)
        ]

    # -- track ----------------------------------------------------------
    def _build_track(self) -> None:
        self.pads = []
        step = 420.0
        d = 520.0
        i = 0
        while d < RACE_METRES * PX_PER_METRE - 400:
            self.pads.append(Pad(d, i % 3, "boost" if i % 2 == 0 else "oil"))
            d += step + (i % 3) * 90.0
            i += 1

    # -- input ----------------------------------------------------------
    def handle_key(self, key: str, pressed: bool, now_ms: float) -> None:
        if key == "space" and pressed:
            if self.state.phase in ("countdown", "finished"):
                self.reset()
            return
        if key in ("up", "down"):
            if pressed:
                if key == "up":
                    self._pending_lane = max(0, self._pending_lane - 1)
                else:
                    self._pending_lane = min(2, self._pending_lane + 1)
            return
        if key == "right":
            self._throttle = 1.0 if pressed else 0.0
        elif key == "left":
            self._throttle = -1.0 if pressed else 0.0
        elif not pressed and self._throttle != 0.0 and key in ("right", "left"):
            self._throttle = 0.0

    def reset(self) -> None:
        self.player.dist = 0.0
        self.player.speed = 0.0
        self.player.lane = 1.0
        self.player.target_lane = 1
        self.player.finished = False
        for i, r in enumerate(self.rivals):
            r.dist = -10.0 - i * 55.0
            r.speed = 0.0
            r.lane = float(i)
            r.target_lane = i
            r.finished = False
            r.base_speed = (286.0, 300.0, 312.0)[i]
        self.state = RaceState()
        self._pending_lane = 1
        self._throttle = 0.0
        self._build_track()

    # -- simulation -----------------------------------------------------
    def tick(self, now_ms: float) -> None:
        if self._last_ms is None:
            self._last_ms = now_ms
        dt = max(0.0, min(0.05, (now_ms - self._last_ms) / 1000.0))
        self._last_ms = now_ms
        self.step(dt)

    def step(self, dt: float) -> None:
        st = self.state
        if st.phase == "countdown":
            st.countdown -= dt
            if st.countdown <= 0:
                st.phase = "racing"
            return
        if st.phase == "finished":
            return

        st.elapsed += dt
        p = self.player

        # lane steering is eased so it reads as a kart, not a teleport
        p.target_lane = self._pending_lane
        p.lane += max(-3.2 * dt, min(3.2 * dt, p.target_lane - p.lane))

        if st.oil_timer > 0:
            st.oil_timer -= dt
            throttle = 0.0
        else:
            throttle = self._throttle
            if throttle > 0:
                p.speed += ACCEL * throttle * dt
            elif throttle < 0:
                p.speed += BRAKE * throttle * dt
        p.speed -= DRAG * dt
        if st.boost_timer > 0:
            st.boost_timer -= dt
            p.speed += 120.0 * dt
        p.speed = max(0.0, min(MAX_SPEED + (90.0 if st.boost_timer > 0 else 0.0), p.speed))
        p.dist += p.speed * dt
        st.scroll += p.speed * dt
        st.distance = p.dist / PX_PER_METRE

        self._step_rivals(dt, p)
        self._step_pads(p)
        self._rank()
        if p.dist >= RACE_METRES * PX_PER_METRE and not p.finished:
            p.finished = True
            p.finish_time = st.elapsed
            st.phase = "finished"
            st.message = f"Finish! {self._ordinal(st.rank)} place in {st.elapsed:.1f}s"

    def _step_rivals(self, dt: float, p: Racer) -> None:
        progress = p.dist / (RACE_METRES * PX_PER_METRE)
        for r in self.rivals:
            if r.finished:
                continue
            # rivals ease toward their pace, with a light rubber band so the race
            # stays close without ever feeling scripted
            gap = r.dist - p.dist
            rubber = 1.0 + max(-0.05, min(0.09, -gap / 2800.0))
            target = r.base_speed * rubber
            r.speed = _lerp(r.speed, target, min(1.0, dt * 1.4))
            r.lane_timer -= dt
            if r.lane_timer <= 0:
                r.lane_timer = self.rng.uniform(1.4, 3.4)
                choices = [0, 1, 2]
                # do not share a lane with another rival that is alongside
                for other in self.rivals:
                    if other is not r and abs(other.dist - r.dist) < 90:
                        near = int(round(other.lane))
                        if near in choices and len(choices) > 1:
                            choices.remove(near)
                r.target_lane = self.rng.choice(choices)
            r.lane += max(-1.6 * dt, min(1.6 * dt, r.target_lane - r.lane))
            r.dist += r.speed * dt
            if r.dist >= RACE_METRES * PX_PER_METRE and not r.finished:
                r.finished = True
                r.finish_time = self.state.elapsed
            # a rival in your lane and close costs both of you speed
            if abs(r.dist - p.dist) < 26 and abs(r.lane - p.lane) < 0.55:
                r.speed *= 0.985
                p.speed *= 0.99

    def _step_pads(self, p: Racer) -> None:
        for pad in self.pads:
            if pad.used or abs(pad.dist - p.dist) > 14:
                continue
            if abs(pad.lane - p.lane) > 0.5:
                continue
            pad.used = True
            if pad.kind == "boost":
                self.state.boost_timer = 1.1
            else:
                self.state.oil_timer = 0.9
                p.speed *= 0.55

    def _rank(self) -> None:
        order = sorted([self.player, *self.rivals], key=lambda r: r.dist, reverse=True)
        self.state.rank = order.index(self.player) + 1

    @staticmethod
    def _ordinal(n: int) -> str:
        return {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(n, f"{n}th")

    # -- drawing --------------------------------------------------------
    def frame(self, now_ms: float) -> Frame:
        c = self.canvas
        c.global_alpha = 1.0
        self._sky(c)
        self._road(c)
        self._scenery(c)
        self._pads(c)
        self._racers(c)
        self._hud(c)
        return Frame(WIDTH, HEIGHT, bytes(c.plane), PALETTE, 255)

    def _band(self, c: Canvas, y0: int, y1: int, colour: int) -> None:
        c.fill_style = f"#{colour:02x}0000"
        c.begin_path()
        c.rect(0, y0, WIDTH, y1 - y0)
        c.fill()

    def _sky(self, c: Canvas) -> None:
        self._band(c, 0, 58, SKY)
        self._band(c, 58, ROAD_TOP - 16, SKY_LOW)
        # clouds drift slower than the road
        for i in range(8):
            x = ((i * 113) - (self.state.scroll * 0.06) % 113) - 60
            y = 8 + (i % 3) * 11
            self._band_rect(c, x, y, 26, 4, 252)
            self._band_rect(c, x + 5, y - 3, 16, 4, 252)
            self._band_rect(c, x + 10, y + 4, 10, 2, 252)
        # stepped hills read as landscape, not as boxes
        for i in range(0, 10):
            x = ((i * 104) - (self.state.scroll * 0.18) % 104) - 50
            self._hill(c, x, ROAD_TOP - 16, 96, 26 + (i * 7) % 17, HILL_FAR)
        for i in range(0, 14):
            x = ((i * 74) - (self.state.scroll * 0.34) % 74) - 40
            self._hill(c, x, ROAD_TOP - 16, 64, 16 + (i * 5) % 12, HILL_NEAR)
        self._band(c, ROAD_TOP - 16, ROAD_TOP, GRASS)

    def _hill(self, c: Canvas, x: float, base_y: int, w: int, h: int, colour: int, steps: int = 5) -> None:
        sh = max(1, h // steps)
        for i in range(steps):
            sw = max(2, int(w * (i + 1) / steps))
            self._band_rect(c, x + (w - sw) // 2, base_y - (i + 1) * sh, sw, sh, colour)

    def _road(self, c: Canvas) -> None:
        self._band(c, ROAD_TOP, ROAD_BOTTOM, ROAD)
        self._band(c, ROAD_BOTTOM, ROAD_BOTTOM + 2, GRASS_DARK)
        # lane dividers scroll with the player's speed
        off = int(self.state.scroll) % 40
        for y in (LANES[0] + 8, LANES[1] + 8):
            x = -off
            while x < WIDTH:
                self._band_rect(c, x, y, 22, 2, LINE)
                x += 40
        # kerbs: alternating red/white blocks on both edges
        x = -off
        i = 0
        while x < WIDTH:
            colour = KERB_A if i % 2 == 0 else KERB_B
            self._band_rect(c, x, ROAD_TOP - 3, 20, 3, colour)
            self._band_rect(c, x, ROAD_BOTTOM, 20, 3, colour)
            x += 20
            i += 1
        # speed streaks sell motion above 150 px/s
        if self.player.speed > 150:
            n = int(min(12, (self.player.speed - 150) / 14))
            for k in range(n):
                sx = ((k * 97) - self.state.scroll * 1.8) % 121 - 20
                sy = LANES[k % 3] + 3 + (k % 2) * 4
                self._band_rect(c, sx, sy, 14, 2, LINE)

    def _scenery(self, c: Canvas) -> None:
        off = (self.state.scroll * 0.9) % 900
        for dist, size, colour in self.roadside:
            x = dist - off
            if -40 < x < WIDTH + 40:
                y = ROAD_TOP - 10 - size
                self._band_rect(c, x, y, 3, size + 10, colour)
                self._band_rect(c, x - 5, y, 13, 5, colour)

    def _pads(self, c: Canvas) -> None:
        for pad in self.pads:
            if pad.used:
                continue
            dx = 90 + (pad.dist - self.player.dist)
            if dx < -40 or dx > WIDTH + 40:
                continue
            y = LANES[pad.lane] + 3
            if pad.kind == "boost":
                self._band_rect(c, dx, y, 18, 6, BOOST)
                self._band_rect(c, dx + 3, y + 2, 12, 2, BOOST_DARK)
            else:
                self._band_rect(c, dx, y, 16, 5, OIL)
                self._band_rect(c, dx + 2, y + 1, 12, 3, OIL_EDGE)

    def _racers(self, c: Canvas) -> None:
        drawables = []
        for r in self.rivals:
            dx = 90 + (r.dist - self.player.dist)
            if -40 < dx < WIDTH + 40:
                drawables.append((r.dist, dx, r.lane, r.body, r.dark))
        drawables.append((self.player.dist, 90.0, self.player.lane, self.player.body, self.player.dark))
        drawables.sort(key=lambda d: d[0])
        for _, dx, lane, body, dark in drawables:
            self._kart(c, dx, LANES[0] + (LANES[2] - LANES[0]) * (lane / 2.0), body, dark)

    def _kart(self, c: Canvas, x: float, y: float, body: int, dark: int) -> None:
        y = y - KART_H // 2
        spin = int(self.state.elapsed * 24) % 2
        self._band_rect(c, x + 1, y + KART_H - 1, KART_W - 2, 3, OIL_EDGE)  # ground shadow
        self._band_rect(c, x + 4, y + 11, 9, 7, OIL)                       # rear wheel
        self._band_rect(c, x + 22, y + 11, 9, 7, OIL)                      # front wheel
        self._band_rect(c, x + 5, y + 12 + spin, 7, 1, INK_SOFT)           # wheel spin
        self._band_rect(c, x + 23, y + 12 + spin, 7, 1, INK_SOFT)
        self._band_rect(c, x, y + 5, KART_W, 9, body)                      # chassis
        self._band_rect(c, x + 2, y + 7, 9, 2, dark)                       # side stripe
        self._band_rect(c, x + 8, y + 1, 15, 7, dark)                      # cockpit
        self._band_rect(c, x + 13, y - 1, 6, 3, INK)                       # helmet
        self._band_rect(c, x + 29, y + 2, 3, 5, dark)                      # spoiler
        self._band_rect(c, x - 3, y + 7, 4, 5, dark)                       # nose cone
        self._band_rect(c, x - 1, y + 8, 2, 3, BOOST)                      # headlight
        if body == PLAYER and self._throttle > 0 and self.state.phase == "racing":
            puff = int(self.state.elapsed * 18) % 4
            self._band_rect(c, x - 8 - puff, y + 11, 4, 2, INK_SOFT)       # exhaust
            self._band_rect(c, x - 13 - puff, y + 12, 3, 2, GRASS_DARK)

    def _hud(self, c: Canvas) -> None:
        st = self.state
        # dark panels keep the tiny 3x5 type readable against sky and road
        c.global_alpha = 0.72
        self._band_rect(c, 4, 4, 108, 28, OIL)
        if st.boost_timer > 0 or st.oil_timer > 0:
            self._band_rect(c, WIDTH - 84, 4, 80, 14, OIL)
        self._band_rect(c, 4, HEIGHT - 18, 96, 14, OIL)
        c.global_alpha = 1.0
        self._text(c, 8, 8, f"{st.distance:5.0f} m", INK)
        self._text(c, 8, 18, f"{self._ordinal(st.rank)} of 4", INK_SOFT)
        # speed bar inside the bottom panel
        frac = min(1.0, self.player.speed / (MAX_SPEED + 90))
        self._band_rect(c, 40, HEIGHT - 13, 56, 5, ROAD_DARK)
        self._band_rect(c, 40, HEIGHT - 13, int(56 * frac), 5, BOOST if st.boost_timer > 0 else PLAYER)
        for tick in (14, 28, 42):
            self._band_rect(c, 40 + tick, HEIGHT - 13, 1, 5, OIL_EDGE)
        self._text(c, 8, HEIGHT - 13, "SPD", INK_SOFT)
        if st.phase == "countdown":
            msg = "3" if st.countdown > 2.2 else "2" if st.countdown > 1.2 else "1" if st.countdown > 0.2 else "GO"
            self._band_rect(c, WIDTH // 2 - 12, 30, 24, 18, OIL)
            self._text(c, WIDTH // 2 - 4, 34, msg, INK)
        elif st.phase == "racing" and st.elapsed < 1.0:
            self._band_rect(c, WIDTH // 2 - 14, 30, 28, 16, OIL)
            self._text(c, WIDTH // 2 - 8, 34, "GO!", INK)
        elif st.phase == "finished":
            self._band_rect(c, WIDTH // 2 - 104, 26, 208, 30, OIL)
            self._text(c, WIDTH // 2 - 96, 32, st.message, INK)
            self._text(c, WIDTH // 2 - 62, 44, "space to race again", INK_SOFT)
        if st.oil_timer > 0:
            self._text(c, WIDTH - 78, 8, "OIL!", KERB_A)
        elif st.boost_timer > 0:
            self._text(c, WIDTH - 78, 8, "BOOST", BOOST)

    def _band_rect(self, c: Canvas, x: float, y: float, w: float, h: float, colour: int) -> None:
        c.fill_style = f"#{colour:02x}0000"
        c.begin_path()
        c.rect(x, y, w, h)
        c.fill()

    _GLYPHS = {
        "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
        "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
        "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
        "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "010", "010", "010"),
        "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
        "A": ("010", "101", "111", "101", "101"), "B": ("110", "101", "110", "101", "110"),
        "C": ("011", "100", "100", "100", "011"), "D": ("110", "101", "101", "101", "110"),
        "E": ("111", "100", "110", "100", "111"), "F": ("111", "100", "110", "100", "100"),
        "G": ("011", "100", "101", "101", "011"), "H": ("101", "101", "111", "101", "101"),
        "I": ("111", "010", "010", "010", "111"), "J": ("001", "001", "001", "101", "010"),
        "K": ("101", "101", "110", "101", "101"), "L": ("100", "100", "100", "100", "111"),
        "M": ("101", "111", "111", "101", "101"), "N": ("101", "111", "111", "111", "101"),
        "O": ("010", "101", "101", "101", "010"), "P": ("110", "101", "110", "100", "100"),
        "Q": ("010", "101", "101", "111", "011"), "R": ("110", "101", "110", "101", "101"),
        "S": ("011", "100", "010", "001", "110"), "T": ("111", "010", "010", "010", "010"),
        "U": ("101", "101", "101", "101", "111"), "V": ("101", "101", "101", "101", "010"),
        "W": ("101", "101", "111", "111", "101"), "X": ("101", "101", "010", "101", "101"),
        "Y": ("101", "101", "010", "010", "010"), "Z": ("111", "001", "010", "100", "111"),
        " ": ("000", "000", "000", "000", "000"), "!": ("010", "010", "010", "000", "010"),
        "?": ("110", "001", "010", "000", "010"), "m": ("000", "000", "101", "111", "101"),
        ".": ("000", "000", "000", "000", "010"), "-": ("000", "000", "111", "000", "000"),
        ",": ("000", "000", "000", "010", "100"), ":": ("000", "010", "000", "010", "000"),
        "o": ("000", "010", "101", "101", "010"), " ": ("000", "000", "000", "000", "000"),
    }

    def _text(self, c: Canvas, x: int, y: int, text: str, colour: int) -> None:
        """3x5 bitmap text, one pixel per cell: no font anywhere in the project."""
        cx = x
        for ch in text:
            glyph = self._GLYPHS.get(ch) or self._GLYPHS.get(ch.upper()) or self._GLYPHS["?"]
            c.fill_style = f"#{colour:02x}0000"
            c.begin_path()
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        c.rect(cx + gx, y + gy, 1, 1)
            c.fill()
            cx += 4

    # -- headless benchmark --------------------------------------------
    def simulate(self, policy, seconds: float = 60.0, seed: int = 7) -> dict:
        """Run a race with a model-written controller and score it.

        ``policy(state) -> {"lane": 0..2, "throttle": -1..1}``. The same physics,
        seed and track as a played race, so two models can be compared honestly.
        """
        self.rng = random.Random(seed)
        self.reset()
        self.state.countdown = 0.0
        self.state.phase = "racing"
        steps = int(seconds / DT)
        errors = 0
        for i in range(steps):
            view = {
                "distance": self.player.dist / PX_PER_METRE,
                "speed": self.player.speed,
                "lane": self.player.lane,
                "rank": self.state.rank,
                "next_pad": self._next_pad(),
                "rivals": [(r.dist / PX_PER_METRE, r.lane) for r in self.rivals],
                "t": i * DT,
            }
            try:
                action = policy(view) or {}
            except Exception:
                errors += 1
                action = {}
            lane = int(action.get("lane", 1))
            self._pending_lane = max(0, min(2, lane))
            self._throttle = max(-1.0, min(1.0, float(action.get("throttle", 1.0))))
            self.step(DT)
            if self.state.phase == "finished":
                break
        return {
            "finished": self.player.finished,
            "distance_m": round(self.player.dist / PX_PER_METRE, 1),
            "place": self.state.rank,
            "time_s": round(self.state.elapsed, 2),
            "speed_avg": round(self.player.dist / max(0.001, self.state.elapsed) / PX_PER_METRE, 1),
            "policy_errors": errors,
        }

    def _next_pad(self):
        ahead = [p for p in self.pads if not p.used and p.dist > self.player.dist]
        if not ahead:
            return None
        pad = min(ahead, key=lambda p: p.dist)
        return (pad.kind, pad.lane, round((pad.dist - self.player.dist) / PX_PER_METRE, 1))

    def hint(self) -> str:
        st = self.state
        if st.phase == "countdown":
            return "Up/Down lane  -  Right accelerate  -  Left brake  -  Space starts"
        return "Up/Down lane  -  Right accelerate  -  Left brake  -  Esc returns to Hermes"

    def text_hud(self) -> str:
        """Readable state for the half-block fallback, where the pixel HUD is mush."""
        st = self.state
        bits = [f"{st.distance:.0f} m", f"{self._ordinal(st.rank)} of 4",
                f"{int(self.player.speed * 0.36)} km/h"]
        if st.boost_timer > 0:
            bits.append("BOOST")
        elif st.oil_timer > 0:
            bits.append("OIL")
        if st.phase == "finished":
            bits.append("space to race again")
        return "  |  ".join(bits)


def _empty_sheet():
    from ...gfx.canvas import SpriteSheet
    return SpriteSheet(1, 1, b"\x00\x00\x00\x00")

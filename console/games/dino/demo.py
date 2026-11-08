"""A simple autopilot so ``console dino --demo`` can play itself.

It exists for screenshots, the README recording and smoke tests; it reads the
game state the way a player reads the screen and presses the same keys.
"""

from __future__ import annotations

from .game import DinoGame


class DemoDino(DinoGame):
    name = "dino"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._release: list[tuple[float, str]] = []
        self._duck_until = 0.0
        self._crash_at: float | None = None
        self._started_at: float | None = None

    @classmethod
    def create(cls) -> "DemoDino":
        return cls(seed=2024)

    def hint(self) -> str:
        return "Demo: the dino is playing itself    Esc to leave"

    def tick(self, now_ms: float) -> None:
        super().tick(now_ms)
        r = self.runner
        for due, key in [x for x in self._release if x[0] <= now_ms]:
            self.handle_key(key, False, now_ms)
        self._release = [x for x in self._release if x[0] > now_ms]

        if self._started_at is None:
            self._started_at = now_ms
        if not r.playing and not r.crashed and not r.activated:
            if now_ms - self._started_at > 1200:
                self.handle_key("space", True, now_ms)
                self._release.append((now_ms + 60, "space"))
            return
        if r.crashed:
            if self._crash_at is None:
                self._crash_at = now_ms
            elif now_ms - self._crash_at > 1700:
                self.handle_key("enter", True, now_ms)
                self.handle_key("enter", False, now_ms)
                self._crash_at = None
            return
        self._crash_at = None
        if not r.playing:
            return
        t = r.t_rex
        obstacles = r.horizon.obstacles
        if not obstacles:
            return
        ob = obstacles[0]
        dx = ob.x_pos - (t.x_pos + 44)
        ptero = ob.type_config.type == "pterodactyl"
        if t.jumping or t.ducking:
            return
        if not ptero or ob.y_pos == 100:
            if -20 < dx < 20 + r.current_speed * (9 if ptero else 7.5):
                self.handle_key("space", True, now_ms)
                self._release.append((now_ms + 1000, "space"))
        elif ob.y_pos == 75 and -50 < dx < 30 + r.current_speed * 12 and now_ms > self._duck_until:
            self.handle_key("down", True, now_ms)
            self._duck_until = now_ms + 650
            self._release.append((now_ms + 650, "down"))

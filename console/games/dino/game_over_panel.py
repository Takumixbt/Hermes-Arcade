"""GAME OVER text and the animated restart button. Port of ``game_over_panel.ts``."""

from __future__ import annotations

from .constants import LOGO_PAUSE_DURATION, PANEL, RESTART_FRAMES, RESTART_MS_PER_FRAME, Dimensions, SpritePosition
from .jsrt import js_round

_NAN = float("nan")


class GameOverPanel:
    def __init__(self, ctx, text_img_pos: SpritePosition, restart_img_pos: SpritePosition, dimensions: Dimensions, runner) -> None:
        self.ctx = ctx
        self.sched = runner.sched
        self.canvas_dimensions = dimensions
        self.text_img_pos = text_img_pos
        self.restart_img_pos = restart_img_pos

        self.frame_time_stamp = 0.0
        self.anim_timer = 0.0
        self.current_frame = 0
        self.game_over_raf_id: int | None = None

    def update_dimensions(self, width: int, height: int | None = None) -> None:
        self.canvas_dimensions.width = width
        if height:
            self.canvas_dimensions.height = height
        self.current_frame = len(RESTART_FRAMES) - 1

    def _draw_game_over_text(self) -> None:
        d = PANEL
        center_x = self.canvas_dimensions.width / 2
        text_source_x = d["textX"] + self.text_img_pos.x
        text_source_y = d["textY"] + self.text_img_pos.y
        text_target_x = js_round(center_x - (d["textWidth"] / 2))
        text_target_y = js_round((self.canvas_dimensions.height - 25) / 3)

        self.ctx.save()
        self.ctx.draw_image(
            text_source_x, text_source_y, d["textWidth"], d["textHeight"], text_target_x, text_target_y, d["textWidth"], d["textHeight"]
        )
        self.ctx.restore()

    def _draw_restart_button(self) -> None:
        d = PANEL
        # frames[currentFrame] is undefined one past the end, which makes
        # drawImage receive NaN and paint nothing; keep that behaviour.
        frame_pos_x = RESTART_FRAMES[self.current_frame] if self.current_frame < len(RESTART_FRAMES) else _NAN
        restart_target_x = (self.canvas_dimensions.width / 2) - (d["restartHeight"] / 2)
        restart_target_y = self.canvas_dimensions.height / 2

        self.ctx.save()
        self.ctx.draw_image(
            self.restart_img_pos.x + frame_pos_x,
            self.restart_img_pos.y,
            d["restartWidth"],
            d["restartHeight"],
            restart_target_x,
            restart_target_y,
            d["restartWidth"],
            d["restartHeight"],
        )
        self.ctx.restore()

    def draw(self) -> None:
        self._draw_game_over_text()
        self._draw_restart_button()
        self.update()

    def update(self, _now: float | None = None) -> None:
        now = self.sched.get_time_stamp()
        delta_time = now - (self.frame_time_stamp or now)
        self.frame_time_stamp = now
        self.anim_timer += delta_time

        # Restart button.
        if self.current_frame == 0 and self.anim_timer > LOGO_PAUSE_DURATION:
            self.anim_timer = 0
            self.current_frame += 1
            self._draw_restart_button()
        elif 0 < self.current_frame < len(RESTART_FRAMES):
            if self.anim_timer >= RESTART_MS_PER_FRAME:
                self.current_frame += 1
                self._draw_restart_button()
        elif self.current_frame == len(RESTART_FRAMES):
            self.reset()
            return

        self.game_over_raf_id = self.sched.request_animation_frame(self.update)

    def reset(self) -> None:
        if self.game_over_raf_id:
            self.sched.cancel_animation_frame(self.game_over_raf_id)
            self.game_over_raf_id = None
        self.anim_timer = 0
        self.frame_time_stamp = 0
        self.current_frame = 0

"""Collision detection, ported from the bottom of Chromium's ``offline.ts``."""

from __future__ import annotations

from .constants import CollisionBox


def create_adjusted_collision_box(box: CollisionBox, adjustment: CollisionBox) -> CollisionBox:
    return CollisionBox(box.x + adjustment.x, box.y + adjustment.y, box.width, box.height)


def box_compare(t_rex_box: CollisionBox, obstacle_box: CollisionBox) -> bool:
    return (
        t_rex_box.x < obstacle_box.x + obstacle_box.width
        and t_rex_box.x + t_rex_box.width > obstacle_box.x
        and t_rex_box.y < obstacle_box.y + obstacle_box.height
        and t_rex_box.height + t_rex_box.y > obstacle_box.y
    )


def check_for_collision(obstacle, t_rex):
    """Return the two crashed boxes, or ``None``. Mirrors Runner.checkForCollision."""
    t_rex_box = CollisionBox(t_rex.x_pos + 1, t_rex.y_pos + 1, t_rex.config.width - 2, t_rex.config.height - 2)
    obstacle_box = CollisionBox(
        obstacle.x_pos + 1,
        obstacle.y_pos + 1,
        obstacle.type_config.width * obstacle.size - 2,
        obstacle.type_config.height - 2,
    )
    if box_compare(t_rex_box, obstacle_box):
        for t_rex_collision_box in t_rex.get_collision_boxes():
            for obstacle_collision_box in obstacle.collision_boxes:
                adj_t_rex_box = create_adjusted_collision_box(t_rex_collision_box, t_rex_box)
                adj_obstacle_box = create_adjusted_collision_box(obstacle_collision_box, obstacle_box)
                if box_compare(adj_t_rex_box, adj_obstacle_box):
                    return [adj_t_rex_box, adj_obstacle_box]
    return None

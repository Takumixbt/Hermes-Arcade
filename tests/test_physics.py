from types import SimpleNamespace

from console.games.dino.constants import OBSTACLE_TYPES, CollisionBox
from console.games.dino.physics import box_compare, check_for_collision, create_adjusted_collision_box

CACTUS = OBSTACLE_TYPES[0]


def trex(x=50, y=93, ducking=False):
    boxes = [CollisionBox(1, 18, 55, 25)] if ducking else [CollisionBox(22, 0, 17, 16), CollisionBox(1, 18, 30, 9)]
    return SimpleNamespace(x_pos=x, y_pos=y, config=SimpleNamespace(width=44, height=47), get_collision_boxes=lambda: boxes)


def cactus(x, y=105, size=1):
    return SimpleNamespace(
        x_pos=x,
        y_pos=y,
        size=size,
        type_config=CACTUS,
        collision_boxes=[CollisionBox(*b) for b in CACTUS.collision_boxes],
    )


def test_boxes_overlap_only_when_they_intersect():
    a = CollisionBox(0, 0, 10, 10)
    assert box_compare(a, CollisionBox(5, 5, 10, 10))
    assert not box_compare(a, CollisionBox(10, 0, 5, 5))  # touching edges do not count
    assert not box_compare(a, CollisionBox(0, 10, 5, 5))


def test_adjusted_box_is_offset_by_its_owner():
    adj = create_adjusted_collision_box(CollisionBox(3, 4, 5, 6), CollisionBox(10, 20, 99, 99))
    assert (adj.x, adj.y, adj.width, adj.height) == (13, 24, 5, 6)


def test_running_into_a_cactus_crashes():
    assert check_for_collision(cactus(60), trex()) is not None


def test_far_cactus_is_safe():
    assert check_for_collision(cactus(300), trex()) is None


def test_jumping_clears_a_small_cactus():
    assert check_for_collision(cactus(60), trex(y=30)) is None


def pterodactyl(x, y):
    ptero = OBSTACLE_TYPES[2]
    return SimpleNamespace(
        x_pos=x,
        y_pos=y,
        size=1,
        type_config=ptero,
        collision_boxes=[CollisionBox(*b) for b in ptero.collision_boxes],
    )


def test_ducking_goes_under_a_mid_height_pterodactyl():
    assert check_for_collision(pterodactyl(60, 75), trex(ducking=False)) is not None
    assert check_for_collision(pterodactyl(60, 75), trex(ducking=True)) is None

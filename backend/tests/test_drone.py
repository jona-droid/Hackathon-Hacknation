import numpy as np

from backend.tests.conftest import fly


def test_braking_distance_from_full_speed(calm_drone):
    fly(calm_drone, {" "}, 4.0)
    fly(calm_drone, {"w"}, 4.0)
    start = calm_drone.pos.copy()
    fly(calm_drone, set(), 4.0)
    assert np.linalg.norm(calm_drone.vel) < 0.3
    assert 8.0 < np.linalg.norm(calm_drone.pos - start) < 14.0


def test_gps_hold_keeps_position_in_still_air(calm_drone):
    fly(calm_drone, {" "}, 3.0)
    fly(calm_drone, set(), 2.0)
    held = calm_drone.pos.copy()
    fly(calm_drone, set(), 5.0)
    assert np.linalg.norm(calm_drone.pos - held) < 0.2


def test_flying_into_a_cable_is_a_crash(calm_drone):
    calm_drone.pos = np.array([30.0, -3.0, 15.0])
    fly(calm_drone, {" "}, 5.0)
    assert calm_drone.collided and calm_drone.collision_with == "cable"

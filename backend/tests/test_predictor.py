import math

import numpy as np
import pytest

from backend.core.config import ROAD_MIN_CROSSING_ALT_M
from backend.sim.drone import DroneSim
from backend.sim.predictor import PredictiveMonitor, TrajectoryPredictor
from backend.tests.conftest import DT, fly


def test_predicted_path_matches_the_simulator(calm_drone, scene):
    fly(calm_drone, {" "}, 4.0)
    fly(calm_drone, {"w"}, 2.0)
    calm_drone.set_keys({"w"})
    path = np.array(TrajectoryPredictor(scene).predict(calm_drone).path)
    real = []
    for _ in range(12):
        fly(calm_drone, {"w"}, 0.25)
        real.append(calm_drone.pos.copy())
    assert np.linalg.norm(path[12] - real[-1]) < 0.3  # 3 s ahead, ~35 m flown


def test_predicted_stopping_distance(calm_drone, scene):
    fly(calm_drone, {" "}, 4.0)
    fly(calm_drone, {"w"}, 4.0)
    calm_drone.set_keys(set())
    predicted = TrajectoryPredictor(scene).predict(calm_drone).stop_dist
    start, travelled = calm_drone.pos.copy(), 0.0
    while np.linalg.norm(calm_drone.vel) >= 0.3:
        prev = calm_drone.pos.copy()
        fly(calm_drone, set(), DT)
        travelled += float(np.linalg.norm(calm_drone.pos - prev))
    assert abs(predicted - travelled) < 1.5


def _scenario(scene, pos, yaw, keys, seconds, guardian):
    d = DroneSim(scene)
    d.reset()
    d.wind.mean_speed = 0.0
    d.pos, d.yaw, d.hold_z = np.array(pos, dtype=float), yaw, pos[2]
    pr, mon, events, tick = TrajectoryPredictor(scene), PredictiveMonitor(), [], 0
    while d.elapsed_time < seconds and not d.collided:
        d.set_keys(keys)
        if tick % 6 == 0:
            pred = pr.predict(d)
            events += mon.update(pred, {"speed": float(np.linalg.norm(d.vel)), "road_dist": float(max(0, 82 - d.pos[0], d.pos[0] - 90))})
        if guardian:
            pr.apply_guardian(d)
        d.step(DT, wind_enabled=False)
        tick += 1
    return d, events


@pytest.mark.parametrize("name,pos,yaw,keys", [
    ("tower", (30, 0, 15), 0.0, {"w"}),
    ("cable from below", (30, -3, 12), 0.0, {" "}),
    ("tree", (30, -3.5, 8), math.pi / 2, {"w"}),
])
def test_guardian_prevents_the_crash(scene, name, pos, yaw, keys):
    crashed, events = _scenario(scene, pos, yaw, keys, 6.0, guardian=False)
    assert crashed.collided, f"{name}: the scenario must crash without the Guardian"
    assert any(e["type"] == "conflict_predicted" for e in events), "the conflict is predicted before the crash"
    safe, events = _scenario(scene, pos, yaw, keys, 6.0, guardian=True)
    assert not safe.collided, f"{name}: the Guardian must prevent the crash"
    assert any(e["type"] == "guardian_engaged" for e in events)


def test_conflict_advice_is_to_brake_not_to_slow_down(scene):
    _, events = _scenario(scene, (30, 0, 15), 0.0, {"w"}, 3.0, guardian=False)
    conflict = next(e for e in events if e["type"] == "conflict_predicted")
    assert conflict["hazard"] == "tower" and conflict["action"] == "brake"


def test_low_road_crossing_is_announced_once_with_a_climb(scene):
    _, events = _scenario(scene, (55, -15, 10), 0.0, {"w"}, 5.0, guardian=False)
    road = [e for e in events if e["type"] == "road_ahead"]
    assert len(road) == 1
    assert road[0]["low"] and road[0]["alt"] < ROAD_MIN_CROSSING_ALT_M
    assert road[0]["action"] in ("climb", "brake_climb")

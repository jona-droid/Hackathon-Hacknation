import json

import numpy as np

from backend.core.config import ROAD_MIN_CROSSING_ALT_M
from backend.sim.scene import CABLE_HIT_DIST, DRONE_RADIUS, clearances, collision, hazard_distances, scene_json


def _near_hazards(scene, n=150, seed=3):
    rng = np.random.default_rng(seed)
    centres = [(px, y, z) for px in scene.pylon_x for y in (-4, 0, 3, -3) for z in (5, 15, 25, 27, 29, 30)]
    centres += [(tx, ty, z) for tx, ty, _, th in scene.trees for z in (3, th, th + 2, th + 5)]
    centres += [(x, cy, 23) for x in (10, 30, 70, 100) for cy in scene.cable_y]
    return np.concatenate([np.array(c) + rng.normal(0, 1.2, (n, 3)) for c in centres])


def test_vectorised_geometry_matches_collision(scene):
    pts = _near_hazards(scene)
    h = hazard_distances(pts, scene)
    crashes = 0
    for i, p in enumerate(pts):
        real = collision(p, scene)
        predicted = h["cable"][i] < CABLE_HIT_DIST or h["tree"][i] < DRONE_RADIUS or h["tower"][i] < DRONE_RADIUS
        assert (real is not None) == bool(predicted), (p, real, {k: float(v[i]) for k, v in h.items()})
        crashes += real is not None
    assert crashes > 500  # the sample really probes the hazards


def test_cable_distance_matches_scalar_clearances(scene):
    for p in ([10.0, -3.5, 22.0], [65.0, 7.0, 18.0], [-5.0, -10.0, 4.0]):
        assert abs(float(hazard_distances(np.array([p]), scene)["cable"][0]) - clearances(np.array(p), scene)["cable_dist"]) < 1e-9


def test_road_and_trees(scene):
    c = clearances(np.array([86.0, -9.0, 15.0]), scene)
    assert c["road_dist"] == 0.0 and c["over_road"] is True
    assert len(scene.trees) == 3
    assert collision(np.array([52.0, -10.0, 4.0]), scene) == "tree"


def test_scene_json_is_serialisable_and_shares_the_safety_margins(scene):
    data = json.loads(json.dumps(scene_json(scene)))
    assert len(data["trees"]) == 3
    assert data["safety"]["road_min_crossing_alt_m"] == ROAD_MIN_CROSSING_ALT_M

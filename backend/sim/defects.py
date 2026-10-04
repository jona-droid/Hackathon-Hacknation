"""Inspection defects on the insulators and conductors, placed at random for each flight.

A defect counts as spotted when the pilot keeps it in the camera view, close and slow,
for a moment: the same thing a real inspector does before reporting it.
"""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np

from backend.sim.scene import CROSSARM_ABOVE_CABLE, Scene, cable_height_at

INSULATOR_DEFECTS = {
    "broken_disc": "Shattered disc in the insulator string",
    "flashover": "Flashover burn marks (soot) on the insulator discs",
    "pollution": "Heavy pollution deposit coating the insulator discs",
    "bird_nest": "Bird nest on the crossarm next to the insulator string",
}
CABLE_DEFECTS = {
    "broken_strands": "Broken outer strands (birdcaging) on the conductor",
    "foreign_object": "Kite tangled on the conductor",
}
INSULATOR_DISCS = 9  # keep in sync with frontend/src/Scene3D.tsx

SPOT_RANGE_M = {"insulator": 4.5, "cable": 6.0}
SPOT_MAX_SPEED = 1.5
SPOT_DWELL_S = 1.0
# Camera: 80 deg vertical FOV tilted 5 deg down, ~110 deg horizontal on a wide screen
VIEW_HALF_H_DEG = 50.0
VIEW_ELEVATION_DEG = (-45.0, 35.0)


def generate_defects(scene: Scene, rng: random.Random | None = None) -> list[dict[str, Any]]:
    rng = rng or random.Random()
    defects: list[dict[str, Any]] = []

    for ins in rng.sample(scene.insulators, rng.randint(2, 3)):
        kind = rng.choice(list(INSULATOR_DEFECTS))
        x, y, z = ins["pos"]
        pos = [x, y, z + CROSSARM_ABOVE_CABLE + 0.3] if kind == "bird_nest" else [x, y, z + 0.9]
        defects.append({
            "kind": kind,
            "label": INSULATOR_DEFECTS[kind],
            "component": "insulator",
            "target": ins["id"],
            "pos": pos,
            "disc": rng.randint(1, INSULATOR_DISCS - 3),  # first damaged disc (from the bottom)
        })

    spans = list(zip(scene.pylon_x[:-1], scene.pylon_x[1:]))
    for _ in range(rng.randint(1, 2)):
        span = rng.randrange(len(spans))
        x0, x1 = spans[span]
        x = rng.uniform(x0 + 10.0, x1 - 10.0)
        y = rng.choice(scene.cable_y)
        kind = rng.choice(list(CABLE_DEFECTS))
        defects.append({
            "kind": kind,
            "label": CABLE_DEFECTS[kind],
            "component": "cable",
            "target": f"{'north' if y > 0 else 'south'} cable, span between pylons {span + 1} and {span + 2}",
            "pos": [round(x, 2), y, round(cable_height_at(x, x0, x1, scene.pylon_height, scene.sag), 2)],
            "seed": rng.randint(0, 10_000),  # shape variation in the 3D view
        })

    for i, d in enumerate(defects, start=1):
        d["id"] = f"d{i}"
    return defects


def in_camera_view(pos: np.ndarray, yaw: float, target: np.ndarray) -> bool:
    d = target - pos
    horiz = math.hypot(d[0], d[1])
    bearing = math.degrees((math.atan2(d[1], d[0]) - yaw + math.pi) % (2 * math.pi) - math.pi)
    elevation = math.degrees(math.atan2(d[2], horiz))
    return abs(bearing) <= VIEW_HALF_H_DEG and VIEW_ELEVATION_DEG[0] <= elevation <= VIEW_ELEVATION_DEG[1]


def can_see(defect: dict[str, Any], pos: np.ndarray, yaw: float, speed: float) -> tuple[bool, float]:
    target = np.asarray(defect["pos"], dtype=float)
    dist = float(np.linalg.norm(target - pos))
    visible = dist <= SPOT_RANGE_M[defect["component"]] and speed <= SPOT_MAX_SPEED and in_camera_view(pos, yaw, target)
    return visible, dist

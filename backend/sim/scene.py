from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
import numpy as np

from backend.core.config import (
    CABLE_CAUTION_M,
    CABLE_DANGER_M,
    ROAD_MIN_CROSSING_ALT_M,
    STRUCTURE_CAUTION_M,
    STRUCTURE_DANGER_M,
)


def _vec(v: list[float] | tuple[float, ...] | np.ndarray) -> np.ndarray:
    return np.asarray(v, dtype=float)


@dataclass
class Scene:
    pylon_x: list[float] = field(default_factory=lambda: [0.0, 60.0, 120.0])
    pylon_height: float = 25.0
    cable_y: list[float] = field(default_factory=lambda: [-3.0, 3.0])
    sag: float = 4.0
    road_x: tuple[float, float] = (82.0, 90.0)
    # (x, y, trunk radius, trunk height); each canopy is a sphere 2 m above its trunk top
    trees: list[tuple[float, float, float, float]] = field(default_factory=lambda: [
        (30.0, 9.0, 2.0, 11.0),  # beside span 1
        (52.0, -10.0, 1.6, 9.0),  # south of tower 2
        (102.0, 8.0, 1.8, 13.0),  # tall, beside span 2: its canopy reaches towards the y = +3 cable
    ])
    defects: list[dict[str, Any]] = field(default_factory=list)  # set per flight by sim/defects.py

    # First tree, for older modules (guardrails.py) written when there was only one
    @property
    def tree_center(self) -> tuple[float, float]:
        return self.trees[0][0], self.trees[0][1]

    @property
    def tree_radius(self) -> float:
        return self.trees[0][2]

    @property
    def tree_height(self) -> float:
        return self.trees[0][3]

    @property
    def insulators(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        idx = 1
        for px in self.pylon_x:
            for y in self.cable_y:
                out.append({
                    "id": f"i{idx}",
                    "pos": [px, y, self.pylon_height],
                    "name": f"Pylon {int(px/60)+1} Insulator {idx}"
                })
                idx += 1
        return out


def cable_height_at(x: float, x0: float, x1: float, z_top: float, sag: float) -> float:
    mid = 0.5 * (x0 + x1)
    half = 0.5 * (x1 - x0)
    if half <= 0:
        return z_top
    xi = (x - mid) / half
    return z_top - sag * (1.0 - xi * xi)


def nearest_cable_point(pos: np.ndarray, scene: Scene) -> tuple[np.ndarray, float]:
    px, py, pz = pos
    best_d = float("inf")
    best = np.zeros(3)
    for yi in scene.cable_y:
        for i in range(len(scene.pylon_x) - 1):
            x0, x1 = scene.pylon_x[i], scene.pylon_x[i + 1]
            cx = float(np.clip(px, x0, x1))
            cz = cable_height_at(cx, x0, x1, scene.pylon_height, scene.sag)
            c = np.array([cx, yi, cz], dtype=float)
            d = float(np.linalg.norm(pos - c))
            if d < best_d:
                best_d, best = d, c
    return best, best_d


TREE_CANOPY_RADIUS = 3.0

# Tower shape (shared with the 3D view through scene_json): tapered lattice body up to the
# crossarm, insulator strings hanging from the crossarm down to the cable attachment point.
TOWER_BASE_HALF_WIDTH = 2.0
TOWER_TOP_HALF_WIDTH = 0.7
CROSSARM_ABOVE_CABLE = 2.0  # crossarm sits this far above the cable attachment height
CROSSARM_HALF_SPAN = 4.2
TOWER_PEAK_ABOVE_CROSSARM = 3.0
DRONE_RADIUS = 0.35  # propeller tip to centre
CABLE_HIT_DIST = 0.45


def collision(pos: np.ndarray, scene: Scene) -> str | None:
    """What the drone's airframe is touching ("cable", "tree", "pylon"), or None."""
    x, y, z = (float(v) for v in pos)
    r = DRONE_RADIUS
    if nearest_cable_point(pos, scene)[1] < CABLE_HIT_DIST:
        return "cable"

    for tx, ty, tr, th in scene.trees:
        if math.hypot(x - tx, y - ty) < tr + r and z <= th:
            return "tree"
        if float(np.linalg.norm(pos - np.array([tx, ty, th + 2.0]))) < TREE_CANOPY_RADIUS + r:
            return "tree"

    arm_z = scene.pylon_height + CROSSARM_ABOVE_CABLE
    for px in scene.pylon_x:
        dx, ay = abs(x - px), abs(y)
        if dx > CROSSARM_HALF_SPAN + r:
            continue
        if z <= arm_z:
            hw = TOWER_BASE_HALF_WIDTH - (TOWER_BASE_HALF_WIDTH - TOWER_TOP_HALF_WIDTH) * z / arm_z
            if dx < hw + r and ay < hw + r:
                return "pylon"
        elif z < arm_z + TOWER_PEAK_ABOVE_CROSSARM + r and dx < 0.4 + r and ay < 0.4 + r:
            return "pylon"
        if dx < 0.4 + r and ay < CROSSARM_HALF_SPAN + r and abs(z - arm_z) < 0.35 + r:
            return "pylon"  # crossarm
        for cy in scene.cable_y:
            if dx < 0.3 + r and abs(y - cy) < 0.3 + r and scene.pylon_height - 0.2 - r < z < arm_z + r:
                return "pylon"  # insulator string
    return None


def _box_gap(*gaps: np.ndarray) -> np.ndarray:
    """Distance outside a box given the signed gap along each axis (0 inside)."""
    return np.maximum(np.maximum.reduce([np.asarray(g, dtype=float) for g in gaps]), 0.0)


def hazard_distances(points: np.ndarray, scene: Scene) -> dict[str, np.ndarray]:
    """Clearance from many points at once (shape (..., 3)) to the surface of each hazard, in metres.

    Vectorised twin of collision() for the predictor: "cable" (to the conductor line), "tower"
    (lattice body, peak, crossarm and insulator strings), "tree" (trunk and canopy), "road"
    (0 = over the road) and "ground" (height). Box-shaped parts use the largest per-axis gap, the
    same test as collision(): a clearance below DRONE_RADIUS (cable: CABLE_HIT_DIST) is a crash.
    """
    p = np.asarray(points, dtype=float)
    x, y, z = p[..., 0], p[..., 1], p[..., 2]

    cable = np.full(x.shape, np.inf)
    for cy in scene.cable_y:
        for x0, x1 in zip(scene.pylon_x[:-1], scene.pylon_x[1:]):
            cx = np.clip(x, x0, x1)
            mid, half = 0.5 * (x0 + x1), 0.5 * (x1 - x0)
            cz = scene.pylon_height - scene.sag * (1.0 - ((cx - mid) / half) ** 2)
            cable = np.minimum(cable, np.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2))

    arm_z = scene.pylon_height + CROSSARM_ABOVE_CABLE
    peak_z = arm_z + TOWER_PEAK_ABOVE_CROSSARM
    tower = np.full(x.shape, np.inf)
    for px in scene.pylon_x:
        dx, ay = np.abs(x - px), np.abs(y)
        zc = np.clip(z, 0.0, arm_z)
        hw = np.where(z <= arm_z, TOWER_BASE_HALF_WIDTH - (TOWER_BASE_HALF_WIDTH - TOWER_TOP_HALF_WIDTH) * zc / arm_z, 0.4)
        body = _box_gap(dx - hw, ay - hw, z - peak_z)  # tapered body, then the peak
        arm = _box_gap(dx - 0.4, ay - CROSSARM_HALF_SPAN, np.abs(z - arm_z) - 0.35)
        tower = np.minimum(tower, np.minimum(body, arm))
        for cy in scene.cable_y:  # insulator strings hanging from the crossarm
            string = _box_gap(dx - 0.3, np.abs(y - cy) - 0.3, np.maximum(scene.pylon_height - 0.2 - z, z - arm_z))
            tower = np.minimum(tower, string)

    tree = np.full(x.shape, np.inf)
    for tx, ty, tr, th in scene.trees:
        trunk = _box_gap(np.hypot(x - tx, y - ty) - tr, z - th)
        canopy = np.maximum(np.sqrt((x - tx) ** 2 + (y - ty) ** 2 + (z - th - 2.0) ** 2) - TREE_CANOPY_RADIUS, 0.0)
        tree = np.minimum(tree, np.minimum(trunk, canopy))

    r0, r1 = scene.road_x
    road = np.maximum(np.maximum(r0 - x, x - r1), 0.0)
    return {"cable": cable, "tower": tower, "tree": tree, "road": road, "ground": z}


def _segment_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    s = float(np.clip(np.dot(p - a, ab) / max(float(np.dot(ab, ab)), 1e-9), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + s * ab)))


def clearances(pos: np.ndarray, scene: Scene) -> dict[str, Any]:
    """Distances from the drone to every hazard and inspection target, in metres."""
    cable_pt, cable_dist = nearest_cable_point(pos, scene)

    pylon_dist = min(
        _segment_dist(pos, np.array([x, 0.0, 0.0]), np.array([x, 0.0, scene.pylon_height]))
        for x in scene.pylon_x
    )

    tree_dist = max(0.0, min(
        min(
            _segment_dist(pos, np.array([tx, ty, 0.0]), np.array([tx, ty, th])) - tr,  # trunk
            float(np.linalg.norm(pos - np.array([tx, ty, th + 2.0]))) - TREE_CANOPY_RADIUS,  # canopy
        )
        for tx, ty, tr, th in scene.trees
    ))

    ins_id, ins_dist = min(
        ((ins["id"], float(np.linalg.norm(pos - _vec(ins["pos"])))) for ins in scene.insulators),
        key=lambda item: item[1],
    )

    r0, r1 = scene.road_x
    road_dist = float(max(0.0, r0 - pos[0], pos[0] - r1))

    return {
        "altitude": float(pos[2]),
        "cable_dist": cable_dist,
        "cable_dz": float(pos[2] - cable_pt[2]),  # >0: above the nearest cable
        "nearest_cable_point": cable_pt,
        "pylon_dist": pylon_dist,
        "tree_dist": tree_dist,
        "nearest_insulator": ins_id,
        "insulator_dist": ins_dist,
        "road_dist": road_dist,
        "over_road": road_dist == 0.0,
    }


def scene_json(scene: Scene) -> dict[str, Any]:
    cables: list[list[list[float]]] = []
    for y in scene.cable_y:
        pts: list[list[float]] = []
        for i in range(len(scene.pylon_x) - 1):
            x0, x1 = scene.pylon_x[i], scene.pylon_x[i + 1]
            xs = np.linspace(x0, x1, 25)
            for x in xs:
                pts.append([
                    float(x),
                    float(y),
                    float(cable_height_at(float(x), x0, x1, scene.pylon_height, scene.sag)),
                ])
        cables.append(pts)
    return {
        "pylons": [{"x": x, "height": scene.pylon_height} for x in scene.pylon_x],
        "cables": cables,
        "road_x": list(scene.road_x),
        "trees": [{"center": [tx, ty], "radius": tr, "height": th} for tx, ty, tr, th in scene.trees],
        "insulators": scene.insulators,
        "tower": {
            "base_half_width": TOWER_BASE_HALF_WIDTH,
            "top_half_width": TOWER_TOP_HALF_WIDTH,
            "crossarm_z": scene.pylon_height + CROSSARM_ABOVE_CABLE,
            "crossarm_half_span": CROSSARM_HALF_SPAN,
            "peak_z": scene.pylon_height + CROSSARM_ABOVE_CABLE + TOWER_PEAK_ABOVE_CROSSARM,
        },
        "canopy_radius": TREE_CANOPY_RADIUS,
        # one set of thresholds for the HUD colours, the alarms and the debrief
        "safety": {
            "cable_danger_m": CABLE_DANGER_M,
            "cable_caution_m": CABLE_CAUTION_M,
            "structure_danger_m": STRUCTURE_DANGER_M,
            "structure_caution_m": STRUCTURE_CAUTION_M,
            "road_min_crossing_alt_m": ROAD_MIN_CROSSING_ALT_M,
        },
        "defects": [{k: d[k] for k in ("id", "kind", "label", "component", "target", "pos", "disc", "seed") if k in d} for d in scene.defects],
    }

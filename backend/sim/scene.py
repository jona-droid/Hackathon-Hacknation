from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import numpy as np


def _vec(v: list[float] | tuple[float, ...] | np.ndarray) -> np.ndarray:
    return np.asarray(v, dtype=float)


@dataclass
class Scene:
    pylon_x: list[float] = field(default_factory=lambda: [0.0, 60.0, 120.0])
    pylon_height: float = 25.0
    cable_y: list[float] = field(default_factory=lambda: [-3.0, 3.0])
    sag: float = 4.0
    road_x: tuple[float, float] = (82.0, 90.0)
    tree_center: tuple[float, float] = (30.0, 9.0)
    tree_radius: float = 2.0
    tree_height: float = 11.0

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


def inside_tree(pos: np.ndarray, scene: Scene) -> bool:
    dx = pos[0] - scene.tree_center[0]
    dy = pos[1] - scene.tree_center[1]
    return (dx * dx + dy * dy) <= scene.tree_radius * scene.tree_radius and pos[2] <= scene.tree_height


TREE_CANOPY_RADIUS = 3.0


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

    tx, ty = scene.tree_center
    trunk = _segment_dist(pos, np.array([tx, ty, 0.0]), np.array([tx, ty, scene.tree_height])) - scene.tree_radius
    canopy = float(np.linalg.norm(pos - np.array([tx, ty, scene.tree_height + 2.0]))) - TREE_CANOPY_RADIUS
    tree_dist = max(0.0, min(trunk, canopy))

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
        "tree": {
            "center": list(scene.tree_center),
            "radius": scene.tree_radius,
            "height": scene.tree_height,
        },
        "insulators": scene.insulators,
    }

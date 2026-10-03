from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from backend.flight import FlightSim


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
    tree_radius: float = 1.5
    tree_height: float = 15.0

    @property
    def insulators(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        idx = 1
        for px in self.pylon_x:
            for y in self.cable_y:
                out.append({"id": f"i{idx}", "pos": [px, y, self.pylon_height]})
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


class EventDetector:
    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.prev_hover = False
        self.prev_over_road = False
        self.prev_near_tree = False
        self.prev_near_cable = False
        self.prev_very_close = False
        self.prev_airborne = False
        self.collision_sent = False
        self.mission_complete_sent = False
        self.inspected: set[str] = set()

    def reset(self) -> None:
        self.__init__(self.scene)

    def update(self, drone: FlightSim, t: float) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        _, cable_dist = nearest_cable_point(drone.pos, self.scene)
        speed = float(np.linalg.norm(drone.vel))
        hover = speed < 0.25 and drone.pos[2] > 0.3
        over_road = self.scene.road_x[0] <= drone.pos[0] <= self.scene.road_x[1] and drone.pos[2] < 20.0
        near_tree = np.linalg.norm(drone.pos[:2] - np.array(self.scene.tree_center)) <= 4.0
        near_cable = cable_dist < 5.0
        very_close = cable_dist < 2.0
        airborne = drone.pos[2] > 0.3

        if airborne and not self.prev_airborne:
            events.append({"type": "takeoff", "t": t})
        if (not airborne) and self.prev_airborne:
            events.append({"type": "land", "t": t})

        if hover and not self.prev_hover:
            events.append({"type": "hover_start", "t": t, "cable_dist": cable_dist})
        if (not hover) and self.prev_hover:
            events.append({"type": "hover_end", "t": t})

        if near_cable and not self.prev_near_cable:
            events.append({"type": "near_cable", "t": t, "cable_dist": cable_dist})
        if very_close and not self.prev_very_close:
            events.append({"type": "very_close_cable", "t": t, "cable_dist": cable_dist})
        if over_road and not self.prev_over_road:
            events.append({"type": "over_road", "t": t})
        if near_tree and not self.prev_near_tree:
            events.append({"type": "near_tree", "t": t})

        for ins in self.scene.insulators:
            if ins["id"] in self.inspected:
                continue
            ip = _vec(ins["pos"])
            if float(np.linalg.norm(drone.pos - ip)) < 3.0 and speed < 0.8:
                self.inspected.add(ins["id"])
                events.append({"type": "insulator_inspected", "t": t, "insulator_id": ins["id"]})

        if len(self.inspected) == len(self.scene.insulators) and not self.mission_complete_sent:
            self.mission_complete_sent = True
            events.append({"type": "mission_complete", "t": t})

        collided = drone.collided or cable_dist < 0.5 or inside_tree(drone.pos, self.scene)
        if collided and not self.collision_sent:
            if not drone.collided:
                drone.crash()
            self.collision_sent = True
            events.append({"type": "collision", "t": t, "cable_dist": cable_dist})

        self.prev_hover = hover
        self.prev_over_road = over_road
        self.prev_near_tree = near_tree
        self.prev_near_cable = near_cable
        self.prev_very_close = very_close
        self.prev_airborne = airborne
        return events


def snapshot(drone: FlightSim, scene: Scene) -> dict[str, Any]:
    cable_point, cable_dist = nearest_cable_point(drone.pos, scene)
    return {
        **drone.pose_json(),
        "pos": drone.pos.tolist(),
        "vel": drone.vel.tolist(),
        "v_cmd": drone.v_cmd.tolist(),
        "cable_dist": cable_dist,
        "nearest_cable_point": cable_point.tolist(),
        "collided": drone.collided,
    }


def scene_json(scene: Scene) -> dict[str, Any]:
    cables: list[list[list[float]]] = []
    for y in scene.cable_y:
        pts: list[list[float]] = []
        for i in range(len(scene.pylon_x) - 1):
            x0, x1 = scene.pylon_x[i], scene.pylon_x[i + 1]
            xs = np.linspace(x0, x1, 25)
            for x in xs:
                pts.append([float(x), y, float(cable_height_at(float(x), x0, x1, scene.pylon_height, scene.sag))])
        cables.append(pts)
    return {
        "pylons": [{"x": x, "height": scene.pylon_height} for x in scene.pylon_x],
        "cables": cables,
        "road_x": list(scene.road_x),
        "tree": {"center": list(scene.tree_center), "radius": scene.tree_radius, "height": scene.tree_height},
        "insulators": scene.insulators,
    }

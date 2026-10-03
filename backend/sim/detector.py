from __future__ import annotations

from collections import deque
from typing import Any
import numpy as np

from backend.sim.drone import DroneSim
from backend.sim.scene import Scene, inside_tree, nearest_cable_point


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
        self.insulator_hover_time: dict[str, float] = {}
        self.recent_speeds: deque[tuple[float, float]] = deque()
        self.prev_braked = False

    def reset(self) -> None:
        self.prev_hover = False
        self.prev_over_road = False
        self.prev_near_tree = False
        self.prev_near_cable = False
        self.prev_very_close = False
        self.prev_airborne = False
        self.collision_sent = False
        self.mission_complete_sent = False
        self.inspected.clear()
        self.insulator_hover_time.clear()
        self.recent_speeds.clear()
        self.prev_braked = False

    def update(self, drone: DroneSim, dt: float) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        t = drone.elapsed_time
        pos = drone.pos
        vel = drone.vel
        speed = drone.speed
        _, cable_dist = nearest_cable_point(pos, self.scene)

        hover = speed < 0.35 and pos[2] > 0.8
        over_road = self.scene.road_x[0] <= pos[0] <= self.scene.road_x[1] and pos[2] < 22.0
        near_tree = float(np.linalg.norm(pos[:2] - np.array(self.scene.tree_center))) <= 4.5
        near_cable = cable_dist < 4.0
        very_close = cable_dist < 1.8
        airborne = pos[2] > 0.4

        # Flight lifecycle events
        if airborne and not self.prev_airborne:
            events.append({"type": "takeoff", "t": t, "pos": pos.tolist()})
        if (not airborne) and self.prev_airborne:
            events.append({"type": "land", "t": t, "pos": pos.tolist()})

        # Hover events
        if hover and not self.prev_hover:
            events.append({
                "type": "hover_start",
                "t": t,
                "pos": pos.tolist(),
                "cable_dist": round(cable_dist, 2),
                "speed": round(speed, 2),
            })
        if (not hover) and self.prev_hover:
            events.append({"type": "hover_end", "t": t})

        # Proximity events
        if near_cable and not self.prev_near_cable:
            events.append({
                "type": "near_cable",
                "t": t,
                "cable_dist": round(cable_dist, 2),
                "altitude": round(pos[2], 2),
            })
        if very_close and not self.prev_very_close:
            events.append({
                "type": "very_close_cable",
                "t": t,
                "cable_dist": round(cable_dist, 2),
                "severity": "high",
            })
        if over_road and not self.prev_over_road:
            events.append({"type": "over_road", "t": t, "altitude": round(pos[2], 2)})
        if near_tree and not self.prev_near_tree:
            events.append({"type": "near_tree", "t": t, "pos": pos.tolist()})

        # Sudden deceleration: speed fell from >= 3 m/s to < 1 m/s within 1.5 s (fires once per stop)
        self.recent_speeds.append((t, speed))
        while t - self.recent_speeds[0][0] > 1.5:
            self.recent_speeds.popleft()
        peak_speed = max(s for _, s in self.recent_speeds)
        braked = speed < 1.0 and peak_speed >= 3.0 and not drone.collided
        if braked and not self.prev_braked:
            events.append({
                "type": "sudden_deceleration",
                "t": t,
                "from_speed": round(peak_speed, 2),
                "pos": pos.tolist(),
            })
        self.prev_braked = braked

        # Insulator inspection tracking
        for ins in self.scene.insulators:
            ins_id = ins["id"]
            if ins_id in self.inspected:
                continue
            dist_to_ins = float(np.linalg.norm(pos - np.array(ins["pos"])))
            if dist_to_ins < 3.2 and speed < 0.8:
                self.insulator_hover_time[ins_id] = self.insulator_hover_time.get(ins_id, 0.0) + dt
                if self.insulator_hover_time[ins_id] >= 1.5:
                    self.inspected.add(ins_id)
                    events.append({
                        "type": "insulator_inspected",
                        "t": t,
                        "insulator_id": ins_id,
                        "name": ins.get("name", ins_id),
                        "distance": round(dist_to_ins, 2),
                    })
            else:
                # Reset dwell time if pilot leaves
                self.insulator_hover_time[ins_id] = max(0.0, self.insulator_hover_time.get(ins_id, 0.0) - dt * 0.5)

        if len(self.inspected) == len(self.scene.insulators) and not self.mission_complete_sent:
            self.mission_complete_sent = True
            events.append({"type": "mission_complete", "t": t})

        # Collision detection
        if drone.collided and not self.collision_sent:
            self.collision_sent = True
            events.append({"type": "collision", "t": t, "pos": pos.tolist(), "cable_dist": round(cable_dist, 2)})

        self.prev_hover = hover
        self.prev_over_road = over_road
        self.prev_near_tree = near_tree
        self.prev_near_cable = near_cable
        self.prev_very_close = very_close
        self.prev_airborne = airborne

        return events

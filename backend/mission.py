from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.sim import Scene


def _norm(v: np.ndarray) -> float:
    return float(np.linalg.norm(v))


def _interp_line(a: np.ndarray, b: np.ndarray, step: float = 0.2) -> list[np.ndarray]:
    d = _norm(b - a)
    n = max(1, int(d / step))
    return [a + (b - a) * (i / n) for i in range(1, n + 1)]


def _guardrail_distance(guardrails: list[dict[str, Any]], target: str, default: float) -> float:
    for g in guardrails:
        if g.get("type") == "min_distance" and g.get("target") == target:
            return float(g.get("value_m", default))
    return default


def _hover_duration(guardrails: list[dict[str, Any]], default: float = 2.0) -> float:
    for g in guardrails:
        if g.get("type") == "hover_at" and g.get("target") == "insulator":
            return float(g.get("duration_s", default))
    return default


@dataclass
class MissionPlanner:
    scene: Scene
    guardrails: list[dict[str, Any]]
    waypoints: list[np.ndarray] = field(default_factory=list)
    cursor: int = 0
    hover_until: float | None = None

    def reset(self, start: np.ndarray) -> None:
        self.waypoints = []
        self.cursor = 0
        self.hover_until = None

        cable_clearance = _guardrail_distance(self.guardrails, "cable", 3.0)
        offset = cable_clearance + 1.0

        path: list[np.ndarray] = [start.copy(), np.array([start[0], start[1], 8.0])]
        for ins in self.scene.insulators:
            ip = np.array(ins["pos"], dtype=float)
            view = np.array([ip[0], ip[1] - offset, ip[2]], dtype=float)
            path.append(view)
        path.extend([np.array([start[0], start[1], 8.0]), start.copy()])

        dense: list[np.ndarray] = [path[0]]
        for i in range(len(path) - 1):
            dense.extend(_interp_line(path[i], path[i + 1], step=0.2))
        self.waypoints = dense

    def current_reference(self, pos: np.ndarray, t: float) -> dict[str, Any]:
        if not self.waypoints:
            self.reset(pos)
        if self.cursor >= len(self.waypoints):
            return {"done": True, "p_ref": self.waypoints[-1].tolist()}

        target = self.waypoints[self.cursor]
        if _norm(pos - target) < 1.0:
            self.cursor += 1
            if self.cursor >= len(self.waypoints):
                return {"done": True, "p_ref": target.tolist()}
            target = self.waypoints[self.cursor]

        hover_dur = _hover_duration(self.guardrails)
        for ins in self.scene.insulators:
            ip = np.array(ins["pos"])
            if _norm(target - np.array([ip[0], target[1], ip[2]])) < 1.1:
                if self.hover_until is None:
                    self.hover_until = t + hover_dur
                if t < self.hover_until:
                    return {"done": False, "p_ref": target.tolist(), "hover": True}
                self.hover_until = None

        return {"done": False, "p_ref": target.tolist(), "hover": False}

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.guardrails import check
from backend.sim import Drone, Scene


def predict(drone: Drone, v_cmd: np.ndarray, horizon: float = 2.0, dt: float = 0.1) -> list[dict[str, Any]]:
    pos = drone.pos.copy()
    vel = drone.vel.copy()
    tau = drone.tau
    points: list[dict[str, Any]] = []
    t = 0.0
    while t <= horizon + 1e-9:
        alpha = min(1.0, dt / max(tau, 1e-6))
        vel = (1 - alpha) * vel + alpha * np.clip(v_cmd, -drone.max_speed, drone.max_speed)
        pos = pos + vel * dt
        pos[2] = max(0.0, pos[2])
        t += dt
        points.append({"t": t, "pos": pos.tolist(), "vel": vel.tolist()})
    return points


@dataclass
class WarningLatch:
    active_ids: set[str] = field(default_factory=set)

    def update(
        self,
        drone: Drone,
        v_cmd: np.ndarray,
        scene: Scene,
        guardrails: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        path = predict(drone, v_cmd=v_cmd)
        warning: dict[str, Any] | None = None
        seen = set()
        for p in path:
            state = {"pos": p["pos"], "vel": p["vel"]}
            vios = check(state, guardrails, scene)
            hard = [v for v in vios if v.get("severity") == "hard"]
            if hard:
                first = hard[0]
                gid = str(first.get("guardrail_id"))
                seen.add(gid)
                if gid not in self.active_ids:
                    warning = {
                        "type": "warning",
                        "guardrail_id": gid,
                        "in_s": p["t"],
                        "text": first.get("text", "Guardrail violation predicted"),
                    }
                    self.active_ids.add(gid)
                break
        self.active_ids &= seen
        return path, warning

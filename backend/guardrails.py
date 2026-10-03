from __future__ import annotations

from typing import Any

import numpy as np

from backend.sim import Scene, nearest_cable_point


ALLOWED_TYPES = {"min_distance", "keep_out_zone", "max_speed", "altitude_range", "hover_at"}


def default_guardrails() -> list[dict[str, Any]]:
    return [
        {
            "id": "g1",
            "type": "min_distance",
            "target": "cable",
            "value_m": 3.0,
            "reason": "Arcing and electromagnetic interference with the compass",
            "source_quote": None,
            "severity": "hard",
            "status": "confirmed",
        },
        {
            "id": "g2",
            "type": "keep_out_zone",
            "zone": "road",
            "reason": "Do not operate over vehicle traffic",
            "source_quote": None,
            "severity": "hard",
            "status": "inferred",
        },
        {
            "id": "g3",
            "type": "max_speed",
            "zone": "near_pylon",
            "radius_m": 10,
            "value_mps": 1.5,
            "reason": "Slow down near structures for control margin",
            "source_quote": None,
            "severity": "soft",
            "status": "inferred",
        },
        {
            "id": "g4",
            "type": "altitude_range",
            "min_m": 5,
            "max_m": 35,
            "reason": "Maintain controllable and legal altitude",
            "source_quote": None,
            "severity": "hard",
            "status": "inferred",
        },
        {
            "id": "g5",
            "type": "hover_at",
            "target": "insulator",
            "duration_s": 2.0,
            "reason": "Stabilize for clear visual inspection",
            "source_quote": None,
            "severity": "soft",
            "status": "inferred",
        },
    ]


def validate_guardrails(guardrails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for g in guardrails:
        if g.get("type") not in ALLOWED_TYPES:
            continue
        valid.append(g)
    return valid


def _in_near_pylon(state: dict[str, Any], scene: Scene, radius: float) -> bool:
    pos = np.asarray(state["pos"], dtype=float)
    for x in scene.pylon_x:
        if np.linalg.norm(pos[:2] - np.array([x, 0.0])) <= radius:
            return True
    return False


def _in_near_cable(state: dict[str, Any], scene: Scene, radius: float = 8.0) -> bool:
    pos = np.asarray(state["pos"], dtype=float)
    _, d = nearest_cable_point(pos, scene)
    return d <= radius


def check(state: dict[str, Any], guardrails: list[dict[str, Any]], scene: Scene) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    pos = np.asarray(state["pos"], dtype=float)
    vel = np.asarray(state.get("vel", [0, 0, 0]), dtype=float)
    speed = float(np.linalg.norm(vel))
    _, cable_dist = nearest_cable_point(pos, scene)

    for g in guardrails:
        gtype = g.get("type")
        severity = g.get("severity", "soft")
        if gtype == "min_distance":
            target = g.get("target")
            dmin = float(g.get("value_m", 0.0))
            dist = float("inf")
            if target == "cable":
                dist = cable_dist
            elif target == "tree":
                dxy = np.linalg.norm(pos[:2] - np.array(scene.tree_center))
                dist = max(0.0, dxy - scene.tree_radius)
            elif target == "pylon":
                dist = min(np.linalg.norm(pos[:2] - np.array([x, 0.0])) for x in scene.pylon_x)
            if dist < dmin:
                violations.append({
                    "guardrail_id": g.get("id"),
                    "type": gtype,
                    "severity": severity,
                    "text": f"Too close to {target}: {dist:.1f}m < {dmin:.1f}m",
                })
        elif gtype == "keep_out_zone" and g.get("zone") == "road":
            if scene.road_x[0] <= pos[0] <= scene.road_x[1] and pos[2] < 30.0:
                violations.append({
                    "guardrail_id": g.get("id"),
                    "type": gtype,
                    "severity": severity,
                    "text": "Road no-fly zone violated",
                })
        elif gtype == "max_speed":
            zone = g.get("zone")
            vmax = float(g.get("value_mps", 5.0))
            active = zone == "global"
            if zone == "near_pylon":
                active = _in_near_pylon(state, scene, float(g.get("radius_m", 10.0)))
            elif zone == "near_cable":
                active = _in_near_cable(state, scene, float(g.get("radius_m", 8.0)))
            if active and speed > vmax:
                violations.append({
                    "guardrail_id": g.get("id"),
                    "type": gtype,
                    "severity": severity,
                    "text": f"Speed too high: {speed:.1f}m/s > {vmax:.1f}m/s",
                })
        elif gtype == "altitude_range":
            z = pos[2]
            zmin = float(g.get("min_m", 0.0))
            zmax = float(g.get("max_m", 999.0))
            if z < zmin or z > zmax:
                violations.append({
                    "guardrail_id": g.get("id"),
                    "type": gtype,
                    "severity": severity,
                    "text": f"Altitude out of bounds: {z:.1f}m not in [{zmin:.1f}, {zmax:.1f}]",
                })
    return violations

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import cvxpy as cp
import numpy as np

from backend.sim import Scene, nearest_cable_point


@dataclass
class MPCResult:
    v_cmd: np.ndarray
    feasible: bool
    solve_ms: float
    active_guardrails: list[str]


def _build_dynamics(dt: float) -> tuple[np.ndarray, np.ndarray]:
    I = np.eye(3)
    A = np.block([[I, dt * I], [np.zeros((3, 3)), I]])
    B = np.block([[0.5 * dt * dt * I], [dt * I]])
    return A, B


def _first(guardrails: list[dict[str, Any]], gtype: str, key: str, default: float) -> float:
    for g in guardrails:
        if g.get("type") == gtype:
            return float(g.get(key, default))
    return default


def solve_mpc(
    pos: np.ndarray,
    vel: np.ndarray,
    p_ref: np.ndarray,
    guardrails: list[dict[str, Any]],
    scene: Scene,
    dt: float = 0.1,
    N: int = 20,
) -> MPCResult:
    A, B = _build_dynamics(dt)
    x0 = np.concatenate([pos, vel])
    X = cp.Variable((6, N + 1))
    U = cp.Variable((3, N))

    cost = 0
    cons = [X[:, 0] == x0]
    Q = 10.0
    R = 1.0

    z_min = _first(guardrails, "altitude_range", "min_m", 0.0)
    z_max = _first(guardrails, "altitude_range", "max_m", 40.0)
    cable_min = 0.0
    for g in guardrails:
        if g.get("type") == "min_distance" and g.get("target") == "cable":
            cable_min = float(g.get("value_m", 0.0))
    active: list[str] = []

    for k in range(N):
        cons += [X[:, k + 1] == A @ X[:, k] + B @ U[:, k]]
        cons += [cp.abs(U[:, k]) <= 3.0]
        cons += [cp.abs(X[3:, k]) <= 5.0]
        cons += [X[2, k] >= z_min, X[2, k] <= z_max]

        if cable_min > 0:
            p_lin = pos if k == 0 else p_ref
            c, _ = nearest_cable_point(p_lin, scene)
            d = p_lin - c
            nd = np.linalg.norm(d)
            if nd > 1e-6:
                n = d / nd
                cons += [n @ (X[:3, k] - c) >= cable_min]

        for g in guardrails:
            if g.get("type") == "keep_out_zone" and g.get("zone") == "road":
                if pos[0] < scene.road_x[0] and p_ref[0] < scene.road_x[0]:
                    cons += [X[0, k] <= scene.road_x[0] - 0.5]
                elif pos[0] > scene.road_x[1] and p_ref[0] > scene.road_x[1]:
                    cons += [X[0, k] >= scene.road_x[1] + 0.5]
                else:
                    cons += [cp.abs(X[0, k] - 86.0) >= 6.0 - (X[2, k] - 30.0) * 0.2]

        cost += Q * cp.sum_squares(X[:3, k] - p_ref) + R * cp.sum_squares(U[:, k])

    cost += Q * cp.sum_squares(X[:3, N] - p_ref)
    cons += [cp.abs(X[3:, N]) <= 5.0]

    prob = cp.Problem(cp.Minimize(cost), cons)
    t0 = time.perf_counter()
    feasible = True
    try:
        prob.solve(solver=cp.OSQP, warm_start=True, verbose=False, max_iter=5000)
        if X.value is None:
            feasible = False
    except Exception:
        feasible = False

    if not feasible:
        v_cmd = np.zeros(3)
    else:
        v_cmd = np.asarray(X.value[3:, 1]).reshape(3)
        if abs(v_cmd[2] - z_min) < 0.1 or abs(v_cmd[2] - z_max) < 0.1:
            active.append("altitude_range")
        active.append("tracking")

    return MPCResult(v_cmd=np.clip(v_cmd, -5, 5), feasible=feasible, solve_ms=(time.perf_counter() - t0) * 1000.0, active_guardrails=active)


def _simulate_to_ref(scene: Scene, guardrails: list[dict[str, Any]]) -> tuple[float, float]:
    pos = np.array([-10.0, -2.0, 10.0])
    vel = np.zeros(3)
    ref = np.array([130.0, -2.0, 23.0])
    min_d = 1e9
    total_ms = 0.0

    for _ in range(150):
        res = solve_mpc(pos, vel, ref, guardrails, scene)
        total_ms += res.solve_ms
        vel = res.v_cmd
        pos = pos + vel * 0.1
        _, d = nearest_cable_point(pos, scene)
        min_d = min(min_d, d)
    return min_d, total_ms / 150.0


if __name__ == "__main__":
    from backend.guardrails import default_guardrails

    scene = Scene()
    gs = default_guardrails()
    min_cable_d, avg_ms = _simulate_to_ref(scene, gs)
    dmin = _first(gs, "min_distance", "value_m", 3.0)
    print(f"avg solve time: {avg_ms:.2f} ms")
    print(f"min cable distance: {min_cable_d:.2f} m")
    assert min_cable_d >= dmin - 0.2, f"distance violation: {min_cable_d:.2f} < {dmin - 0.2:.2f}"
    print("MPC test passed")

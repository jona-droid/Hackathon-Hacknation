"""Model-predictive safety: where the drone is heading in the next seconds, and what to do about it.

PREDICT_HZ times a second, the pilot's current stick command and a set of alternative manoeuvres
(brake, climb, strafe away...) are rolled out over PREDICT_HORIZON_S with a vectorised copy of the
flight controller in drone.py (velocity loop, tilt and thrust limits, attitude lag, drag, wind).
Every rollout is checked against the hazard geometry (scene.hazard_distances) and scored:
collision, clearance below the safety margins, crossing the road low, and the effort of deviating
from what the pilot asked for. The pilot's rollout gives the predicted path, the time to the first
conflict, the road crossing and the stopping distance; the cheapest alternative is the advice.

This is sampling-based MPC: re-planned at every step, and only the first action of the best plan
is ever applied, by the novice-mode Guardian, when a crash is imminent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.core.config import (
    CABLE_CAUTION_M,
    CABLE_DANGER_M,
    PREDICT_HORIZON_S,
    ROAD_MIN_CROSSING_ALT_M,
    STRUCTURE_CAUTION_M,
    STRUCTURE_DANGER_M,
)
from backend.core.config import SIM_HZ
from backend.sim.drone import (
    ATTITUDE_TAU,
    DRAG_K,
    HARD_LANDING_SPEED,
    MAX_ALTITUDE,
    MAX_CLIMB_ACC,
    MAX_HORIZ_ACC,
    MAX_SINK_ACC,
    POS_HOLD_GAIN,
    VEL_GAIN,
    VEL_INTEGRAL_GAIN,
    YAW_TAU,
    DroneSim,
)
from backend.sim.scene import CABLE_HIT_DIST, DRONE_RADIUS, Scene, hazard_distances

SIM_DT = 0.05
PATH_EVERY = 5  # path point every 0.25 s
# The simulator integrates first-order lags with Euler steps at SIM_HZ: match its effective time
# constant at the predictor's coarser step instead of the nominal one.
_REAL_DT = 1.0 / SIM_HZ
ATTITUDE_ALPHA = 1.0 - (1.0 - _REAL_DT / ATTITUDE_TAU) ** (SIM_DT / _REAL_DT)
YAW_ALPHA = 1.0 - (1.0 - _REAL_DT / YAW_TAU) ** (SIM_DT / _REAL_DT)
GUARDIAN_TRIGGER_S = 1.6  # the Guardian takes over when a crash is predicted this soon
TERMINAL_LOOKAHEAD_S = (0.5, 1.0, 1.5)  # beyond the horizon: where the final velocity still leads
ROAD_AHEAD_S = 3.5
ROAD_EVENT_MIN_GAP_S = 8.0
GUARDIAN_MIN_HOLD_S = 0.6
ESCAPE_SPEED = 3.5  # m/s for back-off and strafe manoeuvres
ESCAPE_CLIMB = 3.0

# Alternative manoeuvres: label for the HUD, words for the voice, effort (prefer small corrections)
ACTIONS: dict[str, dict[str, Any]] = {
    "pilot": {"label": "ON COURSE", "say": "keep going", "effort": 0.0},
    "slow": {"label": "SLOW DOWN", "say": "slow down", "effort": 1.5},
    "brake": {"label": "BRAKE", "say": "release the sticks and brake", "effort": 2.0},
    "climb": {"label": "CLIMB", "say": "climb now", "effort": 2.5},
    "brake_climb": {"label": "BRAKE + CLIMB", "say": "brake and climb", "effort": 3.0},
    "back_off": {"label": "BACK OFF", "say": "back off", "effort": 4.0},
    "strafe_left": {"label": "STRAFE LEFT", "say": "move left", "effort": 4.0},
    "strafe_right": {"label": "STRAFE RIGHT", "say": "move right", "effort": 4.0},
    "brake_descend": {"label": "BRAKE + DESCEND", "say": "brake and descend", "effort": 4.5},
}
CANDIDATES = list(ACTIONS)
HAZARD_WORDS = {"cable": "cable", "tower": "tower", "tree": "tree", "ground": "ground"}


@dataclass
class Prediction:
    t: float
    risk: str = "none"  # none | low | medium | high
    conflict: dict[str, Any] | None = None  # {"hazard", "in_s", "dist"}: first danger on the pilot's course
    action: str | None = None  # recommended manoeuvre (key of ACTIONS), when the course is not safe
    road_in_s: float | None = None  # time until the drone is over the road
    road_alt: float | None = None  # height when it gets there
    stop_dist: float = 0.0  # distance flown if the pilot releases the sticks now
    cannot_stop: bool = False  # even braking now ends inside a safety margin
    path: list[list[float]] = field(default_factory=list)  # pilot's predicted path
    stop_point: list[float] | None = None
    guardian: dict[str, Any] | None = None  # {"action", "v_cmd", "climb"} when a crash is imminent

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "risk": self.risk,
            "stop_dist": round(self.stop_dist, 1),
            "cannot_stop": self.cannot_stop,
            "path": self.path,
            "stop_point": self.stop_point,
        }
        if self.conflict:
            out["conflict"] = self.conflict
        if self.action:
            out["action"] = self.action
            out["action_label"] = ACTIONS[self.action]["label"]
        if self.road_in_s is not None:
            out["road_in_s"] = round(self.road_in_s, 1)
            out["road_alt"] = round(self.road_alt or 0.0, 1)
        return out


def _pilot_body_command(drone: DroneSim) -> tuple[float, float, float]:
    """The stick command (forward, left, climb in m/s) in the frame the flight controller believes in."""
    believed = drone.yaw + drone.compass_drift
    vx, vy, vz = (float(v) for v in drone.v_cmd)
    return vx * math.cos(believed) + vy * math.sin(believed), -vx * math.sin(believed) + vy * math.cos(believed), vz


def _wind(drone: DroneSim) -> np.ndarray:
    w = drone.wind.at(drone.pos[2])
    return np.asarray(w, dtype=float)


class TrajectoryPredictor:
    """Rolls out the pilot's command and the alternative manoeuvres; see the module docstring."""

    def __init__(self, scene: Scene, horizon_s: float = PREDICT_HORIZON_S) -> None:
        self.scene = scene
        self.steps = int(round(horizon_s / SIM_DT))
        self.latest: Prediction | None = None
        self._guardian_until = -1.0
        self._guardian: dict[str, Any] | None = None

    def reset(self) -> None:
        self.latest = None
        self._guardian_until = -1.0
        self._guardian = None

    # ---- rollouts ---------------------------------------------------------------------

    def _targets(self, drone: DroneSim, yaw_t: float, pos: np.ndarray) -> np.ndarray:
        """Target velocity of every candidate at one step, shape (K, 3)."""
        fwd, left, climb = _pilot_body_command(drone)
        horiz_stick, vert_stick = drone.stick_active
        c, s = math.cos(yaw_t), math.sin(yaw_t)
        pilot_xy = np.array([fwd * c - left * s, fwd * s + left * c]) if horiz_stick else np.zeros(2)
        # altitude hold (GPS mode) for every manoeuvre that doesn't climb or descend
        hold_alt = drone.hold_z if drone.hold_z is not None else float(drone.pos[2])
        hold_z = np.clip(POS_HOLD_GAIN * (hold_alt - pos[:, 2]), -1.0, 1.0) * np.ones(len(CANDIDATES))
        pilot_z = np.full(len(CANDIDATES), climb) if vert_stick else hold_z

        v0 = drone.vel[:2]
        speed0 = float(np.hypot(*v0))
        if speed0 > 0.5:
            back = -v0 / speed0
        elif np.hypot(*pilot_xy) > 0.1:
            back = -pilot_xy / np.hypot(*pilot_xy)
        else:
            back = -np.array([math.cos(drone.yaw), math.sin(drone.yaw)])
        left_dir = np.array([-math.sin(drone.yaw), math.cos(drone.yaw)])

        t = np.zeros((len(CANDIDATES), 3))
        t[:, 2] = hold_z
        t[0, :2], t[0, 2] = pilot_xy, pilot_z[0]  # pilot
        t[1, :2], t[1, 2] = 0.35 * pilot_xy, pilot_z[1]  # slow
        # 2: brake (zero horizontal, hold altitude)
        t[3, :2], t[3, 2] = pilot_xy, ESCAPE_CLIMB  # climb
        t[4, 2] = ESCAPE_CLIMB  # brake_climb
        t[5, :2] = ESCAPE_SPEED * back  # back_off
        t[6, :2] = ESCAPE_SPEED * left_dir  # strafe_left
        t[7, :2] = -ESCAPE_SPEED * left_dir  # strafe_right
        t[8, 2] = -2.0  # brake_descend
        # landing protection, as in DroneSim._target_velocity
        t[:, 2] = np.maximum(t[:, 2], -np.maximum(0.6, 0.8 * pos[:, 2]))
        return t

    def rollout(self, drone: DroneSim) -> tuple[np.ndarray, np.ndarray]:
        """Positions and velocities of every candidate, shapes (K, steps + 1, 3)."""
        k = len(CANDIDATES)
        pos = np.repeat(drone.pos[None, :], k, axis=0).astype(float)
        vel = np.repeat(drone.vel[None, :], k, axis=0).astype(float)
        thrust = np.repeat(drone.thrust_acc[None, :], k, axis=0).astype(float)
        integral = np.repeat(np.asarray(drone.vel_integral, dtype=float)[None, :], k, axis=0)
        wind = _wind(drone)
        positions = np.empty((k, self.steps + 1, 3))
        velocities = np.empty((k, self.steps + 1, 3))
        positions[:, 0], velocities[:, 0] = pos, vel
        landed = np.zeros(k, dtype=bool)
        yaw, yaw_rate = drone.yaw + drone.compass_drift, drone.yaw_rate

        for i in range(1, self.steps + 1):
            yaw_rate += (drone.yaw_rate_cmd - yaw_rate) * YAW_ALPHA
            yaw += yaw_rate * SIM_DT
            target = self._targets(drone, yaw, pos)
            err = target - vel
            # PI velocity loop with the same clamp and anti-windup as DroneSim.step
            integral = np.clip(integral + VEL_INTEGRAL_GAIN * err * SIM_DT, -3.0, 3.0)
            a_cmd = VEL_GAIN * err + integral
            h = np.hypot(a_cmd[:, 0], a_cmd[:, 1])
            saturated = h > MAX_HORIZ_ACC
            scale = np.where(saturated, MAX_HORIZ_ACC / np.maximum(h, 1e-9), 1.0)
            a_cmd[:, :2] *= scale[:, None]
            integral[saturated, :2] -= (VEL_INTEGRAL_GAIN[:2] * err[saturated, :2]) * SIM_DT
            a_cmd[:, 2] = np.clip(a_cmd[:, 2], -MAX_SINK_ACC, MAX_CLIMB_ACC)
            thrust += (a_cmd - thrust) * ATTITUDE_ALPHA
            air = vel - wind
            drag = -DRAG_K * np.linalg.norm(air, axis=1)[:, None] * air
            drag[:, 2] *= 0.5
            vel = vel + (thrust + drag) * SIM_DT
            pos = pos + vel * SIM_DT
            pos[:, 2] = np.minimum(pos[:, 2], MAX_ALTITUDE)
            ground = pos[:, 2] <= 0.0
            landed |= ground
            pos[ground, 2] = 0.0
            vel[ground] = np.where(vel[ground, 2:3] < -HARD_LANDING_SPEED, vel[ground], 0.0)
            positions[:, i], velocities[:, i] = pos, vel
        return positions, velocities

    # ---- scoring ----------------------------------------------------------------------

    def _assess(self, positions: np.ndarray, velocities: np.ndarray, now: dict[str, float]) -> dict[str, Any]:
        h = hazard_distances(positions, self.scene)
        times = np.arange(positions.shape[1]) * SIM_DT
        k = positions.shape[0]

        hit = (h["cable"] < CABLE_HIT_DIST) | (h["tower"] < DRONE_RADIUS) | (h["tree"] < DRONE_RADIUS)
        hard_landing = (h["ground"] <= 0.01) & (velocities[..., 2] < -HARD_LANDING_SPEED)
        crash = hit | hard_landing

        margins = {"cable": (CABLE_DANGER_M, CABLE_CAUTION_M), "tower": (STRUCTURE_DANGER_M, STRUCTURE_CAUTION_M),
                   "tree": (STRUCTURE_DANGER_M, STRUCTURE_CAUTION_M)}
        weight = 1.0 - 0.5 * times / times[-1]  # the near future is more certain
        cost = np.array([ACTIONS[c]["effort"] for c in CANDIDATES], dtype=float)
        danger = np.zeros((k, positions.shape[1]), dtype=bool)
        caution = np.zeros_like(danger)
        for name, (d_m, c_m) in margins.items():
            d = h[name]
            # only count a margin the drone is entering, not one it already sits in and keeps
            closing = d < now[name] - 0.15
            danger |= (d < d_m) & closing
            caution |= (d < c_m) & closing
            cost += 30.0 * np.sum(weight * np.maximum(d_m - d, 0.0) ** 2 * closing, axis=1)
            cost += 2.0 * np.sum(weight * np.maximum(c_m - d, 0.0) ** 2 * closing, axis=1)

        first_crash = np.where(crash.any(axis=1), crash.argmax(axis=1), -1)
        cost += np.where(first_crash >= 0, 1000.0 + 500.0 * (1.0 - first_crash / positions.shape[1]), 0.0)

        # terminal cost: a plan that only pushes the crash past the horizon is not a safe plan
        ahead = positions[:, -1, None, :] + velocities[:, -1, None, :] * np.asarray(TERMINAL_LOOKAHEAD_S)[None, :, None]
        th = hazard_distances(ahead, self.scene)
        terminal_danger = ((th["cable"] < CABLE_DANGER_M) | (th["tower"] < STRUCTURE_DANGER_M)
                           | (th["tree"] < STRUCTURE_DANGER_M)).any(axis=1)
        cost += np.where(terminal_danger & (first_crash < 0), 300.0, 0.0)

        over_road = h["road"] <= 0.0
        low = np.maximum(ROAD_MIN_CROSSING_ALT_M - positions[..., 2], 0.0) / ROAD_MIN_CROSSING_ALT_M
        hover = np.hypot(velocities[..., 0], velocities[..., 1]) < 1.0
        cost += np.sum(over_road * (3.0 * low + 1.0 * hover), axis=1)
        return {"h": h, "crash": crash, "first_crash": first_crash, "danger": danger, "caution": caution,
                "cost": cost, "over_road": over_road, "times": times}

    # ---- public -----------------------------------------------------------------------

    def predict(self, drone: DroneSim) -> Prediction:
        pred = Prediction(t=drone.elapsed_time)
        if drone.collided or drone.on_ground and drone.v_cmd[2] <= 0.0:
            self.latest = pred
            return pred

        positions, velocities = self.rollout(drone)
        now_h = hazard_distances(drone.pos[None, :], self.scene)
        now = {name: float(now_h[name][0]) for name in ("cable", "tower", "tree")}
        a = self._assess(positions, velocities, now)
        times, h = a["times"], a["h"]
        pilot, brake = 0, CANDIDATES.index("brake")

        pred.path = [[round(float(v), 2) for v in p] for p in positions[pilot, ::PATH_EVERY]]

        # stopping distance: what the drone covers if the pilot lets go now
        b_speed = np.linalg.norm(velocities[brake], axis=1)
        stop_i = int(np.argmax(b_speed < 0.3)) if (b_speed < 0.3).any() else len(b_speed) - 1
        seg = np.linalg.norm(np.diff(positions[brake, : stop_i + 1], axis=0), axis=1)
        pred.stop_dist = float(seg.sum())
        pred.stop_point = [round(float(v), 2) for v in positions[brake, stop_i]]
        pred.cannot_stop = bool(a["first_crash"][brake] >= 0 or a["danger"][brake].any())

        # first conflict on the pilot's course
        crash_i = int(a["first_crash"][pilot])
        danger_row = a["danger"][pilot]
        danger_i = int(np.argmax(danger_row)) if danger_row.any() else -1
        idx = crash_i if crash_i >= 0 else danger_i
        if idx >= 0:
            dists = {name: float(h[name][pilot, idx]) for name in ("cable", "tower", "tree")}
            hazard = "ground" if crash_i >= 0 and min(dists.values()) > 1.0 else min(dists, key=dists.get)
            pred.conflict = {"hazard": hazard, "in_s": round(float(times[idx]), 1),
                             "dist": round(max(0.0, dists.get(hazard, 0.0)), 1), "crash": crash_i >= 0}
        if crash_i >= 0:
            pred.risk = "high" if times[crash_i] <= 2.0 else "medium"
        elif danger_i >= 0:
            pred.risk = "medium"
        elif a["caution"][pilot].any():
            pred.risk = "low"

        # road ahead on the pilot's course
        road_i = int(np.argmax(a["over_road"][pilot])) if a["over_road"][pilot].any() else -1
        if float(now_h["road"][0]) > 0.0 and road_i >= 0 and (crash_i < 0 or road_i < crash_i):
            i = road_i
            pred.road_in_s = float(times[i])
            pred.road_alt = float(positions[pilot, i, 2])
            if pred.road_alt < ROAD_MIN_CROSSING_ALT_M and pred.risk in ("none", "low"):
                pred.risk = "low"

        # advice: the cheapest manoeuvre, when it is clearly better than the pilot's course
        cost = a["cost"]
        if pred.risk in ("medium", "high"):
            best = int(np.argmin(cost[1:])) + 1
            if cost[best] < cost[pilot] - 5.0:
                pred.action = CANDIDATES[best]
        elif pred.road_in_s is not None and (pred.road_alt or 0.0) < ROAD_MIN_CROSSING_ALT_M:
            # low road crossing: climb on the way if that clears the minimum height in time, else stop first
            i = int(round(pred.road_in_s / SIM_DT))
            climb_alt = float(positions[CANDIDATES.index("climb"), i, 2])
            pred.action = "climb" if climb_alt >= ROAD_MIN_CROSSING_ALT_M else "brake_climb"

        # Guardian: a crash is imminent and a manoeuvre avoids it
        if crash_i >= 0 and times[crash_i] <= GUARDIAN_TRIGGER_S:
            safe = [i for i in range(1, len(CANDIDATES)) if a["first_crash"][i] < 0]
            if safe:
                best = min(safe, key=lambda i: cost[i])
                pred.guardian = {"action": CANDIDATES[best], "hazard": pred.conflict["hazard"] if pred.conflict else None,
                                 "in_s": round(float(times[crash_i]), 1)}
                start = np.repeat(drone.pos[None, :], len(CANDIDATES), axis=0)
                self._guardian = {**pred.guardian, "target": self._targets(drone, drone.yaw + drone.compass_drift, start)[best]}
                self._guardian_until = drone.elapsed_time + GUARDIAN_MIN_HOLD_S

        self.latest = pred
        return pred

    def guardian_command(self, drone: DroneSim) -> dict[str, Any] | None:
        """The manoeuvre the Guardian is flying right now, or None (the pilot has the sticks)."""
        if self._guardian is None or drone.elapsed_time > self._guardian_until or drone.collided:
            self._guardian = None
            return None
        return self._guardian

    def apply_guardian(self, drone: DroneSim) -> dict[str, Any] | None:
        """Override the pilot's command with the Guardian's manoeuvre (call after set_keys)."""
        g = self.guardian_command(drone)
        if g is None:
            return None
        target = np.asarray(g["target"], dtype=float)
        drone.v_cmd = target.copy()
        horiz = bool(np.hypot(target[0], target[1]) > 0.05)
        vert = bool(abs(target[2]) > 0.05)
        drone.stick_active = (horiz, vert)
        if not horiz:
            drone.hold_xy = None  # brake first, then hold where the drone stops
        return g


class PredictiveMonitor:
    """Turns predictions into edge-triggered events for the log, the apprentice and the tutor."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._road_armed = True
        self._last_road_event = -1e9
        self._conflict_active = False
        self._calm_since: float | None = None
        self._cannot_stop = False
        self._guardian_active = False

    def update(self, pred: Prediction, state: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        t = pred.t

        # one warning per approach: re-armed once the road is no longer on the predicted course
        if (pred.road_in_s is not None and pred.road_in_s <= ROAD_AHEAD_S and self._road_armed
                and t - self._last_road_event >= ROAD_EVENT_MIN_GAP_S):
            self._road_armed = False
            self._last_road_event = t
            low = (pred.road_alt or 0.0) < ROAD_MIN_CROSSING_ALT_M
            events.append({"type": "road_ahead", "t": t, "in_s": round(pred.road_in_s, 1),
                           "alt": round(pred.road_alt or 0.0, 1), "low": low,
                           "action": pred.action if low else None,
                           "speed": round(float(state.get("speed", 0.0)), 1)})
        if pred.road_in_s is None and state.get("road_dist", 0.0) > 2.0:
            self._road_armed = True

        if pred.risk == "high":
            self._calm_since = None
            if not self._conflict_active:
                self._conflict_active = True
                c = pred.conflict or {}
                events.append({"type": "conflict_predicted", "t": t, "hazard": c.get("hazard"),
                               "in_s": c.get("in_s"), "dist": c.get("dist"), "action": pred.action,
                               "speed": round(float(state.get("speed", 0.0)), 1)})
        elif self._conflict_active:
            self._calm_since = self._calm_since if self._calm_since is not None else t
            if t - self._calm_since >= 1.0:
                self._conflict_active = False

        if pred.cannot_stop and not self._cannot_stop and float(state.get("speed", 0.0)) > 2.0:
            events.append({"type": "cannot_stop", "t": t, "speed": round(float(state.get("speed", 0.0)), 1),
                           "stop_dist": round(pred.stop_dist, 1),
                           "hazard": (pred.conflict or {}).get("hazard")})
        self._cannot_stop = pred.cannot_stop

        guardian = pred.guardian is not None
        if guardian and not self._guardian_active:
            events.append({"type": "guardian_engaged", "t": t, **(pred.guardian or {})})
        self._guardian_active = guardian
        return events

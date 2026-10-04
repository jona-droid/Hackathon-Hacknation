from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any
import numpy as np

from backend.sim.scene import Scene, clearances, collision, nearest_cable_point
from backend.sim.wind import Wind

START_POS = np.array([-10.0, -10.0, 0.0], dtype=float)
# Stick limits, close to a DJI-class inspection drone in normal (GPS) mode
FORWARD_SPEED = 12.0
STRAFE_SPEED = 8.0
YAW_RATE = 1.2
CLIMB_RATE = 5.0
DESCENT_RATE = 3.0  # descents are slower than climbs (vortex ring state)
MAX_ALTITUDE = 60.0

GRAVITY = 9.81
MAX_TILT = math.radians(30.0)
MAX_HORIZ_ACC = GRAVITY * math.tan(MAX_TILT)
MAX_CLIMB_ACC = 6.0  # thrust margin above hover
MAX_SINK_ACC = 8.0  # throttling down can shed most of the lift
DRAG_K = 0.025  # quadratic drag, 1/m: limits airspeed to ~15 m/s at full tilt
ATTITUDE_TAU = 0.12  # s for the airframe to reach the commanded tilt
YAW_TAU = 0.15
VEL_GAIN = np.array([2.2, 2.2, 3.5])  # velocity loop (1/s)
VEL_INTEGRAL_GAIN = np.array([0.8, 0.8, 0.0])  # learns the steady horizontal wind push
POS_HOLD_GAIN = 0.9  # GPS position hold when the sticks are centred (1/s)
POS_HOLD_MAX_SPEED = 2.0
HARD_LANDING_SPEED = 2.5  # m/s vertical at touchdown

# High-voltage conductors disturb the magnetometer: heading drifts when close to a cable
COMPASS_INTERFERENCE_RANGE_M = 5.0
COMPASS_DRIFT_SIGMA = 0.08  # rad/s at the cable
COMPASS_DRIFT_TAU_S = 2.0


def keys_to_command(keys: set[str]) -> tuple[float, float, float, float]:
    """Map held keys to (forward m/s, left m/s, yaw rate rad/s, climb m/s)."""
    k = {x.lower() for x in keys}
    fwd = float(("w" in k or "arrowup" in k) - ("s" in k or "arrowdown" in k))
    left = float(("a" in k) - ("d" in k))
    yaw = float(("q" in k or "arrowleft" in k) - ("e" in k or "arrowright" in k))
    climb = float((" " in k or "space" in k) - ("shift" in k))
    return FORWARD_SPEED * fwd, STRAFE_SPEED * left, YAW_RATE * yaw, (CLIMB_RATE if climb > 0 else DESCENT_RATE) * climb


@dataclass
class DroneSim:
    scene: Scene
    pos: np.ndarray = field(default_factory=lambda: START_POS.copy())
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    acc: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    v_cmd: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    yaw: float = 0.0
    yaw_rate_cmd: float = 0.0
    yaw_rate: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    collided: bool = False
    collision_with: str | None = None
    elapsed_time: float = 0.0
    wind: Wind = field(default_factory=Wind)
    # flight controller state
    thrust_acc: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))  # acc from tilted thrust
    vel_integral: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    hold_xy: np.ndarray | None = None
    hold_z: float | None = None
    stick_active: tuple[bool, bool] = (False, False)  # (horizontal, vertical)
    compass_drift: float = 0.0  # heading estimate error (rad)
    compass_drift_rate: float = 0.0
    compass_interference: float = 0.0  # 0..1

    def __post_init__(self) -> None:
        self.wind.reset()

    def reset(self) -> None:
        self.pos = START_POS.copy()
        self.vel = np.zeros(3, dtype=float)
        self.acc = np.zeros(3, dtype=float)
        self.v_cmd = np.zeros(3, dtype=float)
        self.yaw = 0.0
        self.yaw_rate_cmd = 0.0
        self.yaw_rate = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.collided = False
        self.collision_with = None
        self.elapsed_time = 0.0
        self.wind.reset()
        self.thrust_acc = np.zeros(3, dtype=float)
        self.vel_integral = np.zeros(3, dtype=float)
        self.hold_xy = None
        self.hold_z = None
        self.stick_active = (False, False)
        self.compass_drift = 0.0
        self.compass_drift_rate = 0.0
        self.compass_interference = 0.0

    @property
    def speed(self) -> float:
        return float(np.linalg.norm(self.vel))

    @property
    def altitude(self) -> float:
        return float(self.pos[2])

    @property
    def on_ground(self) -> bool:
        return self.pos[2] <= 0.02

    @property
    def rpy(self) -> list[float]:
        return [self.roll, self.pitch, self.yaw]

    @property
    def quat(self) -> list[float]:
        # Quaternion [x, y, z, w] from RPY (Euler ZYX)
        cy = math.cos(self.yaw * 0.5)
        sy = math.sin(self.yaw * 0.5)
        cp = math.cos(self.pitch * 0.5)
        sp = math.sin(self.pitch * 0.5)
        cr = math.cos(self.roll * 0.5)
        sr = math.sin(self.roll * 0.5)

        w = cr * cp * cy + sr * sp * sy
        x = sr * cp * cy - cr * sp * sy
        y = cr * sp * cy + sr * cp * sy
        z = cr * cp * sy - sr * sp * cy
        return [float(x), float(y), float(z), float(w)]

    def set_keys(self, keys: set[str]) -> None:
        if self.collided:
            return
        fwd, left, yaw_rate, climb = keys_to_command(keys)
        # Sticks are interpreted in the heading the flight controller believes in (skewed by compass drift)
        believed_yaw = self.yaw + self.compass_drift
        cos_y = math.cos(believed_yaw)
        sin_y = math.sin(believed_yaw)
        vx = fwd * cos_y - left * sin_y
        vy = fwd * sin_y + left * cos_y

        self.v_cmd = np.array([vx, vy, climb], dtype=float)
        self.yaw_rate_cmd = yaw_rate
        self.stick_active = (bool(fwd or left), bool(climb))

    def _target_velocity(self) -> np.ndarray:
        """Stick velocity, or GPS position/altitude hold once the sticks are released and the drone has braked."""
        target = self.v_cmd.copy()
        horiz_moving, vert_moving = self.stick_active
        if horiz_moving:
            self.hold_xy = None
        else:
            if self.hold_xy is None and math.hypot(self.vel[0], self.vel[1]) < 0.5:
                self.hold_xy = self.pos[:2].copy()
            if self.hold_xy is not None:
                err = self.hold_xy - self.pos[:2]
                v = POS_HOLD_GAIN * err
                n = float(np.linalg.norm(v))
                target[:2] = v * (POS_HOLD_MAX_SPEED / n) if n > POS_HOLD_MAX_SPEED else v
        if vert_moving:
            self.hold_z = None
        else:
            if self.hold_z is None and abs(self.vel[2]) < 0.3:
                self.hold_z = float(self.pos[2])
            if self.hold_z is not None:
                target[2] = float(np.clip(POS_HOLD_GAIN * (self.hold_z - self.pos[2]), -1.0, 1.0))
        # Landing protection (downward sensors): descent slows down close to the ground
        target[2] = max(target[2], -max(0.6, 0.8 * float(self.pos[2])))
        return target

    def _crash(self, what: str) -> None:
        self.collided = True
        self.collision_with = what
        self.vel[:2] = 0.0
        self.v_cmd[:] = 0.0

    def step(self, dt: float, wind_enabled: bool = True) -> None:
        self.elapsed_time += dt
        if self.collided:
            # Motors stopped: free fall
            self.acc = np.array([0.0, 0.0, -GRAVITY])
            self.vel[2] -= GRAVITY * dt
            self.pos += self.vel * dt
            if self.pos[2] <= 0.0:
                self.pos[2] = 0.0
                self.vel[:] = 0.0
                self.acc = np.zeros(3, dtype=float)
            return

        if wind_enabled:
            self.wind.step(dt)
        wind = self.wind.at(self.pos[2]) if wind_enabled else np.zeros(3)

        # Resting on the ground until the pilot climbs
        if self.on_ground and self.v_cmd[2] <= 0.0:
            self.pos[2] = 0.0
            self.vel[:] = 0.0
            self.acc[:] = 0.0
            self.thrust_acc[:] = 0.0
            self.vel_integral[:] = 0.0
            self.hold_xy, self.hold_z = None, None
            self.pitch = self.roll = 0.0
            self._step_yaw(dt)
            return

        # Flight controller: velocity loop (PI, integral cancels the steady wind) -> desired acceleration,
        # limited by the maximum tilt horizontally and by thrust margin vertically
        v_target = self._target_velocity()
        err = v_target - self.vel
        self.vel_integral = np.clip(self.vel_integral + VEL_INTEGRAL_GAIN * err * dt, -3.0, 3.0)
        a_cmd = VEL_GAIN * err + self.vel_integral
        h = float(np.hypot(a_cmd[0], a_cmd[1]))
        if h > MAX_HORIZ_ACC:
            a_cmd[:2] *= MAX_HORIZ_ACC / h
            self.vel_integral[:2] -= VEL_INTEGRAL_GAIN[:2] * err[:2] * dt  # anti-windup while saturated
        a_cmd[2] = float(np.clip(a_cmd[2], -MAX_SINK_ACC, MAX_CLIMB_ACC))

        # The airframe needs time to tilt
        self.thrust_acc += (a_cmd - self.thrust_acc) * min(1.0, dt / ATTITUDE_TAU)

        # Aerodynamic drag acts on airspeed, so wind pushes the drone and gusts make it wobble
        air = self.vel - wind
        drag = -DRAG_K * float(np.linalg.norm(air)) * air
        drag[2] *= 0.5
        new_vel = self.vel + (self.thrust_acc + drag) * dt
        self.acc = (new_vel - self.vel) / dt if dt > 0 else np.zeros(3)
        self.vel = new_vel
        self.pos += self.vel * dt
        if self.pos[2] > MAX_ALTITUDE:
            self.pos[2] = MAX_ALTITUDE
            self.vel[2] = min(self.vel[2], 0.0)

        # Touchdown: gentle landing or crash
        if self.pos[2] <= 0.0:
            if self.vel[2] < -HARD_LANDING_SPEED:
                self.pos[2] = 0.0
                self._crash("ground")
                self.vel[:] = 0.0
                return
            self.pos[2] = 0.0
            self.vel[:] = 0.0

        self._step_yaw(dt)

        # Attitude as seen from the body: lean forward to accelerate forward (pitch < 0), and into the wind
        cos_y, sin_y = math.cos(self.yaw), math.sin(self.yaw)
        a_fwd = self.thrust_acc[0] * cos_y + self.thrust_acc[1] * sin_y
        a_left = -self.thrust_acc[0] * sin_y + self.thrust_acc[1] * cos_y
        self.pitch = -math.atan2(a_fwd, GRAVITY)
        self.roll = math.atan2(a_left, GRAVITY)

        hit = collision(self.pos, self.scene)
        if hit:
            self._crash(hit)

    def _step_yaw(self, dt: float) -> None:
        # Compass interference near conductors: the heading estimate wanders and the drone yaws with it
        _, cable_dist = nearest_cable_point(self.pos, self.scene)
        self.compass_interference = float(np.clip(1.0 - cable_dist / COMPASS_INTERFERENCE_RANGE_M, 0.0, 1.0))
        a = math.exp(-dt / COMPASS_DRIFT_TAU_S)
        sigma = COMPASS_DRIFT_SIGMA * self.compass_interference
        self.compass_drift_rate = a * self.compass_drift_rate + math.sqrt(1 - a * a) * sigma * random.gauss(0.0, 1.0)
        drift_rate = 0.0 if self.on_ground else self.compass_drift_rate
        self.compass_drift = float(np.clip(self.compass_drift + drift_rate * dt, -0.25, 0.25))
        if self.compass_interference == 0.0:
            self.compass_drift *= math.exp(-dt / 3.0)  # magnetometer recovers away from the line

        self.yaw_rate += (self.yaw_rate_cmd + drift_rate - self.yaw_rate) * min(1.0, dt / YAW_TAU)
        self.yaw += self.yaw_rate * dt
        self.yaw = (self.yaw + math.pi) % (2 * math.pi) - math.pi

    def snapshot(self) -> dict[str, Any]:
        c = clearances(self.pos, self.scene)
        horiz_speed = math.hypot(self.vel[0], self.vel[1])
        w = self.wind.at(self.pos[2])
        return {
            "pos": [round(float(x), 3) for x in self.pos],
            "vel": [round(float(x), 3) for x in self.vel],
            "acc": [round(float(x), 3) for x in self.acc],
            "speed": round(self.speed, 2),
            "horizontal_speed": round(horiz_speed, 2),
            "vertical_speed": round(float(self.vel[2]), 2),
            "acc_magnitude": round(float(np.linalg.norm(self.acc)), 2),
            "altitude": round(self.altitude, 2),
            "yaw": round(self.yaw, 3),
            "heading_deg": round((90.0 - math.degrees(self.yaw)) % 360.0, 1),
            "rpy": [round(float(x), 3) for x in self.rpy],
            "quat": [round(float(x), 4) for x in self.quat],
            "cable_dist": round(c["cable_dist"], 2),
            "cable_dz": round(c["cable_dz"], 2),
            "nearest_cable_point": [round(float(x), 3) for x in c["nearest_cable_point"]],
            "pylon_dist": round(c["pylon_dist"], 2),
            "tree_dist": round(c["tree_dist"], 2),
            "nearest_insulator": c["nearest_insulator"],
            "insulator_dist": round(c["insulator_dist"], 2),
            "road_dist": round(c["road_dist"], 2),
            "over_road": c["over_road"],
            "collided": self.collided,
            "collision_with": self.collision_with,
            "wind": [round(float(x), 2) for x in w],
            "wind_speed": round(float(math.hypot(w[0], w[1])), 1),
            "wind_from_deg": round(self.wind.from_heading_deg, 0),
            "compass_interference": round(self.compass_interference, 2),
            "position_hold": self.hold_xy is not None and not self.on_ground,
            "t": round(self.elapsed_time, 2),
        }

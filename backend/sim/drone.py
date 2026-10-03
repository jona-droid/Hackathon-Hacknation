from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any
import numpy as np

from backend.sim.scene import Scene, inside_tree, nearest_cable_point

START_POS = np.array([-10.0, -10.0, 0.05], dtype=float)
FORWARD_SPEED = 13.0
STRAFE_SPEED = 10.0
YAW_RATE = 1.2
CLIMB_RATE = 8.0
MAX_ALTITUDE = 60.0


def keys_to_command(keys: set[str]) -> tuple[float, float, float, float]:
    """Map held keys to (forward m/s, left m/s, yaw rate rad/s, climb m/s)."""
    k = {x.lower() for x in keys}
    fwd = float(("w" in k or "arrowup" in k) - ("s" in k or "arrowdown" in k))
    left = float(("a" in k) - ("d" in k))
    yaw = float(("q" in k or "arrowleft" in k) - ("e" in k or "arrowright" in k))
    climb = float((" " in k or "space" in k) - ("shift" in k))
    return FORWARD_SPEED * fwd, STRAFE_SPEED * left, YAW_RATE * yaw, CLIMB_RATE * climb


@dataclass
class DroneSim:
    scene: Scene
    pos: np.ndarray = field(default_factory=lambda: START_POS.copy())
    vel: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    acc: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    v_cmd: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    yaw: float = 0.0
    yaw_rate_cmd: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    collided: bool = False
    tau: float = 0.35
    elapsed_time: float = 0.0
    _last_vel: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))

    def reset(self) -> None:
        self.pos = START_POS.copy()
        self.vel = np.zeros(3, dtype=float)
        self.acc = np.zeros(3, dtype=float)
        self.v_cmd = np.zeros(3, dtype=float)
        self.yaw = 0.0
        self.yaw_rate_cmd = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.collided = False
        self.elapsed_time = 0.0
        self._last_vel = np.zeros(3, dtype=float)

    @property
    def speed(self) -> float:
        return float(np.linalg.norm(self.vel))

    @property
    def altitude(self) -> float:
        return float(self.pos[2])

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
        # Convert body-frame forward & left to world-frame velocity
        cos_y = math.cos(self.yaw)
        sin_y = math.sin(self.yaw)
        vx = fwd * cos_y - left * sin_y
        vy = fwd * sin_y + left * cos_y
        vz = climb

        self.v_cmd = np.array([vx, vy, vz], dtype=float)
        self.yaw_rate_cmd = yaw_rate

    def step(self, dt: float, wind_enabled: bool = True) -> None:
        self.elapsed_time += dt
        if self.collided:
            # Drone falls under gravity if collided
            self.vel[2] -= 9.81 * dt
            self.pos += self.vel * dt
            if self.pos[2] <= 0.0:
                self.pos[2] = 0.0
                self.vel[:] = 0.0
            return

        alpha = min(1.0, dt / max(self.tau, 1e-4))
        wind = np.zeros(3)
        if wind_enabled and self.pos[2] > 1.0:
            wind = np.array([
                random.uniform(-0.15, 0.15),
                random.uniform(-0.15, 0.15),
                random.uniform(-0.05, 0.05),
            ])

        target_vel = self.v_cmd + wind
        new_vel = (1.0 - alpha) * self.vel + alpha * target_vel

        # Compute acceleration
        if dt > 0:
            self.acc = (new_vel - self._last_vel) / dt
        self._last_vel = self.vel.copy()
        self.vel = new_vel

        # Integrate position
        self.pos += self.vel * dt
        self.pos[2] = float(np.clip(self.pos[2], 0.0, MAX_ALTITUDE))
        if self.pos[2] <= 0.0 and self.vel[2] < 0:
            self.vel[2] = 0.0

        # Integrate yaw
        self.yaw += self.yaw_rate_cmd * dt
        self.yaw = (self.yaw + math.pi) % (2 * math.pi) - math.pi

        # Tilt angles dynamically follow velocity
        speed_horiz = math.hypot(self.vel[0], self.vel[1])
        if speed_horiz > 0.1:
            body_fwd = self.vel[0] * math.cos(self.yaw) + self.vel[1] * math.sin(self.yaw)
            body_left = -self.vel[0] * math.sin(self.yaw) + self.vel[1] * math.cos(self.yaw)
            self.pitch = float(np.clip(-body_fwd * 0.05, -0.25, 0.25))
            self.roll = float(np.clip(body_left * 0.05, -0.25, 0.25))
        else:
            self.pitch = 0.0
            self.roll = 0.0

        # Collision check
        _, cable_dist = nearest_cable_point(self.pos, self.scene)
        if cable_dist < 0.45 or inside_tree(self.pos, self.scene):
            self.collided = True
            self.vel[:] = 0.0
            self.v_cmd[:] = 0.0

    def snapshot(self) -> dict[str, Any]:
        cable_pt, cable_dist = nearest_cable_point(self.pos, self.scene)
        return {
            "pos": [round(float(x), 3) for x in self.pos],
            "vel": [round(float(x), 3) for x in self.vel],
            "acc": [round(float(x), 3) for x in self.acc],
            "speed": round(self.speed, 2),
            "altitude": round(self.altitude, 2),
            "yaw": round(self.yaw, 3),
            "rpy": [round(float(x), 3) for x in self.rpy],
            "quat": [round(float(x), 4) for x in self.quat],
            "cable_dist": round(cable_dist, 2),
            "nearest_cable_point": [round(float(x), 3) for x in cable_pt],
            "collided": self.collided,
            "t": round(self.elapsed_time, 2),
        }

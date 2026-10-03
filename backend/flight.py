"""PyFlyt (PyBullet) quadrotor flown manually from keyboard commands.

The drone is a PyFlyt QuadX in flight mode 4 (body-frame forward/left velocity,
yaw rate, altitude hold). The pilot never commands a trajectory; keys only set
velocity and altitude setpoints, and PyFlyt's flight controller handles the rest.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pybullet as p
from PyFlyt.core import Aviary

from backend.sim import Scene

START_POS = np.array([-10.0, -10.0, 0.05])
FLIGHT_MODE = 4  # u, v, vr, z
FORWARD_SPEED = 4.0
STRAFE_SPEED = 3.0
YAW_RATE = 1.2
CLIMB_RATE = 2.0
MAX_ALTITUDE = 60.0
ALTITUDE_LEAD = 1.5
MAX_CATCHUP_STEPS = 12


def keys_to_command(keys: set[str]) -> tuple[float, float, float, float]:
    """Map held keys to (forward m/s, left m/s, yaw rate rad/s, climb m/s)."""
    k = {x.lower() for x in keys}
    fwd = ("w" in k or "arrowup" in k) - ("s" in k or "arrowdown" in k)
    left = ("a" in k) - ("d" in k)
    yaw = ("q" in k or "arrowleft" in k) - ("e" in k or "arrowright" in k)
    climb = (" " in k or "space" in k) - ("shift" in k)
    return FORWARD_SPEED * fwd, STRAFE_SPEED * left, YAW_RATE * yaw, CLIMB_RATE * climb


class FlightSim:
    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.env = Aviary(
            start_pos=START_POS[None, :],
            start_orn=np.zeros((1, 3)),
            render=False,
            drone_type="quadx",
        )
        self._setup()

    def _setup(self) -> None:
        self.obstacle_ids = self._build_world()
        self.env.register_all_new_bodies()
        self.env.set_mode(FLIGHT_MODE)
        # PyFlyt's cf2x altitude loop caps climb at 1 m/s; allow CLIMB_RATE instead
        self.env.drones[0].z_PIDs[1].limits = np.array([CLIMB_RATE])
        self.drone_id = self.env.drones[0].Id
        self.v_cmd = np.zeros(3)
        self.yaw_rate_cmd = 0.0
        self.z_ref = 0.0
        self.collided = False
        self._accum = 0.0
        self._read_state()

    def reset(self) -> None:
        self.env.reset()
        self._setup()

    @property
    def t(self) -> float:
        return float(self.env.elapsed_time)

    def set_keys(self, keys: set[str]) -> None:
        fwd, left, yaw, climb = keys_to_command(keys)
        if climb == 0 and self.v_cmd[2] != 0 and not self.collided:
            # hold the altitude reached when Space/Shift is released
            self.z_ref = float(np.clip(self.pos[2], 0.0, MAX_ALTITUDE))
        self.v_cmd = np.array([fwd, left, climb])
        self.yaw_rate_cmd = yaw

    def advance(self, wall_dt: float) -> None:
        """Run as many PyFlyt control steps as fit in the elapsed wall time."""
        step_dt = float(self.env.step_period)
        self._accum = min(self._accum + wall_dt, MAX_CATCHUP_STEPS * step_dt)
        while self._accum >= step_dt:
            self._accum -= step_dt
            if not self.collided:
                # cap how far the altitude target can run ahead of the drone while climbing
                z = self.pos[2]
                z_ref = np.clip(self.z_ref + self.v_cmd[2] * step_dt, z - ALTITUDE_LEAD, z + ALTITUDE_LEAD)
                self.z_ref = float(np.clip(z_ref, 0.0, MAX_ALTITUDE)) if self.v_cmd[2] else self.z_ref
                self.env.set_setpoint(0, np.array([self.v_cmd[0], self.v_cmd[1], self.yaw_rate_cmd, self.z_ref]))
            self.env.step()
            if not self.collided and self._hit_obstacle():
                self.crash()
        self._read_state()

    def crash(self) -> None:
        """Cut the motors; the drone then falls under PyBullet physics."""
        self.collided = True
        self.v_cmd = np.zeros(3)
        self.yaw_rate_cmd = 0.0
        self.env.set_armed(False)

    def _hit_obstacle(self) -> bool:
        contacts = self.env.contact_array[self.drone_id]
        return any(contacts[i] for i in self.obstacle_ids)

    def _read_state(self) -> None:
        pos, quat = self.env.getBasePositionAndOrientation(self.drone_id)
        lin_vel, _ = self.env.getBaseVelocity(self.drone_id)
        self.pos = np.array(pos, dtype=float)
        self.vel = np.array(lin_vel, dtype=float)
        self.quat = np.array(quat, dtype=float)  # x, y, z, w
        self.rpy = np.array(self.env.getEulerFromQuaternion(quat), dtype=float)

    def pose_json(self) -> dict[str, Any]:
        return {"quat": self.quat.tolist(), "rpy": self.rpy.tolist(), "z_ref": self.z_ref}

    def _build_world(self) -> list[int]:
        """Static collision bodies matching scene_json (rendered by the frontend)."""
        s = self.scene
        ids: list[int] = []

        def box(pos: list[float], half: list[float]) -> None:
            shape = self.env.createCollisionShape(p.GEOM_BOX, halfExtents=half)
            ids.append(self.env.createMultiBody(baseMass=0, baseCollisionShapeIndex=shape, basePosition=pos))

        def cylinder(pos: list[float], radius: float, height: float) -> None:
            shape = self.env.createCollisionShape(p.GEOM_CYLINDER, radius=radius, height=height)
            ids.append(self.env.createMultiBody(baseMass=0, baseCollisionShapeIndex=shape, basePosition=pos))

        h = s.pylon_height
        for x in s.pylon_x:
            for dx in (-0.9, 0.9):
                for dy in (-1.8, 1.8):
                    box([x + dx, dy, h / 2], [0.225, 0.225, h / 2])
            box([x, 0.0, h / 2], [0.175, 0.175, h / 2])
            box([x, 0.0, h - 1.5], [0.275, 3.6, 0.225])
            box([x, 0.0, h - 0.35], [0.275, 2.4, 0.2])
        for ins in s.insulators:
            cylinder(ins["pos"], 0.3, 1.0)

        tx, ty = s.tree_center
        cylinder([tx, ty, s.tree_height / 2], s.tree_radius, s.tree_height)
        canopy = self.env.createCollisionShape(p.GEOM_SPHERE, radius=3.0)
        ids.append(self.env.createMultiBody(baseMass=0, baseCollisionShapeIndex=canopy, basePosition=[tx, ty, s.tree_height + 2]))
        return ids

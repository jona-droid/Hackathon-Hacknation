"""Test isolation: a throw-away data directory and no API keys, set before any backend import."""

import os
import tempfile

os.environ["ROBOT_APPRENTICE_DATA_DIR"] = tempfile.mkdtemp(prefix="apprentice-test-")
os.environ["ANTHROPIC_API_KEY"] = ""  # no network calls: every LLM feature falls back to local logic
os.environ["ELEVENLABS_API_KEY"] = ""

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from backend.sim.detector import EventDetector  # noqa: E402
from backend.sim.drone import DroneSim  # noqa: E402
from backend.sim.flight_log import FlightLog  # noqa: E402
from backend.sim.scene import Scene  # noqa: E402

DT = 1.0 / 60.0


@pytest.fixture
def scene() -> Scene:
    return Scene()


@pytest.fixture
def calm_drone(scene: Scene) -> DroneSim:
    """A drone in still air (deterministic physics)."""
    d = DroneSim(scene)
    d.reset()
    d.wind.mean_speed = 0.0
    return d


def fly(drone: DroneSim, keys: set[str], seconds: float, wind: bool = False) -> None:
    for _ in range(int(round(seconds / DT))):
        drone.set_keys(keys)
        drone.step(DT, wind_enabled=wind)


def fly_path(drone: DroneSim, log: FlightLog, detector: EventDetector, waypoints: list[tuple[float, float, float, float]]) -> None:
    """Kinematic flight through (duration, x, y, z) waypoints, recorded like the sim loop does."""
    for dur, x, y, z in waypoints:
        a, b = drone.pos.copy(), np.array([x, y, z], dtype=float)
        n = max(1, int(round(dur / DT)))
        for i in range(n):
            p = a + (b - a) * (i + 1) / n
            drone.vel = (p - drone.pos) / DT
            drone.pos = p
            drone.elapsed_time += DT
            for e in detector.update(drone, DT):
                log.record_event(e)
            log.record(drone.snapshot())
    drone.vel = np.zeros(3)

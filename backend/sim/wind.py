"""Wind field: a steady wind per flight, stronger with height, plus gusts.

Gusts are an Ornstein-Uhlenbeck process (random but smooth, correlated over a few
seconds), which is the usual cheap stand-in for atmospheric turbulence models.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import numpy as np

REF_HEIGHT_M = 10.0
SHEAR_EXPONENT = 0.15  # power-law wind profile over open grassland
GUST_TIME_CONSTANT_S = 3.0


@dataclass
class Wind:
    mean_speed: float = 0.0  # m/s at REF_HEIGHT_M
    from_heading_deg: float = 0.0  # where the wind blows FROM, same convention as heading_deg
    gust: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def reset(self, rng: random.Random | None = None) -> None:
        rng = rng or random
        self.mean_speed = rng.uniform(2.0, 6.0)
        self.from_heading_deg = rng.uniform(0.0, 360.0)
        self.gust = np.zeros(3)

    @property
    def gust_sigma(self) -> np.ndarray:
        horiz = 0.3 + 0.2 * self.mean_speed
        return np.array([horiz, horiz, 0.35 * horiz])

    def step(self, dt: float) -> None:
        a = math.exp(-dt / GUST_TIME_CONSTANT_S)
        noise = np.random.standard_normal(3)
        self.gust = a * self.gust + math.sqrt(1.0 - a * a) * self.gust_sigma * noise

    def at(self, z: float) -> np.ndarray:
        """Wind velocity (m/s, world frame) at height z. Calm on the ground."""
        if z <= 0.3:
            return np.zeros(3)
        profile = (max(z, 1.0) / REF_HEIGHT_M) ** SHEAR_EXPONENT
        # heading_deg: 0 = +y, 90 = +x; the wind blows towards the opposite of from_heading
        to = math.radians(self.from_heading_deg + 180.0)
        mean = self.mean_speed * profile * np.array([math.sin(to), math.cos(to), 0.0])
        return mean + self.gust * profile

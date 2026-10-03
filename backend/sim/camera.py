from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CameraManager:
    latest_frame_b64: str | None = None
    latest_timestamp: float = field(default_factory=time.time)
    frame_history: list[dict[str, Any]] = field(default_factory=list)

    def set_frame(self, b64_data: str, sim_t: float) -> None:
        # Strip header if data URL format (e.g. data:image/jpeg;base64,...)
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        self.latest_frame_b64 = b64_data
        self.latest_timestamp = sim_t
        self.frame_history.append({"t": sim_t, "has_frame": True})
        if len(self.frame_history) > 100:
            self.frame_history.pop(0)

    def get_latest_frame(self) -> str | None:
        return self.latest_frame_b64

    def reset(self) -> None:
        self.latest_frame_b64 = None
        self.frame_history.clear()

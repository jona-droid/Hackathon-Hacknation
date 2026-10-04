"""Which competence-grid task the pilot is doing, split into episodes with measured signatures.

Rules run on every flight-log sample (10 Hz). A new task must last TASK_SWITCH_S before it
replaces the current one, so one noisy sample does not cut an episode in two; an emergency
switches at once. Side tasks (a defect just found, interference, wind) run alongside the main
one without cutting its episode. The ids match backend/llm/prompts/competence_grid.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

TASK_SWITCH_S = 1.0
MIN_EPISODE_S = 2.0  # shorter episodes are not compared with learned rules
EMERGENCY_HOLD_S = 4.0
DEFECT_HOLD_S = 20.0
RECENT_EPISODES_S = 20.0  # an episode that ended this recently still counts as "just finished"
IMMEDIATE_TASKS = {"emergency"}

# Metrics shown to the observer for each task (all are computed for every episode)
TASK_METRICS = {
    "preflight_takeoff": ["altitude_max", "speed_median"],
    "corridor_transit": ["speed_median", "cable_dist_median", "cable_dz_median"],
    "structure_approach": ["speed_median", "pylon_dist_min", "cable_dz_median"],
    "insulator_inspection": ["insulator_dist_median", "cable_dz_median", "duration_s"],
    "conductor_inspection": ["cable_dist_median", "cable_dz_median", "speed_median"],
    "road_crossing": ["altitude_median", "speed_median"],
    "vegetation": ["tree_dist_min", "speed_median"],
    "emergency": ["cable_dist_min", "speed_median"],
}


@dataclass
class Episode:
    task: str
    start: float
    end: float
    first_row: int
    last_row: int
    target: str | None = None  # insulator id for insulator inspections
    signature: dict[str, float] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


def signature(rows: np.ndarray, cols: tuple[str, ...]) -> dict[str, float]:
    """What telemetry measured during an episode, in the metric names of the competence grid."""
    c = {name: rows[:, i] for i, name in enumerate(cols)}
    out = {
        "duration_s": float(c["t"][-1] - c["t"][0]),
        "speed_median": float(np.median(c["speed"])),
        "altitude_median": float(np.median(c["z"])),
        "altitude_max": float(c["z"].max()),
        "cable_dist_median": float(np.median(c["cable_dist"])),
        "cable_dist_min": float(c["cable_dist"].min()),
        "cable_dz_median": float(np.median(c["cable_dz"])),
        "insulator_dist_median": float(np.median(c["insulator_dist"])),
        "pylon_dist_min": float(c["pylon_dist"].min()),
        "tree_dist_min": float(c["tree_dist"].min()),
    }
    return {k: round(v, 1) for k, v in out.items()}


def classify(s: dict[str, Any], t: float, last_alarm_t: float, approached: bool) -> str | None:
    """Main task for one sample, or None (on the ground, or pausing away from everything)."""
    if s["altitude"] < 0.4:
        return None
    if t - last_alarm_t < EMERGENCY_HOLD_S:
        return "emergency"
    if s["road_dist"] < 4.0:
        return "road_crossing"
    if not approached:
        return "preflight_takeoff"
    if s["insulator_dist"] < 7.0 and s["speed"] < 1.0:
        return "insulator_inspection"
    if s["tree_dist"] < 6.0:
        return "vegetation"
    if s["pylon_dist"] < 10.0:
        return "structure_approach"
    if s["cable_dist"] < 6.0:
        return "conductor_inspection"
    if s["speed"] > 1.5 and s["cable_dist"] < 20.0:
        return "corridor_transit"
    return None


def side_tasks(s: dict[str, Any], t: float, last_defect_t: float) -> list[str]:
    """Tasks that run alongside the main one."""
    out = []
    if t - last_defect_t < DEFECT_HOLD_S:
        out.append("defect_assessment")
    if s.get("compass_interference", 0.0) > 0.3 or (s.get("wind_speed", 0.0) > 5.0 and s["cable_dist"] < 8.0):
        out.append("interference_wind")
    return out


@dataclass
class TaskTracker:
    current: str | None = None
    current_start: float = 0.0
    current_first_row: int = 0
    finished: list[Episode] = field(default_factory=list)
    side: list[str] = field(default_factory=list)
    _candidate: str | None = None
    _candidate_since: float = 0.0
    _candidate_row: int = 0
    _last_alarm_t: float = -1e9
    _last_defect_t: float = -1e9
    _approached: bool = False
    _unreviewed: list[Episode] = field(default_factory=list)

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def on_event(self, ev: dict[str, Any]) -> None:
        t = float(ev.get("t", 0.0))
        if ev.get("type") in ("very_close_cable", "collision"):
            self._last_alarm_t = t
        elif ev.get("type") == "defect_spotted":
            self._last_defect_t = t

    def update(self, row: int, t: float, s: dict[str, Any], rows: list[tuple[float, ...]], cols: tuple[str, ...], nearest: list[str]) -> None:
        if s["pylon_dist"] < 10.0 or s["cable_dist"] < 6.0:  # the launch point is 14 m from the first tower
            self._approached = True
        self.side = side_tasks(s, t, self._last_defect_t) if s["altitude"] >= 0.4 else []
        task = classify(s, t, self._last_alarm_t, self._approached)

        if task == self.current:
            self._candidate = None
            return
        if task != self._candidate:
            self._candidate, self._candidate_since, self._candidate_row = task, t, row
        if task in IMMEDIATE_TASKS or t - self._candidate_since >= TASK_SWITCH_S:
            self._switch(self._candidate_since, self._candidate_row, rows, cols, nearest)

    def _switch(self, t: float, row: int, rows: list[tuple[float, ...]], cols: tuple[str, ...], nearest: list[str]) -> None:
        if self.current is not None and row > self.current_first_row:
            ep = self._episode(self.current, self.current_first_row, row - 1, rows, cols, nearest)
            self.finished.append(ep)
            if ep.duration >= MIN_EPISODE_S:
                self._unreviewed.append(ep)
        self.current, self.current_start, self.current_first_row = self._candidate, t, row
        self._candidate = None

    def _episode(self, task: str, first: int, last: int, rows: list[tuple[float, ...]], cols: tuple[str, ...], nearest: list[str]) -> Episode:
        data = np.asarray(rows[first:last + 1], dtype=float)
        target = None
        if task == "insulator_inspection":
            ids = nearest[first:last + 1]
            target = max(set(ids), key=ids.count)
        return Episode(task, float(data[0, 0]), float(data[-1, 0]), first, last, target, signature(data, cols))

    def current_episode(self, rows: list[tuple[float, ...]], cols: tuple[str, ...], nearest: list[str]) -> Episode | None:
        if self.current is None or not rows or len(rows) <= self.current_first_row:
            return None
        return self._episode(self.current, self.current_first_row, len(rows) - 1, rows, cols, nearest)

    def recent(self, t_now: float) -> list[Episode]:
        return [ep for ep in self.finished[-4:] if t_now - ep.end <= RECENT_EPISODES_S]

    def last_episode(self, task: str) -> Episode | None:
        return next((ep for ep in reversed(self.finished) if ep.task == task), None)

    def pop_unreviewed(self) -> list[Episode]:
        out, self._unreviewed = self._unreviewed, []
        return out

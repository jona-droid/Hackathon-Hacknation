"""Full-flight telemetry history plus the summaries the LLM observer reads.

Every sample since takeoff is kept in memory (10 Hz is ~36k rows per hour), so
patterns that span the whole flight - coming back to an earlier spot, flying
in circles - can be detected here in Python instead of asking the LLM to do
geometry on thousands of raw points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.sim.tasks import Episode, TaskTracker

# Columns kept per sample; the window table sent to the LLM uses the same order.
COLUMNS = (
    "t", "x", "y", "z", "speed", "vz", "acc", "heading_deg",
    "cable_dist", "cable_dz", "pylon_dist", "tree_dist", "insulator_dist", "road_dist",
    "wind_speed", "compass_interference",
)

REVISIT_RADIUS_M = 4.0
REVISIT_MIN_GAP_S = 20.0
CIRCLE_LOOKBACK_S = 20.0
CIRCLE_MIN_SWEEP_DEG = 300.0
CIRCLE_MAX_RADIUS_M = 30.0
OVERVIEW_MAX_POINTS = 40
MAX_WINDOW_EVENTS = 10


@dataclass
class FlightLog:
    record_hz: float = 10.0
    rows: list[tuple[float, ...]] = field(default_factory=list)
    insulators: list[str] = field(default_factory=list)  # nearest insulator per row
    events: list[dict[str, Any]] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)
    tasks: TaskTracker = field(default_factory=TaskTracker)
    _last_t: float = -1e9

    def reset(self) -> None:
        self.rows.clear()
        self.insulators.clear()
        self.events.clear()
        self.conditions = {}
        self.tasks.reset()
        self._last_t = -1e9

    def record(self, state: dict[str, Any]) -> None:
        t = float(state["t"])
        if t < self._last_t:  # sim was reset
            self.reset()
        if t - self._last_t < 1.0 / self.record_hz:
            return
        self._last_t = t
        x, y, z = state["pos"]
        self.rows.append((
            t, x, y, z, state["speed"], state["vertical_speed"], state["acc_magnitude"], state["heading_deg"],
            state["cable_dist"], state["cable_dz"], state["pylon_dist"], state["tree_dist"],
            state["insulator_dist"], state["road_dist"],
            state["wind_speed"], state["compass_interference"],
        ))
        self.conditions = {
            "wind_from_deg": state["wind_from_deg"],
            "wind_speed_here": state["wind_speed"],
            "drone_upwind_of_nearest_cable": _upwind_of_cable(state),
            "position_hold": state["position_hold"],
        }
        self.insulators.append(state["nearest_insulator"])
        self.tasks.update(len(self.rows) - 1, t, state, self.rows, COLUMNS, self.insulators)

    def record_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self.tasks.on_event(event)

    def current_episode(self) -> Episode | None:
        return self.tasks.current_episode(self.rows, COLUMNS, self.insulators)

    def latest_episode(self, task: str) -> Episode | None:
        """The ongoing episode of `task`, else the last finished one."""
        cur = self.current_episode()
        return cur if cur and cur.task == task else self.tasks.last_episode(task)

    def find_episode(self, task: str, start: float) -> Episode | None:
        """The episode a question was asked about, with all its samples so far."""
        cur = self.current_episode()
        if cur and cur.task == task and cur.start == start:
            return cur
        found = next((ep for ep in reversed(self.tasks.finished) if ep.task == task and ep.start == start), None)
        return found or self.latest_episode(task)

    @property
    def duration(self) -> float:
        return self.rows[-1][0] - self.rows[0][0] if self.rows else 0.0

    def _array(self) -> np.ndarray:
        return np.asarray(self.rows, dtype=float)

    # ---- LLM context -------------------------------------------------------

    def observer_context(self, window_s: float, window_hz: float = 4.0) -> dict[str, Any]:
        """Everything the observer needs: last `window_s` seconds in detail + whole-flight patterns."""
        if not self.rows:
            return {"status": "no telemetry yet"}
        data = self._array()
        t_now = data[-1, 0]
        win = data[data[:, 0] >= t_now - window_s]
        step = max(1, int(round(self.record_hz / window_hz)))

        return {
            "flight_time_s": round(t_now, 1),
            "conditions_now": self.conditions,
            "last_window": {
                "seconds": window_s,
                "columns": list(COLUMNS),
                "rows": [[round(float(v), 2) for v in row] for row in win[::step]],
                "stats": _window_stats(win),
                "events": [_compact_event(e) for e in self.events if float(e.get("t", 0.0)) >= t_now - window_s][-MAX_WINDOW_EVENTS:],
            },
            "whole_flight": {
                "totals": _totals(data),
                "revisit": _revisit(data),
                "circling": _circling(data),
                "insulators_approached": _insulators_approached(data, self.insulators),
                "defects_spotted": [
                    {"t": round(float(e["t"]), 1), "defect": e["label"], "on": e["target"]}
                    for e in self.events if e.get("type") == "defect_spotted"
                ],
                "path_overview": _overview(data),
                "earlier_events": [
                    {"t": round(float(e.get("t", 0.0)), 1), "type": e.get("type")}
                    for e in self.events if float(e.get("t", 0.0)) < t_now - window_s
                ][-25:],
            },
        }


def _upwind_of_cable(state: dict[str, Any]) -> bool | None:
    """True if the wind blows from the drone towards the nearest cable (a gust pushes it into the line)."""
    wind = np.asarray(state["wind"][:2], dtype=float)
    if state["cable_dist"] > 15.0 or np.linalg.norm(wind) < 0.5:
        return None
    to_cable = np.asarray(state["nearest_cable_point"][:2], dtype=float) - np.asarray(state["pos"][:2], dtype=float)
    return bool(np.dot(wind, to_cable) > 0)


def _compact_event(e: dict[str, Any]) -> dict[str, Any]:
    """Event without position arrays (already in the rows), numbers rounded."""
    return {k: round(v, 1) if isinstance(v, float) else v for k, v in e.items() if not isinstance(v, (list, tuple))}


def _col(name: str) -> int:
    return COLUMNS.index(name)


def _window_stats(win: np.ndarray) -> dict[str, Any]:
    xyz = win[:, 1:4]
    path = float(np.sum(np.linalg.norm(np.diff(xyz, axis=0), axis=1))) if len(win) > 1 else 0.0
    heading = np.unwrap(np.radians(win[:, _col("heading_deg")]))
    speed = win[:, _col("speed")]
    dt = float(np.mean(np.diff(win[:, 0]))) if len(win) > 1 else 0.0
    return {
        "speed_mean": round(float(speed.mean()), 2),
        "speed_max": round(float(speed.max()), 2),
        "acc_max": round(float(win[:, _col("acc")].max()), 2),
        "altitude_change": round(float(win[-1, 3] - win[0, 3]), 2),
        "path_length": round(path, 2),
        "net_displacement": round(float(np.linalg.norm(xyz[-1] - xyz[0])), 2),
        "heading_change_deg": round(float(np.degrees(heading[-1] - heading[0])), 1),
        "hover_time_s": round(float(np.sum(speed < 0.35)) * dt, 1),
        "min_cable_dist": round(float(win[:, _col("cable_dist")].min()), 2),
        "min_pylon_dist": round(float(win[:, _col("pylon_dist")].min()), 2),
        "min_tree_dist": round(float(win[:, _col("tree_dist")].min()), 2),
        "min_insulator_dist": round(float(win[:, _col("insulator_dist")].min()), 2),
        "max_compass_interference": round(float(win[:, _col("compass_interference")].max()), 2),
    }


def _totals(data: np.ndarray) -> dict[str, Any]:
    xyz = data[:, 1:4]
    path = float(np.sum(np.linalg.norm(np.diff(xyz, axis=0), axis=1))) if len(data) > 1 else 0.0
    i_min = int(np.argmin(data[:, _col("cable_dist")]))
    return {
        "distance_flown_m": round(path, 1),
        "max_altitude_m": round(float(data[:, 3].max()), 1),
        "max_speed": round(float(data[:, _col("speed")].max()), 2),
        "closest_cable_approach": {
            "dist": round(float(data[i_min, _col("cable_dist")]), 2),
            "t": round(float(data[i_min, 0]), 1),
        },
    }


def _revisit(data: np.ndarray) -> dict[str, Any] | None:
    """Is the drone back near a spot it already visited at least REVISIT_MIN_GAP_S ago?"""
    now = data[-1]
    past = data[data[:, 0] <= now[0] - REVISIT_MIN_GAP_S]
    if not len(past):
        return None
    d = np.linalg.norm(past[:, 1:4] - now[1:4], axis=1)
    close = d < REVISIT_RADIUS_M
    if not close.any():
        return None
    # separate visits: close samples split by gaps longer than 2 s
    times = past[close, 0]
    starts = [float(times[0])] + [float(b) for a, b in zip(times[:-1], times[1:]) if b - a > 2.0]
    return {
        "returned_to_earlier_position": True,
        "previous_visit_times_s": [round(s, 1) for s in starts][-5:],
        "visit_count_including_now": len(starts) + 1,
        "closest_distance_m": round(float(d.min()), 2),
    }


def _circling(data: np.ndarray) -> dict[str, Any] | None:
    """Total turn of the flight direction over the recent path, with the path staying compact."""
    recent = data[data[:, 0] >= data[-1, 0] - CIRCLE_LOOKBACK_S]
    xy = recent[:, 1:3]
    step = np.diff(xy, axis=0)
    step = step[np.linalg.norm(step, axis=1) > 0.05]  # ignore hovering jitter
    if len(step) < 20:
        return None
    sweep = float(np.degrees(np.sum(np.diff(np.unwrap(np.arctan2(step[:, 1], step[:, 0]))))))
    centre = xy.mean(axis=0)
    radius = float(np.linalg.norm(xy - centre, axis=1).mean())
    if abs(sweep) < CIRCLE_MIN_SWEEP_DEG or radius > CIRCLE_MAX_RADIUS_M:
        return None
    return {
        "circling": True,
        "laps": round(abs(sweep) / 360.0, 1),
        "direction": "counter-clockwise" if sweep > 0 else "clockwise",
        "centre_xy": [round(float(c), 1) for c in centre],
        "radius_m": round(radius, 1),
        "over_last_s": CIRCLE_LOOKBACK_S,
    }


def _insulators_approached(data: np.ndarray, nearest: list[str]) -> list[dict[str, Any]]:
    """Closest approach to each insulator the drone came within 8 m of."""
    out: dict[str, dict[str, Any]] = {}
    dist = data[:, _col("insulator_dist")]
    for i in np.flatnonzero(dist < 8.0):
        ins = nearest[i]
        if ins not in out or dist[i] < out[ins]["closest_m"]:
            out[ins] = {"insulator": ins, "closest_m": round(float(dist[i]), 2), "t": round(float(data[i, 0]), 1)}
    return list(out.values())


def _overview(data: np.ndarray) -> dict[str, Any]:
    step = max(1, math.ceil(len(data) / OVERVIEW_MAX_POINTS))
    pts = data[::step]
    return {
        "columns": ["t", "x", "y", "z", "speed"],
        "rows": [[round(float(r[0]), 1), round(float(r[1]), 1), round(float(r[2]), 1), round(float(r[3]), 1), round(float(r[4]), 1)] for r in pts],
    }

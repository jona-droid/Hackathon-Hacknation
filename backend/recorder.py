from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "sessions"


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


@dataclass
class SessionRecorder:
    session_id: str
    mode: str
    root: Path
    telemetry_path: Path = field(init=False)
    events_path: Path = field(init=False)
    transcript_path: Path = field(init=False)
    meta_path: Path = field(init=False)
    started_at: float = field(default_factory=time.time)
    _last_telemetry_t: float = -999.0

    def __post_init__(self) -> None:
        self.telemetry_path = self.root / "telemetry.jsonl"
        self.events_path = self.root / "events.jsonl"
        self.transcript_path = self.root / "transcript.jsonl"
        self.meta_path = self.root / "meta.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "mode": self.mode,
                    "started_at": self.started_at,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def record_state(self, t: float, state: dict[str, Any], hz: float = 10.0) -> None:
        if t - self._last_telemetry_t < (1.0 / hz):
            return
        self._last_telemetry_t = t
        _append_jsonl(self.telemetry_path, {"t": t, **state})

    def record_event(self, event: dict[str, Any]) -> None:
        _append_jsonl(self.events_path, event)

    def record_transcript(self, item: dict[str, Any]) -> None:
        _append_jsonl(self.transcript_path, item)

    def stop(self) -> None:
        meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
        meta["stopped_at"] = time.time()
        self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def start_session(mode: str) -> SessionRecorder:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session_id = str(uuid.uuid4())
    root = DATA_DIR / session_id
    return SessionRecorder(session_id=session_id, mode=mode, root=root)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def summarize_for_llm(session_id: str) -> dict[str, Any]:
    root = DATA_DIR / session_id
    telemetry = load_jsonl(root / "telemetry.jsonl")
    events = load_jsonl(root / "events.jsonl")
    transcript = load_jsonl(root / "transcript.jsonl")

    by_second: dict[int, dict[str, Any]] = {}
    for row in telemetry:
        sec = int(row.get("t", 0.0))
        by_second[sec] = {
            "t": sec,
            "pos": row.get("pos"),
            "vel": row.get("vel"),
            "cable_dist": row.get("cable_dist"),
        }
    telemetry_summary = [by_second[k] for k in sorted(by_second.keys())]
    return {
        "session_id": session_id,
        "telemetry_1hz": telemetry_summary,
        "events": events,
        "transcript": transcript,
    }


def replay_state(session_id: str, t: float) -> dict[str, Any] | None:
    rows = load_jsonl(DATA_DIR / session_id / "telemetry.jsonl")
    if not rows:
        return None
    return min(rows, key=lambda r: abs(float(r.get("t", 0.0)) - t))


def append_transcript(session_id: str, item: dict[str, Any]) -> None:
    root = DATA_DIR / session_id
    root.mkdir(parents=True, exist_ok=True)
    _append_jsonl(root / "transcript.jsonl", item)

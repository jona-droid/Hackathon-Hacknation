from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.core.config import SESSIONS_DIR


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@dataclass
class SessionRecorder:
    session_id: str
    mode: str
    root: Path
    telemetry_path: Path = field(init=False)
    events_path: Path = field(init=False)
    transcript_path: Path = field(init=False)
    meta_path: Path = field(init=False)
    summary_path: Path = field(init=False)
    frames_dir: Path = field(init=False)
    started_at: float = field(default_factory=time.time)
    _last_telemetry_t: float = -999.0

    def __post_init__(self) -> None:
        self.telemetry_path = self.root / "telemetry.jsonl"
        self.events_path = self.root / "events.jsonl"
        self.transcript_path = self.root / "transcript.jsonl"
        self.meta_path = self.root / "meta.json"
        self.summary_path = self.root / "summary.json"
        self.frames_dir = self.root / "frames"

        self.root.mkdir(parents=True, exist_ok=True)
        self.frames_dir.mkdir(parents=True, exist_ok=True)

        self.meta_path.write_text(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "mode": self.mode,
                    "started_at": self.started_at,
                    "stopped_at": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def record_state(self, t: float, state: dict[str, Any], hz: float = 10.0) -> None:
        if t - self._last_telemetry_t < (1.0 / hz):
            return
        self._last_telemetry_t = t
        _append_jsonl(self.telemetry_path, {"t": round(t, 2), **state})

    def record_event(self, event: dict[str, Any]) -> None:
        _append_jsonl(self.events_path, event)

    def record_transcript(self, item: dict[str, Any]) -> None:
        _append_jsonl(self.transcript_path, item)

    def save_frame(self, t: float, b64_frame: str) -> str:
        try:
            if "," in b64_frame:
                b64_frame = b64_frame.split(",", 1)[1]
            data = base64.b64decode(b64_frame)
            filename = f"frame_{int(t*10):05d}.jpg"
            frame_path = self.frames_dir / filename
            frame_path.write_bytes(data)
            return filename
        except Exception:
            return ""

    def save_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_summary(self) -> dict[str, Any] | None:
        if self.summary_path.exists():
            return json.loads(self.summary_path.read_text(encoding="utf-8"))
        return None

    def stop(self) -> None:
        if self.meta_path.exists():
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            meta["stopped_at"] = time.time()
            self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def start_session(mode: str) -> SessionRecorder:
    session_id = f"{mode}-{uuid.uuid4().hex[:8]}"
    root = SESSIONS_DIR / session_id
    return SessionRecorder(session_id=session_id, mode=mode, root=root)


def get_session(session_id: str) -> SessionRecorder | None:
    root = SESSIONS_DIR / session_id
    if not root.exists():
        return None
    meta_path = root / "meta.json"
    mode = "expert"
    if meta_path.exists():
        try:
            mode = json.loads(meta_path.read_text(encoding="utf-8")).get("mode", "expert")
        except Exception:
            pass
    return SessionRecorder(session_id=session_id, mode=mode, root=root)


def list_sessions() -> list[dict[str, Any]]:
    out = []
    if not SESSIONS_DIR.exists():
        return out
    for p in sorted(SESSIONS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir():
            meta_path = p / "meta.json"
            meta: dict[str, Any] = {"session_id": p.name, "mode": "unknown"}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            meta["has_summary"] = (p / "summary.json").exists()
            out.append(meta)
    return out


def replay_state(session_id: str, t: float) -> dict[str, Any] | None:
    rows = load_jsonl(SESSIONS_DIR / session_id / "telemetry.jsonl")
    if not rows:
        return None
    return min(rows, key=lambda r: abs(float(r.get("t", 0.0)) - t))

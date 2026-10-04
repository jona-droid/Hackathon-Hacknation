from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from fastapi import WebSocket

from backend.llm.advisor import Advisor
from backend.llm.comparator import FlightComparator
from backend.llm.knowledge_manager import KnowledgeManager
from backend.llm.observer import FlightObserver
from backend.llm.summarizer import FlightSummarizer
from backend.sim.camera import CameraManager
from backend.sim.defects import generate_defects
from backend.sim.detector import EventDetector
from backend.sim.drone import DroneSim
from backend.sim.flight_log import FlightLog
from backend.sim.scene import Scene
from backend.storage.session_recorder import SessionRecorder


@dataclass
class SimRuntime:
    scene: Scene = field(default_factory=Scene)
    drone: DroneSim = field(init=False)
    detector: EventDetector = field(init=False)
    camera: CameraManager = field(default_factory=CameraManager)
    flight_log: FlightLog = field(default_factory=FlightLog)
    mode: str = "expert"  # "expert" | "novice"
    keys_down: set[str] = field(default_factory=set)
    clients: set[WebSocket] = field(default_factory=set)
    recorder: SessionRecorder | None = None

    # LLM modules
    observer: FlightObserver = field(default_factory=FlightObserver)
    knowledge_manager: KnowledgeManager = field(default_factory=KnowledgeManager)
    advisor: Advisor = field(default_factory=Advisor)
    summarizer: FlightSummarizer = field(default_factory=FlightSummarizer)
    comparator: FlightComparator = field(default_factory=FlightComparator)

    # Active dialogue states
    latest_question: dict[str, Any] | None = None
    latest_advice: dict[str, Any] | None = None
    last_question_time: float = -999.0
    last_advice_time: float = -999.0
    # observer questions: [{"t", "question", "answer" (None until answered), "slot", "kind", "deviation"}]
    qa_history: list[dict[str, Any]] = field(default_factory=list)
    observer_busy: bool = False
    # follow-up for a vague answer, asked at the next calm moment: {"t", "question", "slot"}
    pending_follow_up: dict[str, Any] | None = None
    # pilot just did a task differently from a learned rule: {"t", "slot", "expected", "now", "rule"}
    pending_deviation: dict[str, Any] | None = None
    # bumped on every session start/stop; LLM results from an older epoch are discarded
    session_epoch: int = 0

    def __post_init__(self) -> None:
        self.scene.defects = generate_defects(self.scene)
        self.drone = DroneSim(self.scene)
        self.detector = EventDetector(self.scene)

    @property
    def session_active(self) -> bool:
        return self.recorder is not None

    def end_session(self) -> SessionRecorder | None:
        """Stop all AI activity immediately; in-flight LLM results will be discarded."""
        recorder, self.recorder = self.recorder, None
        self.session_epoch += 1
        self.latest_question = None
        self.latest_advice = None
        self.pending_follow_up = None
        self.pending_deviation = None
        return recorder

    def reset(self, mode: str = "expert") -> None:
        self.session_epoch += 1
        self.mode = mode
        self.scene.defects = generate_defects(self.scene)  # new damage to find on every flight
        self.keys_down.clear()
        self.drone.reset()
        self.detector.reset()
        self.camera.reset()
        self.flight_log.reset()
        self.qa_history.clear()
        self.observer_busy = False
        self.pending_follow_up = None
        self.pending_deviation = None
        self.latest_question = None
        self.latest_advice = None
        self.last_question_time = -999.0
        self.last_advice_time = -999.0


runtime = SimRuntime()

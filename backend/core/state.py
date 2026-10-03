from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from fastapi import WebSocket

from backend.llm.advisor import Advisor
from backend.llm.comparator import FlightComparator
from backend.llm.knowledge_manager import KnowledgeManager
from backend.llm.questioner import Questioner
from backend.llm.summarizer import FlightSummarizer
from backend.sim.camera import CameraManager
from backend.sim.detector import EventDetector
from backend.sim.drone import DroneSim
from backend.sim.scene import Scene
from backend.storage.session_recorder import SessionRecorder


@dataclass
class SimRuntime:
    scene: Scene = field(default_factory=Scene)
    drone: DroneSim = field(init=False)
    detector: EventDetector = field(init=False)
    camera: CameraManager = field(default_factory=CameraManager)
    mode: str = "expert"  # "expert" | "novice"
    keys_down: set[str] = field(default_factory=set)
    clients: set[WebSocket] = field(default_factory=set)
    recorder: SessionRecorder | None = None

    # LLM modules
    questioner: Questioner = field(default_factory=Questioner)
    knowledge_manager: KnowledgeManager = field(default_factory=KnowledgeManager)
    advisor: Advisor = field(default_factory=Advisor)
    summarizer: FlightSummarizer = field(default_factory=FlightSummarizer)
    comparator: FlightComparator = field(default_factory=FlightComparator)

    # Active dialogue states
    latest_question: dict[str, Any] | None = None
    latest_advice: dict[str, Any] | None = None
    last_question_time: float = -999.0
    last_advice_time: float = -999.0

    def __post_init__(self) -> None:
        self.drone = DroneSim(self.scene)
        self.detector = EventDetector(self.scene)

    def reset(self, mode: str = "expert") -> None:
        self.mode = mode
        self.keys_down.clear()
        self.drone.reset()
        self.detector.reset()
        self.camera.reset()
        self.latest_question = None
        self.latest_advice = None
        self.last_question_time = -999.0
        self.last_advice_time = -999.0


runtime = SimRuntime()

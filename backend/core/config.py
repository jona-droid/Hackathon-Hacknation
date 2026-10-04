from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

# API Keys & IDs
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2")  # transcribes spoken answers
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel default

# Storage Paths
DATA_DIR = ROOT_DIR / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
KNOWLEDGE_FILE = KNOWLEDGE_DIR / "knowledge.md"
SESSIONS_DIR = DATA_DIR / "sessions"
COMPARISONS_DIR = DATA_DIR / "comparisons"

KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
COMPARISONS_DIR.mkdir(parents=True, exist_ok=True)

# Simulation & Telemetry Config
SIM_HZ = 60.0
BROADCAST_HZ = 30.0
RECORD_HZ = 10.0
MIN_CABLE_SAFE_DISTANCE = 2.5
MAX_APPROACH_SPEED = 2.0

# LLM flight observer: every OBSERVER_INTERVAL_S it sends the last OBSERVER_WINDOW_S
# of telemetry (plus whole-flight patterns) to Claude, which decides whether to ask
# the operator a question.
OBSERVER_MODEL = os.getenv("OBSERVER_MODEL", "claude-haiku-4-5")
# Live novice tutor: frequent calls during the flight, so the fast model
TUTOR_MODEL = os.getenv("TUTOR_MODEL", "claude-haiku-4-5")
OBSERVER_INTERVAL_S = float(os.getenv("OBSERVER_INTERVAL_S", "3.0"))
OBSERVER_WINDOW_S = float(os.getenv("OBSERVER_WINDOW_S", "5.0"))
QUESTION_COOLDOWN_S = float(os.getenv("QUESTION_COOLDOWN_S", "15.0"))
UNANSWERED_QUESTION_TIMEOUT_S = float(os.getenv("UNANSWERED_QUESTION_TIMEOUT_S", "30.0"))

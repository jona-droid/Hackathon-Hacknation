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
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")
ELEVENLABS_TUTOR_AGENT_ID = os.getenv("ELEVENLABS_TUTOR_AGENT_ID", "")
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
MAX_APPROACH_SPEED = 1.0

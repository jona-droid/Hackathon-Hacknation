from __future__ import annotations

import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.state import runtime
from backend.llm import usage
from backend.storage.comparison_store import list_comparisons
from backend.storage.session_recorder import (
    get_session,
    list_sessions,
    replay_state,
    start_session,
)

router = APIRouter(tags=["Sessions"])


class StartSessionRequest(BaseModel):
    mode: str = "expert"  # "expert" | "novice"
    voice: str = "classic"  # "agent" = ElevenAgents words and speaks; "classic" = Claude + ElevenLabs TTS/STT


class GuardianRequest(BaseModel):
    enabled: bool


class CompareRequest(BaseModel):
    expert_session_id: str
    novice_session_id: str


@router.post("/session/start")
async def session_start(payload: StartSessionRequest) -> dict[str, Any]:
    mode = payload.mode.lower()
    if mode not in {"expert", "novice", "tutor"}:
        raise HTTPException(status_code=400, detail="Mode must be 'expert' or 'novice'")
    runtime.reset(mode=mode, voice_agent=payload.voice == "agent")
    runtime.recorder = start_session(mode)
    runtime.recorder.update_meta(voice="elevenlabs_agent" if runtime.voice_agent else "classic")
    # Ground truth for the flight summary / comparison: which defects existed on this flight
    runtime.recorder.record_event({"type": "defects_placed", "t": 0.0, "defects": runtime.scene.defects})
    return {
        "session_id": runtime.recorder.session_id,
        "mode": mode,
        "voice": "agent" if runtime.voice_agent else "classic",
        "message": f"Started {mode} flight session",
    }


@router.post("/session/stop")
async def session_stop() -> dict[str, Any]:
    # End the session first so the observer and tutor stop before the (slow) summary call
    recorder = runtime.end_session()
    if recorder is None:
        raise HTTPException(status_code=400, detail="No active session to stop")

    sid = recorder.session_id
    mode = recorder.mode
    recorder.stop()

    # Generate flight summary
    try:
        summary = await asyncio.to_thread(runtime.summarizer.generate_summary, sid, usage.snapshot()["flight"])
    except Exception as e:
        summary = {"session_id": sid, "error": str(e)}

    return {
        "session_id": sid,
        "mode": mode,
        "summary": summary,
    }


@router.post("/guardian")
async def set_guardian(payload: GuardianRequest) -> dict[str, Any]:
    """Switch the novice-mode Guardian (predictive collision avoidance) on or off."""
    runtime.guardian_enabled = payload.enabled
    return {"enabled": runtime.guardian_enabled, "armed": runtime.guardian_active}


@router.get("/ai/usage")
async def ai_usage() -> dict[str, Any]:
    """Claude calls, tokens and estimated cost: this flight and since the server started."""
    return usage.snapshot()


@router.get("/sessions")
async def get_all_sessions() -> dict[str, Any]:
    return {"sessions": list_sessions()}


@router.get("/session/{session_id}/summary")
async def session_summary(session_id: str) -> dict[str, Any]:
    rec = get_session(session_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Session not found")
    summary = rec.get_summary()
    if not summary:
        summary = await asyncio.to_thread(runtime.summarizer.generate_summary, session_id)
    return {"session_id": session_id, "summary": summary}


@router.get("/session/{session_id}/replay")
async def session_replay(session_id: str, t: float) -> dict[str, Any]:
    state = replay_state(session_id, t)
    if state is None:
        raise HTTPException(status_code=404, detail="No telemetry available for this session/timestamp")
    return state


@router.post("/session/compare")
async def compare_sessions(payload: CompareRequest) -> dict[str, Any]:
    exp_id = payload.expert_session_id
    nov_id = payload.novice_session_id
    if not exp_id or not nov_id:
        raise HTTPException(status_code=400, detail="Both expert_session_id and novice_session_id are required")

    comparison = await asyncio.to_thread(runtime.comparator.compare, exp_id, nov_id)
    return {"ok": True, "comparison": comparison}


@router.get("/comparisons")
async def get_comparisons() -> dict[str, Any]:
    return {"comparisons": list_comparisons()}

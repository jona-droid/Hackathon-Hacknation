from __future__ import annotations

import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.state import runtime
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


class CompareRequest(BaseModel):
    expert_session_id: str
    novice_session_id: str


@router.post("/session/start")
async def session_start(payload: StartSessionRequest) -> dict[str, Any]:
    mode = payload.mode.lower()
    if mode not in {"expert", "novice", "tutor"}:
        raise HTTPException(status_code=400, detail="Mode must be 'expert' or 'novice'")
    runtime.reset(mode=mode)
    runtime.recorder = start_session(mode)
    return {
        "session_id": runtime.recorder.session_id,
        "mode": mode,
        "message": f"Started {mode} flight session",
    }


@router.post("/session/stop")
async def session_stop() -> dict[str, Any]:
    if not runtime.recorder:
        raise HTTPException(status_code=400, detail="No active session to stop")

    sid = runtime.recorder.session_id
    mode = runtime.recorder.mode
    runtime.recorder.stop()

    # Generate flight summary
    try:
        summary = await asyncio.to_thread(runtime.summarizer.generate_summary, sid)
    except Exception as e:
        summary = {"session_id": sid, "error": str(e)}

    runtime.recorder = None
    return {
        "session_id": sid,
        "mode": mode,
        "summary": summary,
    }


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

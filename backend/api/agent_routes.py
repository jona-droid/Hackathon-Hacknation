"""ElevenAgents routes: the browser talks to the ElevenLabs agents; the backend only hands out
signed URLs and receives what the agents report (their wording, the tutor's verdicts)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.privacy import redact
from backend.core.state import runtime
from backend.llm import eleven_agents
from backend.llm.teach import expert_moment

router = APIRouter(tags=["ElevenAgents"])


class AskedRequest(BaseModel):
    cue_id: str
    question: str


class AgentVerdict(BaseModel):
    id: str
    verdict: str
    answer: str = ""


@router.get("/agent/status")
async def agent_status() -> dict[str, Any]:
    """Whether the ElevenAgents voice can be used (creates or updates the agents on the first call)."""
    return await asyncio.to_thread(eleven_agents.status)


@router.get("/agent/session")
async def agent_session(role: str) -> dict[str, Any]:
    """A signed URL for one conversation with the interviewer or the tutor, and its dynamic variables."""
    if role not in eleven_agents.ROLES:
        raise HTTPException(status_code=400, detail="role must be interviewer or tutor")
    if not eleven_agents.enabled():
        raise HTTPException(status_code=503, detail="ElevenAgents is off on the server")
    try:
        url = await asyncio.to_thread(eleven_agents.signed_url, role)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ElevenLabs agent unavailable: {e}")
    variables = {"expert_rules": eleven_agents.expert_rules_text()} if role == "tutor" else {}
    return {"signed_url": url, "dynamic_variables": variables}


@router.post("/agent/asked")
async def agent_asked(payload: AskedRequest) -> dict[str, Any]:
    """The interviewer agent worded a cued question: keep its words for the answer and the transcript."""
    qa = next((q for q in reversed(runtime.qa_history) if q.get("cue_id") == payload.cue_id), None)
    if qa is None:
        return {"ok": False}
    qa["question"] = payload.question
    if runtime.latest_question and runtime.latest_question.get("cue_id") == payload.cue_id:
        runtime.latest_question["question"] = payload.question
    if runtime.recorder:
        runtime.recorder.record_transcript({"role": "apprentice_model", "text": payload.question, "t": qa["t"],
                                            "slot": qa.get("slot"), "kind": qa.get("kind"), "voice": "elevenlabs_agent"})
    return {"ok": True}


@router.post("/teach/agent-verdict")
async def teach_agent_verdict(payload: AgentVerdict) -> dict[str, Any]:
    """The tutor agent judged the novice's prediction (log_prediction tool): recorded for the mastery
    report; returns the expert's moment to replay when the novice was not right."""
    q = runtime.open_prediction
    if not q or q["id"] != payload.id:
        raise HTTPException(status_code=409, detail="This question is no longer open")
    verdict = payload.verdict if payload.verdict in ("right", "partly", "wrong") else "wrong"
    runtime.open_prediction = None
    runtime.last_advice_time = time.time()
    answer = redact(payload.answer)
    if runtime.recorder:
        runtime.recorder.record_transcript({"role": "novice_operator", "kind": "prediction", "text": answer, "t": q["t"]})
        runtime.recorder.record_transcript({"role": "tutor_quiz", "slot": q["slot"], "kind": q["kind"], "topic": q["topic"],
                                            "question": q["question"], "answer": answer, "verdict": verdict,
                                            "voice": "elevenlabs_agent", "t": runtime.drone.elapsed_time})
    return {"ok": True, "verdict": verdict, "replay": expert_moment(q["topic"]) if verdict != "right" else None}

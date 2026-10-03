from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from backend.core.config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
from backend.api.observer import observe_once
from backend.core.state import runtime

logger = logging.getLogger("robot-apprentice.dialogue")
router = APIRouter(tags=["Dialogue & Voice"])


class AnswerRequest(BaseModel):
    session_id: str | None = None
    question: str
    answer: str
    context: dict[str, Any] | None = None


class AdviseRequest(BaseModel):
    session_id: str | None = None
    query: str | None = None
    event: dict[str, Any] | None = None


class FrameUploadRequest(BaseModel):
    t: float
    frame_b64: str


class TTSRequest(BaseModel):
    text: str
    voice_id: str | None = None


@router.post("/dialogue/trigger-question")
async def trigger_question() -> dict[str, Any]:
    """Operator pressed "Ask Question Now": the observer must ask about the recent flight."""
    if not runtime.observer.is_available:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY is not configured on the server.")
    if not runtime.session_active:
        raise HTTPException(status_code=409, detail="No flight in progress: press Start Flight first.")
    if runtime.observer_busy:
        raise HTTPException(status_code=409, detail="The observer is already thinking; try again in a moment.")
    payload = await observe_once(force=True)
    if payload is None:
        if not runtime.session_active:
            raise HTTPException(status_code=409, detail="The flight ended before the question was ready.")
        raise HTTPException(status_code=502, detail="The observer did not produce a question.")
    return payload


@router.post("/dialogue/answer")
async def receive_operator_answer(payload: AnswerRequest) -> dict[str, Any]:
    if not payload.answer.strip():
        raise HTTPException(status_code=400, detail="Answer cannot be empty")

    telemetry = runtime.drone.snapshot()
    for qa in reversed(runtime.qa_history):
        if qa["answer"] is None:
            qa["answer"] = payload.answer
            break
    context = payload.context or {
        "telemetry": telemetry,
        "event": runtime.latest_question.get("event") if runtime.latest_question else {"type": "inspection"},
    }

    result = await asyncio.to_thread(
        runtime.knowledge_manager.process_operator_response,
        question=payload.question,
        answer=payload.answer,
        context=context,
    )

    # Record to session transcript
    if runtime.recorder:
        runtime.recorder.record_transcript({
            "role": "expert_operator",
            "text": payload.answer,
            "t": telemetry["t"],
            "distilled_insight": result.get("insight"),
        })

    return {"ok": True, **result}


@router.post("/dialogue/advise")
async def request_advice(payload: AdviseRequest) -> dict[str, Any]:
    telemetry = runtime.drone.snapshot()
    frame = runtime.camera.get_latest_frame()

    advice = await asyncio.to_thread(
        runtime.advisor.advise,
        telemetry=telemetry,
        image_b64=frame,
        event=payload.event,
        novice_query=payload.query,
    )
    runtime.latest_advice = advice

    # Record to transcript
    if runtime.recorder:
        if payload.query:
            runtime.recorder.record_transcript({
                "role": "novice_operator",
                "text": payload.query,
                "t": telemetry["t"],
            })
        runtime.recorder.record_transcript({
            "role": "tutor_model",
            "text": advice.get("speech", ""),
            "t": telemetry["t"],
            "advice": advice,
        })

    return advice


@router.post("/session/{session_id}/frame")
async def upload_frame(session_id: str, payload: FrameUploadRequest) -> dict[str, Any]:
    runtime.camera.set_frame(payload.frame_b64, payload.t)
    if runtime.recorder and runtime.recorder.session_id == session_id:
        filename = runtime.recorder.save_frame(payload.t, payload.frame_b64)
        return {"ok": True, "saved": filename}
    return {"ok": True, "saved": None}


@router.post("/elevenlabs/tts")
async def elevenlabs_text_to_speech(payload: TTSRequest) -> Response:
    """Generate audio via ElevenLabs REST API."""
    if not ELEVENLABS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ELEVENLABS_API_KEY is not configured on the server.",
        )

    voice_id = payload.voice_id or ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    data = json.dumps({
        "text": payload.text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            audio_bytes = resp.read()
            return Response(content=audio_bytes, media_type="audio/mpeg")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        logger.error(f"ElevenLabs TTS error: {err_msg}")
        raise HTTPException(status_code=e.code, detail=f"ElevenLabs TTS failed: {err_msg}")
    except Exception as e:
        logger.exception("ElevenLabs TTS request error")
        raise HTTPException(status_code=500, detail=str(e))

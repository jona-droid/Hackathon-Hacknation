from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any
import httpx2 as httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from backend.core.config import ELEVENLABS_API_KEY, ELEVENLABS_STT_MODEL, ELEVENLABS_VOICE_ID
from backend.api.observer import observe_once
from backend.core.state import runtime

logger = logging.getLogger("robot-apprentice.dialogue")

# One shared ElevenLabs client: it uses the macOS trust store (like the Anthropic SDK), keeps its TLS
# connection open between calls instead of a new handshake per question, and retries a failed connect once.
_elevenlabs = httpx.Client(
    base_url="https://api.elevenlabs.io",
    headers={"xi-api-key": ELEVENLABS_API_KEY},
    timeout=30.0,
    transport=httpx.HTTPTransport(retries=1),
)
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


def _close_pending_question(answer: str) -> dict[str, Any] | None:
    """Record the answer on the latest unanswered question so the observer can ask again."""
    for qa in reversed(runtime.qa_history):
        if qa["answer"] is None:
            qa["answer"] = answer
            return qa
    return None


def _measured(qa: dict[str, Any]) -> tuple[str | None, dict[str, float]]:
    """Task and telemetry signature of the episode the question was about (current one if unknown)."""
    log = runtime.flight_log
    ep = log.find_episode(**qa["episode"]) if qa.get("episode") else log.current_episode()
    return (ep.task, ep.signature) if ep else (None, {})


async def _process_answer(question: str, answer: str) -> dict[str, Any]:
    t = runtime.drone.elapsed_time
    qa = _close_pending_question(answer)
    if not qa or qa["question"] != question:
        qa = {}  # typed note without a question: let the knowledge manager pick the slot
    measured_task, measured = _measured(qa)

    result = await asyncio.to_thread(
        runtime.knowledge_manager.process_operator_response,
        question=question,
        answer=answer,
        target_slot=qa.get("slot"),
        deviation=qa.get("deviation"),
        measured=measured,
        measured_task=measured_task,
        session=runtime.recorder.session_id if runtime.recorder else None,
        t=t,
    )

    # One follow-up per question, asked by the observer loop at the next calm moment
    if result.get("follow_up") and qa.get("kind") != "follow_up" and runtime.session_active:
        runtime.pending_follow_up = {"t": runtime.drone.elapsed_time, "question": result["follow_up"],
                                     "slot": result.get("slot") or qa.get("slot")}
    else:
        result["follow_up"] = ""

    if runtime.recorder:
        runtime.recorder.record_transcript({
            "role": "expert_operator",
            "text": answer,
            "t": t,
            "slot": result.get("slot"),
            "distilled_insight": result.get("insight"),
        })

    return {"ok": True, **result}


@router.post("/dialogue/answer")
async def receive_operator_answer(payload: AnswerRequest) -> dict[str, Any]:
    if not payload.answer.strip():
        raise HTTPException(status_code=400, detail="Answer cannot be empty")
    return await _process_answer(payload.question, payload.answer)


def _transcribe(audio: bytes, content_type: str) -> str:
    """ElevenLabs speech-to-text (no LLM): audio bytes -> text, language auto-detected."""
    resp = _elevenlabs.post(
        "/v1/speech-to-text",
        data={"model_id": ELEVENLABS_STT_MODEL},
        files={"file": ("answer", audio, content_type)},
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


@router.post("/dialogue/voice-answer")
async def receive_voice_answer(request: Request, question: str) -> dict[str, Any]:
    """Body = the recorded answer audio. Transcribed by ElevenLabs, then processed like a typed answer."""
    if not ELEVENLABS_API_KEY:
        raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY is not configured on the server.")
    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="No audio received")
    try:
        transcript = await asyncio.to_thread(_transcribe, audio, request.headers.get("content-type", "audio/webm"))
    except httpx.HTTPStatusError as e:
        logger.error(f"ElevenLabs speech-to-text error: {e.response.text}")
        raise HTTPException(status_code=502, detail=f"Speech-to-text failed: {e.response.text}")
    except httpx.TransportError as e:
        logger.error(f"Cannot reach ElevenLabs: {e}")
        raise HTTPException(status_code=502, detail=f"Cannot reach ElevenLabs (network or certificate problem): {e}")
    if not transcript:
        _close_pending_question("(no answer)")
        return {"ok": False, "transcript": "", "insight": None}
    return {"transcript": transcript, **await _process_answer(question, transcript)}


@router.post("/dialogue/skip")
async def skip_question() -> dict[str, Any]:
    """Pilot skipped the question (Esc) or said nothing: let the observer move on."""
    _close_pending_question("(no answer)")
    return {"ok": True}


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
    body = {
        "text": payload.text,
        "model_id": "eleven_flash_v2_5",  # low-latency; the v1 models were retired
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
    }
    try:
        resp = await asyncio.to_thread(
            _elevenlabs.post, f"/v1/text-to-speech/{voice_id}", json=body, headers={"Accept": "audio/mpeg"}
        )
        resp.raise_for_status()
        return Response(content=resp.content, media_type="audio/mpeg")
    except httpx.HTTPStatusError as e:
        logger.error(f"ElevenLabs TTS error: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail=f"ElevenLabs TTS failed: {e.response.text}")
    except httpx.TransportError as e:
        logger.error(f"Cannot reach ElevenLabs: {e}")
        raise HTTPException(status_code=502, detail=f"Cannot reach ElevenLabs (network or certificate problem): {e}")

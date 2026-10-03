"""Periodic LLM observation of the expert's flight; pushes questions to the frontend for TTS."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.api.ws import broadcast
from backend.core.config import (
    OBSERVER_INTERVAL_S,
    OBSERVER_WINDOW_S,
    QUESTION_COOLDOWN_S,
    UNANSWERED_QUESTION_TIMEOUT_S,
)
from backend.core.state import runtime

logger = logging.getLogger("robot-apprentice.observer")


def _awaiting_answer(t: float) -> bool:
    last = runtime.qa_history[-1] if runtime.qa_history else None
    return bool(last and last["answer"] is None and t - last["t"] < UNANSWERED_QUESTION_TIMEOUT_S)


def _should_observe(t: float) -> bool:
    return (
        runtime.mode == "expert"
        and runtime.session_active
        and runtime.observer.is_available
        and not runtime.observer_busy
        and runtime.drone.altitude > 0.4
        and runtime.flight_log.duration >= OBSERVER_WINDOW_S
        and t - runtime.last_question_time >= QUESTION_COOLDOWN_S
        and not _awaiting_answer(t)
    )


async def observe_once(force: bool = False) -> dict[str, Any] | None:
    """Ask the observer about the recent flight; returns the question payload if one was asked."""
    epoch = runtime.session_epoch
    runtime.observer_busy = True
    try:
        flight = runtime.flight_log.observer_context(OBSERVER_WINDOW_S)
        decision = await runtime.observer.decide(flight, runtime.qa_history, force=force)
    finally:
        runtime.observer_busy = False

    if runtime.session_epoch != epoch or not runtime.session_active:
        logger.info("observer: flight ended during the call, result discarded")
        return None

    telemetry = runtime.drone.snapshot()
    t = telemetry["t"]
    if runtime.recorder:
        runtime.recorder.record_observation({"t": t, "forced": force, **decision.model_dump()})
    await broadcast({"type": "observation", "t": t, "observation": decision.observation, "asked": decision.ask_question})

    if not (decision.ask_question or force) or not decision.question:
        return None

    runtime.last_question_time = t
    runtime.qa_history.append({"t": t, "question": decision.question, "answer": None})
    payload = {
        "question": decision.question,
        "observation": decision.observation,
        "event": {"type": "observer", "t": t},
        "telemetry": telemetry,
        "t": t,
    }
    runtime.latest_question = payload
    if runtime.recorder:
        runtime.recorder.record_transcript({"role": "apprentice_model", "text": decision.question, "t": t})
    return payload


async def observer_loop() -> None:
    while True:
        await asyncio.sleep(OBSERVER_INTERVAL_S)
        try:
            if _should_observe(runtime.drone.elapsed_time):
                payload = await observe_once()
                if payload:
                    await broadcast({"type": "question", **payload})
        except Exception:
            logger.exception("Observer iteration failed")

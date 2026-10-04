"""LLM observer (telemetry + latest camera frame) that decides when to ask the expert pilot a question."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import anthropic
from pydantic import BaseModel, Field

from backend.core.config import ANTHROPIC_API_KEY, OBSERVER_MODEL
from backend.storage.knowledge_store import get_knowledge

logger = logging.getLogger("robot-apprentice.observer")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "observer.txt"


class ObserverDecision(BaseModel):
    observation: str = Field(description="One short sentence: what the pilot did in the last few seconds.")
    ask_question: bool = Field(description="True only if now is a good moment to ask the pilot something.")
    question: str = Field(description="The exact words to speak to the pilot, or an empty string if not asking.")


class FlightObserver:
    def __init__(self, model: str = OBSERVER_MODEL) -> None:
        self.model = model
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self._client = (
            anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=15.0, max_retries=1)
            if ANTHROPIC_API_KEY
            else None
        )

    @property
    def is_available(self) -> bool:
        return self._client is not None

    async def decide(
        self,
        flight: dict[str, Any],
        qa_history: list[dict[str, Any]],
        force: bool = False,
        frame_b64: str | None = None,
        frame_age_s: float | None = None,
    ) -> ObserverDecision:
        if self._client is None:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        text = (
            f"<knowledge_already_captured>\n{get_knowledge()[-1500:]}\n</knowledge_already_captured>\n\n"
            f"<recent_questions_and_answers>\n{json.dumps(qa_history[-6:], ensure_ascii=False)}\n</recent_questions_and_answers>\n\n"
            f"<telemetry>\n{json.dumps(flight, separators=(',', ':'))}\n</telemetry>\n\n"
            + (
                "The operator explicitly asked for a question now: you must ask one."
                if force
                else "Decide whether to ask the pilot a question right now."
            )
        )

        content: list[dict[str, Any]] = []
        if frame_b64:
            content.append({"type": "text", "text": f"Camera frame, captured {frame_age_s or 0:.1f} s ago:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64},
            })
        else:
            text += "\n\nNo camera frame is available right now: rely on telemetry only."
        content.append({"type": "text", "text": text})

        response = await self._client.messages.parse(
            model=self.model,
            max_tokens=400,
            system=self.system_prompt,
            messages=[{"role": "user", "content": content}],
            output_format=ObserverDecision,
        )
        if response.stop_reason in ("max_tokens", "refusal") or response.parsed_output is None:
            raise RuntimeError(f"Observer returned no decision (stop_reason={response.stop_reason})")

        decision = response.parsed_output
        decision.question = decision.question.strip()
        if not decision.question:
            decision.ask_question = False
        logger.info(
            "observer: ask=%s frame=%s in=%d out=%d | %s",
            decision.ask_question,
            frame_b64 is not None,
            response.usage.input_tokens,
            response.usage.output_tokens,
            decision.question or decision.observation,
        )
        return decision

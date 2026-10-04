"""LLM observer (expert mode): turns a moment flagged by the attention model into one spoken question.

The system prompt (rules + the whole competence grid) is identical on every call and marked for
prompt caching, so after the first call of a flight it costs a tenth of the price; each call then
only sends the moment: why now, the candidate slots, compact telemetry and the latest camera frame.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import anthropic
from pydantic import BaseModel, Field

from backend.core.config import ANTHROPIC_API_KEY, OBSERVER_MODEL
from backend.llm import usage
from backend.storage.competence_store import GRID, SLOTS

logger = logging.getLogger("robot-apprentice.observer")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "observer.txt"


class ObserverDecision(BaseModel):
    observation: str = Field(description="One short sentence: what the pilot just did.")
    ask_question: bool = Field(description="True only if now is a good moment to ask the pilot something.")
    target_slot: str = Field(description="The target slot the question fills (task.slot), or an empty string if not asking.")
    question: str = Field(description="The exact words to speak to the pilot, or an empty string if not asking.")


def grid_reference() -> str:
    """The competence grid as prompt text: every task, how it is detected, every slot with an example."""
    lines = [
        "## Competence grid reference",
        "Slot ids are task.slot. Slot types: " + "; ".join(f"{k} = {v}" for k, v in GRID["slot_types"].items()) + ".",
    ]
    for task in GRID["tasks"]:
        lines.append(f"\n### {task['id']}: {task['name']}")
        lines.append(f"Detected by telemetry when: {task['detect']}. Already measured: {', '.join(task['measured'])}.")
        for s in task["slots"]:
            lines.append(f"- {task['id']}.{s['id']} [{s['type']}]: {s['learn']}. Example: \"{s['ask_example']}\"")
    ref = GRID.get("industry_reference", {})
    lines.append("\n## Industry reference (to recognise plausible answers and ask sharper follow-ups; never put these numbers in the pilot's mouth)")
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in ref.items()]
    return "\n".join(lines)


SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8").rstrip() + "\n\n" + grid_reference()


class FlightObserver:
    def __init__(self, model: str = OBSERVER_MODEL, api_key: str | None = None) -> None:
        self.model = model
        self.system_prompt = SYSTEM_PROMPT
        key = api_key if api_key is not None else ANTHROPIC_API_KEY
        self._client = anthropic.AsyncAnthropic(api_key=key, timeout=15.0, max_retries=1) if key else None

    @property
    def is_available(self) -> bool:
        return self._client is not None

    async def decide(
        self,
        flight: dict[str, Any],
        learning: dict[str, Any],
        qa_history: list[dict[str, Any]],
        force: bool = False,
        frame_b64: str | None = None,
        frame_age_s: float | None = None,
    ) -> ObserverDecision:
        if self._client is None:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        text = (
            f"<moment>\n{json.dumps(learning, ensure_ascii=False, separators=(',', ':'))}\n</moment>\n\n"
            f"<recent_questions_and_answers>\n{json.dumps(qa_history, ensure_ascii=False, separators=(',', ':'))}\n</recent_questions_and_answers>\n\n"
            f"<flight>\n{json.dumps(flight, separators=(',', ':'))}\n</flight>\n\n"
            + (
                "The operator explicitly asked for a question now: you must ask one."
                if force
                else "Decide whether to ask the pilot a question right now."
            )
        )
        content: list[dict[str, Any]] = []
        if frame_b64:
            content.append({"type": "text", "text": f"Camera frame, captured {frame_age_s or 0:.1f} s ago:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": frame_b64}})
        else:
            text += "\n\nNo camera frame is available right now: rely on telemetry only."
        content.append({"type": "text", "text": text})

        response = await self._client.messages.parse(
            model=self.model,
            max_tokens=400,
            system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=ObserverDecision,
        )
        usage.record(self.model, response.usage, "observer")
        if response.stop_reason in ("max_tokens", "refusal") or response.parsed_output is None:
            raise RuntimeError(f"Observer returned no decision (stop_reason={response.stop_reason})")

        decision = response.parsed_output
        decision.question = decision.question.strip()
        if not decision.question:
            decision.ask_question = False
        if decision.target_slot not in SLOTS:
            decision.target_slot = ""
        u = response.usage
        logger.info(
            "observer: ask=%s slot=%s frame=%s in=%d cached=%d out=%d | %s",
            decision.ask_question,
            decision.target_slot or "-",
            frame_b64 is not None,
            u.input_tokens,
            (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0),
            u.output_tokens,
            decision.question or decision.observation,
        )
        return decision

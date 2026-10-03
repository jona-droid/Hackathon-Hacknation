from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.llm.client import LLMClient
from backend.storage.knowledge_store import get_knowledge

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "questioner.txt"


def _fallback_question(event: dict[str, Any], telemetry: dict[str, Any]) -> str:
    ev_type = event.get("type", "")
    cable_dist = telemetry.get("cable_dist", 0.0)
    alt = telemetry.get("altitude", 0.0)
    speed = telemetry.get("speed", 0.0)

    if ev_type == "hover_start":
        return f"I noticed you began hovering at altitude {alt:.1f}m with {cable_dist:.1f}m cable distance. What specific component are you inspecting here?"
    if ev_type == "sudden_deceleration":
        return f"You abruptly decelerated to {speed:.1f} m/s. Did you spot wind shear or a visual hazard on the conductor?"
    if ev_type == "insulator_inspected":
        name = event.get("name", "the insulator")
        return f"You completed a dwell by {name}. What signs of wear or flashover did you check for?"
    if ev_type == "near_cable" or ev_type == "very_close_cable":
        return f"You brought the drone within {cable_dist:.1f}m of the high-voltage cable. What clearance rationale did you apply here?"
    if ev_type == "over_road":
        return f"You are crossing above the road at altitude {alt:.1f}m. How did you choose this crossing height?"

    return f"You performed a distinct flight maneuver at t={telemetry.get('t', 0.0):.1f}s. What was your operational objective?"


class Questioner:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()
        self.system_prompt = (
            PROMPT_PATH.read_text(encoding="utf-8")
            if PROMPT_PATH.exists()
            else "You are an AI apprentice questioning a master drone pilot."
        )

    def generate_question(
        self,
        event: dict[str, Any],
        telemetry: dict[str, Any],
        image_b64: str | None = None,
    ) -> dict[str, Any]:
        knowledge = get_knowledge()

        prompt = (
            f"Current Knowledge Base excerpt:\n{knowledge[:1200]}\n\n"
            f"Trigger Event Detected:\n{json.dumps(event, indent=2)}\n\n"
            f"Drone Telemetry:\n{json.dumps(telemetry, indent=2)}\n\n"
            "Ask the operator ONE short, direct question about why they took this action."
        )

        try:
            if self.client.is_available:
                question_text = self.client.call_multimodal(
                    prompt=prompt,
                    system=self.system_prompt,
                    image_b64=image_b64,
                    max_tokens=150,
                )
            else:
                question_text = _fallback_question(event, telemetry)
        except Exception:
            question_text = _fallback_question(event, telemetry)

        return {
            "question": question_text,
            "event": event,
            "telemetry": telemetry,
            "t": telemetry.get("t", 0.0),
        }

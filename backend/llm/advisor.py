from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.core.config import TUTOR_MODEL
from backend.llm.client import LLMClient
from backend.storage.knowledge_store import get_knowledge

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "advisor.txt"


def _fallback_advice(
    telemetry: dict[str, Any],
    event: dict[str, Any] | None = None,
    novice_query: str | None = None,
) -> dict[str, Any]:
    cable_dist = telemetry.get("cable_dist", 99.0)
    speed = telemetry.get("speed", 0.0)
    alt = telemetry.get("altitude", 0.0)

    if novice_query:
        query_l = novice_query.lower()
        if "road" in query_l:
            return {
                "speech": "Maintain at least 20 meters altitude over the road to clear vehicular traffic and avoid electro-magnetic interference.",
                "category": "qa_response",
                "urgency": "medium",
                "knowledge_reference": "Section 1: Altitude Range",
            }
        if "cable" in query_l or "distance" in query_l:
            return {
                "speech": "Keep a minimum standoff distance of 2.5 meters while traveling and never approach closer than 1.5 meters during inspection.",
                "category": "qa_response",
                "urgency": "medium",
                "knowledge_reference": "Section 1: Cable Standoff Distance",
            }
        if "insulator" in query_l:
            return {
                "speech": "Approach the insulator horizontally at eye-level, hold steady for 3 seconds, and look for flashover burn marks.",
                "category": "qa_response",
                "urgency": "medium",
                "knowledge_reference": "Section 2: Insulator Strings",
            }
        return {
            "speech": f"Based on the flight guidelines: maintain safe cable clearance, limit speed to 1 meter per second near wires, and stabilize before inspecting.",
            "category": "qa_response",
            "urgency": "low",
            "knowledge_reference": "General Maintenance Protocol",
        }

    # Proactive advice based on live telemetry & events
    if cable_dist < 1.6:
        return {
            "speech": f"Caution! Cable clearance is critically low at {cable_dist:.1f} meters. Increase standoff immediately.",
            "category": "safety_alert",
            "urgency": "high",
            "knowledge_reference": "Section 1: Cable Standoff Distance (1.5m hard limit)",
        }
    if cable_dist < 2.5 and speed > 1.2:
        return {
            "speech": f"Reduce speed to under 1 meter per second when flying within 4 meters of conductors.",
            "category": "technique_tip",
            "urgency": "medium",
            "knowledge_reference": "Section 1: Maximum Approach Speed",
        }
    if event and event.get("type") == "over_road" and alt < 20.0:
        return {
            "speech": "You are low over the roadway. Climb above 20 meters as mandated by the corridor crossing protocol.",
            "category": "safety_alert",
            "urgency": "high",
            "knowledge_reference": "Section 1: Road Clearance",
        }
    if event and event.get("type") == "hover_start" and cable_dist < 3.5:
        return {
            "speech": "Stabilized in inspection stance. Maintain position for 3 seconds to ensure sharp imagery of the insulator clamp.",
            "category": "technique_tip",
            "urgency": "low",
            "knowledge_reference": "Section 2: Insulator Inspection Dwell",
        }

    return {
        "speech": "Flight parameters are within standard limits. Proceed along the span towards the next pylon.",
        "category": "technique_tip",
        "urgency": "low",
        "knowledge_reference": "Standard Operating Limits",
    }


class Advisor:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient(model=TUTOR_MODEL, timeout=15.0)
        self.system_prompt = (
            PROMPT_PATH.read_text(encoding="utf-8")
            if PROMPT_PATH.exists()
            else "You are an AI Flight Instructor tutoring a novice drone operator."
        )

    def advise(
        self,
        telemetry: dict[str, Any],
        image_b64: str | None = None,
        event: dict[str, Any] | None = None,
        novice_query: str | None = None,
    ) -> dict[str, Any]:
        knowledge = get_knowledge()

        prompt = (
            f"Knowledge Base (knowledge.md):\n{knowledge}\n\n"
            f"Current Telemetry:\n{json.dumps(telemetry, indent=2)}\n\n"
            f"Event Context:\n{json.dumps(event or {}, indent=2)}\n\n"
            f"Novice Pilot Query (if any):\n{novice_query or 'None (provide proactive coaching)'}\n\n"
            "Return JSON advice with keys: speech, category, urgency, knowledge_reference."
        )

        try:
            if self.client.is_available:
                result = self.client.call_json(
                    prompt=prompt,
                    system=self.system_prompt,
                    image_b64=image_b64,
                )
                if "speech" in result:
                    return result
        except Exception:
            pass

        return _fallback_advice(telemetry, event, novice_query)

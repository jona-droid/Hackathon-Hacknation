from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from backend.core.config import TUTOR_MODEL
from backend.llm.client import LLMClient
from backend.storage.knowledge_store import get_knowledge

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "advisor.txt"
logger = logging.getLogger("robot-apprentice.advisor")

# Cable proximity alerts are spoken without the LLM: they must be instant and never skipped
DANGER_CABLE_DIST_M = 2.0  # the HUD turns red at the same distance
CLOSING_CABLE_DIST_M = 4.0
CLOSING_SPEED_MS = 1.5  # approaching a cable faster than this, inside CLOSING_CABLE_DIST_M, is a warning


def _away_command(telemetry: dict[str, Any]) -> str:
    """The stick move that takes the drone straight away from the nearest cable, in the pilot's frame."""
    pos, cable = telemetry["pos"], telemetry["nearest_cable_point"]
    away = [pos[i] - cable[i] for i in range(3)]
    yaw = telemetry["yaw"]
    fwd = away[0] * math.cos(yaw) + away[1] * math.sin(yaw)
    left = -away[0] * math.sin(yaw) + away[1] * math.cos(yaw)
    options = [
        (away[2], "climb"), (-away[2], "descend"),
        (fwd, "move forward"), (-fwd, "back up"),
        (left, "move left"), (-left, "move right"),
    ]
    return max(options)[1]


def safety_alert(telemetry: dict[str, Any]) -> dict[str, Any] | None:
    """Spoken alert when the drone is dangerously close to a cable or closing in on one fast."""
    dist = telemetry["cable_dist"]
    if dist >= CLOSING_CABLE_DIST_M:
        return None
    pos, cable, vel = telemetry["pos"], telemetry["nearest_cable_point"], telemetry["vel"]
    closing = -sum((pos[i] - cable[i]) * vel[i] for i in range(3)) / max(dist, 0.1)
    move = _away_command(telemetry)
    if dist < DANGER_CABLE_DIST_M:
        return {
            "speech": f"Danger! Cable {dist:.1f} metres away. {move.capitalize()} now.",
            "category": "safety_alert",
            "urgency": "high",
            "knowledge_reference": f"Cable standoff: never closer than {DANGER_CABLE_DIST_M:.0f} m",
        }
    if closing > CLOSING_SPEED_MS:
        return {
            "speech": f"Slow down, you are closing on the cable fast, {dist:.0f} metres. Release the sticks or {move}.",
            "category": "safety_alert",
            "urgency": "medium",
            "knowledge_reference": "Maximum approach speed near conductors",
        }
    return None


def _next_step(mission: dict[str, Any] | None) -> str:
    todo = (mission or {}).get("remaining_insulators") or []
    if not todo:
        return "All insulators are inspected. Fly back to the take-off point and land."
    n = todo[0]
    return f"Next, inspect insulator {n['id']}, about {n['distance_m']:.0f} metres {n['direction']}. Approach slowly and hover about 3 metres from it."


def _fallback_advice(
    telemetry: dict[str, Any],
    event: dict[str, Any] | None = None,
    novice_query: str | None = None,
    mission: dict[str, Any] | None = None,
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
            "speech": _next_step(mission),
            "category": "qa_response",
            "urgency": "low",
            "knowledge_reference": "General Maintenance Protocol",
        }

    # Proactive advice based on live telemetry & events
    alert = safety_alert(telemetry)
    if alert:
        return alert
    if cable_dist < DANGER_CABLE_DIST_M:
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
        "speech": _next_step(mission),
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
        mission: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        knowledge = get_knowledge()
        tel = {k: v for k, v in telemetry.items() if k not in ("quat", "rpy", "acc", "wind")}

        prompt = (
            f"Knowledge Base (knowledge.md):\n{knowledge}\n\n"
            f"Current Telemetry:\n{json.dumps(tel, separators=(',', ':'))}\n\n"
            f"Mission Progress:\n{json.dumps(mission or {}, separators=(',', ':'))}\n\n"
            f"Event Context:\n{json.dumps(event or {}, separators=(',', ':'))}\n\n"
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
                logger.warning("Tutor reply has no speech, using the fallback: %s", result)
        except Exception:
            logger.exception("Tutor call failed, using the fallback advice")

        return _fallback_advice(telemetry, event, novice_query, mission)

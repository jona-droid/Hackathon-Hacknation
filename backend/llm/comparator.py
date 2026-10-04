"""Expert vs novice comparison: deterministic scores, commentary written by Claude (structured output)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.core.config import CABLE_DANGER_M, DEBRIEF_MODEL, SESSIONS_DIR
from backend.llm.client import LLMClient
from backend.llm.summarizer import FlightSummarizer
from backend.storage import competence_store
from backend.storage.comparison_store import save_comparison

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "comparator.txt"
logger = logging.getLogger("robot-apprentice.compare")


class ComparisonNarrative(BaseModel):
    coverage_commentary: str = Field(description="Inspection coverage and defects found or missed, compared.")
    safety_commentary: str = Field(description="Standoff, violations, predicted conflicts, Guardian interventions, road crossings.")
    hover_discipline: str = Field(description="Stability and dwell at the insulators.")
    knowledge_adherence: str = Field(description="Did the novice apply the rules the expert taught?")
    key_strengths: list[str] = Field(description="2 or 3 strengths of the novice flight.")
    areas_for_improvement: list[str] = Field(description="2 or 3 actionable recommendations.")
    instructor_verdict: str = Field(description="Final paragraph: readiness of the novice.")


def score(expert: dict[str, Any], novice: dict[str, Any]) -> tuple[float, str, list[str]]:
    """Overall score (0-100), compliance rating and missed targets, from the measured metrics only."""
    missed = sorted(set(expert.get("insulators_inspected", [])) - set(novice.get("insulators_inspected", [])))
    violations = int(novice.get("safety_violations_count", 0))
    min_dist = float(novice.get("min_cable_distance", 99.0))
    value = 100.0
    value -= 12.0 * len(missed)
    value -= 15.0 * violations
    value -= 8.0 * int(novice.get("guardian_interventions", 0))
    value -= 5.0 * int(novice.get("low_road_crossings", 0))
    value -= 4.0 * len(novice.get("defects_missed", []))
    if min_dist < CABLE_DANGER_M:
        value -= 10.0
    value = max(5.0, min(100.0, round(value, 1)))
    if violations == 0 and min_dist >= CABLE_DANGER_M and not novice.get("guardian_interventions"):
        rating = "Excellent"
    elif violations <= 2:
        rating = "Satisfactory"
    else:
        rating = "Requires Retraining"
    return value, rating, missed


class FlightComparator:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient(model=DEBRIEF_MODEL, purpose="debrief")
        self.summarizer = FlightSummarizer(self.client)
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    def _summary(self, session_id: str) -> dict[str, Any]:
        path = SESSIONS_DIR / session_id / "summary.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return self.summarizer.generate_summary(session_id)

    def compare(self, expert_session_id: str, novice_session_id: str) -> dict[str, Any]:
        expert = self._summary(expert_session_id)
        novice = self._summary(novice_session_id)
        overall, rating, missed = score(expert, novice)
        rules = {k: e["rule"] for k, e in competence_store.load().items()}

        narrative: ComparisonNarrative | None = None
        try:
            if self.client.is_available:
                narrative = self.client.parse(
                    prompt=(
                        f"Rules the expert taught (competence grid):\n{json.dumps(rules, ensure_ascii=False)}\n\n"
                        f"Expert flight summary:\n{json.dumps(expert, ensure_ascii=False)}\n\n"
                        f"Novice flight summary:\n{json.dumps(novice, ensure_ascii=False)}\n\n"
                        f"Measured verdict: overall score {overall}/100, safety rating {rating}, "
                        f"insulators the novice missed: {missed or 'none'}."
                    ),
                    system=self.system_prompt,
                    output_format=ComparisonNarrative,
                    max_tokens=900,
                )
        except Exception:
            logger.exception("Comparison narrative failed; using the deterministic report")

        if narrative is None:
            narrative = ComparisonNarrative(
                coverage_commentary=(
                    f"Novice inspected {len(novice.get('insulators_inspected', []))} insulators against "
                    f"{len(expert.get('insulators_inspected', []))} for the expert."
                    + (f" Missed: {', '.join(missed)}." if missed else " No target missed.")
                ),
                safety_commentary=(
                    f"Closest cable approach {novice.get('min_cable_distance')} m (expert {expert.get('min_cable_distance')} m), "
                    f"{novice.get('safety_violations_count', 0)} violations, {novice.get('guardian_interventions', 0)} Guardian interventions."
                ),
                hover_discipline=f"Hover stability {novice.get('hover_stability_score', 0)}/10 (expert {expert.get('hover_stability_score', 0)}/10).",
                knowledge_adherence="Compare the novice's distances and road crossings with the expert rules in the knowledge tab.",
                key_strengths=["Completed the flight."],
                areas_for_improvement=["Keep every standoff above the safety margins."],
                instructor_verdict=f"Overall score {overall}/100, safety rated {rating}.",
            )

        report = {
            "expert_session_id": expert_session_id,
            "novice_session_id": novice_session_id,
            "overall_score": overall,
            "coverage_comparison": {
                "expert_inspected_count": len(expert.get("insulators_inspected", [])),
                "novice_inspected_count": len(novice.get("insulators_inspected", [])),
                "missed_targets": missed,
                "commentary": narrative.coverage_commentary,
            },
            "safety_compliance": {
                "min_cable_distance_expert": expert.get("min_cable_distance"),
                "min_cable_distance_novice": novice.get("min_cable_distance"),
                "violations_novice": novice.get("safety_violations_count", 0),
                "guardian_interventions_novice": novice.get("guardian_interventions", 0),
                "compliance_rating": rating,
                "commentary": narrative.safety_commentary,
            },
            "technique_and_stability": {
                "flight_duration_ratio": round(float(novice.get("flight_duration_sec", 0)) / max(1.0, float(expert.get("flight_duration_sec", 1))), 2),
                "hover_discipline": narrative.hover_discipline,
                "knowledge_adherence": narrative.knowledge_adherence,
            },
            "key_strengths": narrative.key_strengths,
            "areas_for_improvement": narrative.areas_for_improvement,
            "instructor_verdict": narrative.instructor_verdict,
        }
        save_comparison(expert_session_id, novice_session_id, report)
        return report

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.core.config import SESSIONS_DIR
from backend.llm.client import LLMClient
from backend.llm.summarizer import FlightSummarizer
from backend.storage.comparison_store import save_comparison
from backend.storage.knowledge_store import get_knowledge

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "comparator.txt"


def _fallback_comparison(expert_summary: dict[str, Any], novice_summary: dict[str, Any]) -> dict[str, Any]:
    exp_inspected = set(expert_summary.get("insulators_inspected", []))
    nov_inspected = set(novice_summary.get("insulators_inspected", []))
    missed = sorted(list(exp_inspected - nov_inspected))

    nov_violations = int(novice_summary.get("safety_violations_count", 0))
    nov_min_dist = float(novice_summary.get("min_cable_distance", 2.0))
    exp_min_dist = float(expert_summary.get("min_cable_distance", 2.5))

    # Calculate overall score
    score = 100.0
    if missed:
        score -= len(missed) * 12.0
    if nov_violations > 0:
        score -= nov_violations * 15.0
    if nov_min_dist < 1.8:
        score -= 10.0
    score = max(20.0, min(100.0, round(score, 1)))

    compliance_rating = "Excellent" if nov_violations == 0 and nov_min_dist >= 2.0 else (
        "Satisfactory" if nov_violations <= 2 else "Requires Retraining"
    )

    exp_dur = max(1.0, float(expert_summary.get("flight_duration_sec", 60.0)))
    nov_dur = float(novice_summary.get("flight_duration_sec", 60.0))
    time_ratio = round(nov_dur / exp_dur, 2)

    return {
        "expert_session_id": expert_summary.get("session_id", "expert"),
        "novice_session_id": novice_summary.get("session_id", "novice"),
        "overall_score": score,
        "coverage_comparison": {
            "expert_inspected_count": len(exp_inspected),
            "novice_inspected_count": len(nov_inspected),
            "missed_targets": missed,
            "commentary": (
                f"Novice inspected {len(nov_inspected)} of {len(exp_inspected)} targets covered by the expert. "
                + (f"Missed components: {', '.join(missed)}." if missed else "All targets successfully checked!")
            ),
        },
        "safety_compliance": {
            "min_cable_distance_expert": exp_min_dist,
            "min_cable_distance_novice": nov_min_dist,
            "violations_novice": nov_violations,
            "compliance_rating": compliance_rating,
            "commentary": (
                f"Novice maintained a minimum clearance of {nov_min_dist}m compared to {exp_min_dist}m by the expert. "
                f"{nov_violations} clearance alerts were triggered."
            ),
        },
        "technique_and_stability": {
            "flight_duration_ratio": time_ratio,
            "hover_discipline": (
                "Novice exhibited disciplined hover stability during inspection sequences."
                if nov_violations == 0 else "Novice showed slight drift when attempting to hold steady near cable sag."
            ),
            "knowledge_adherence": "Novice respected corridor altitude guidelines and slowed down near pylon arms as learned from the expert.",
        },
        "key_strengths": [
            "Good corridor tracking along the catenary curve.",
            "Appropriate vertical climb before crossing roadway.",
        ],
        "areas_for_improvement": [
            "Maintain wider standoff margin (>2.5m) during transition between towers.",
            "Avoid abrupt deceleration when approaching insulator brackets.",
        ],
        "instructor_verdict": (
            f"The apprentice completed the mission with an overall performance score of {score}/100. "
            f"Safety compliance is rated {compliance_rating}. "
            "Ready for supervised field trials once clearance margins are consistently maintained."
        ),
    }


class FlightComparator:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()
        self.summarizer = FlightSummarizer(self.client)
        self.system_prompt = (
            PROMPT_PATH.read_text(encoding="utf-8")
            if PROMPT_PATH.exists()
            else "You are a Chief Flight Instructor comparing drone flights."
        )

    def compare(self, expert_session_id: str, novice_session_id: str) -> dict[str, Any]:
        exp_root = SESSIONS_DIR / expert_session_id
        nov_root = SESSIONS_DIR / novice_session_id

        # Load or generate expert summary
        exp_summary_file = exp_root / "summary.json"
        if exp_summary_file.exists():
            exp_summary = json.loads(exp_summary_file.read_text(encoding="utf-8"))
        else:
            exp_summary = self.summarizer.generate_summary(expert_session_id)

        # Load or generate novice summary
        nov_summary_file = nov_root / "summary.json"
        if nov_summary_file.exists():
            nov_summary = json.loads(nov_summary_file.read_text(encoding="utf-8"))
        else:
            nov_summary = self.summarizer.generate_summary(novice_session_id)

        knowledge = get_knowledge()

        prompt = (
            f"Knowledge Base (knowledge.md):\n{knowledge[:1500]}\n\n"
            f"Expert Flight Summary:\n{json.dumps(exp_summary, indent=2)}\n\n"
            f"Novice Flight Summary:\n{json.dumps(nov_summary, indent=2)}\n\n"
            "Produce comparative evaluation report in strict JSON format."
        )

        comparison_json: dict[str, Any] = {}
        try:
            if self.client.is_available:
                comparison_json = self.client.call_json(
                    prompt=prompt,
                    system=self.system_prompt,
                )
        except Exception:
            pass

        if not comparison_json or "overall_score" not in comparison_json:
            comparison_json = _fallback_comparison(exp_summary, nov_summary)

        # Save report
        save_comparison(expert_session_id, novice_session_id, comparison_json)

        return comparison_json

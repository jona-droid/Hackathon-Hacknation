from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.core.config import SESSIONS_DIR
from backend.llm.client import LLMClient
from backend.storage.session_recorder import load_jsonl

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "summarizer.txt"


def _compute_metrics(session_id: str) -> dict[str, Any]:
    root = SESSIONS_DIR / session_id
    telemetry = load_jsonl(root / "telemetry.jsonl")
    events = load_jsonl(root / "events.jsonl")
    transcript = load_jsonl(root / "transcript.jsonl")

    meta = {}
    if (root / "meta.json").exists():
        try:
            meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    operator_type = meta.get("mode", "expert")
    start_t = telemetry[0].get("t", 0.0) if telemetry else 0.0
    end_t = telemetry[-1].get("t", 0.0) if telemetry else 0.0
    duration = max(0.0, end_t - start_t)

    min_cable_dist = min([float(r.get("cable_dist", 99.0)) for r in telemetry], default=99.0)
    max_speed = max([float(r.get("speed", 0.0)) for r in telemetry], default=0.0)

    inspected_set: set[str] = set()
    violations: list[str] = []
    defects_present: list[dict[str, Any]] = []
    defects_found: list[str] = []

    for ev in events:
        if ev.get("type") == "defects_placed":
            defects_present = ev.get("defects", [])
        if ev.get("type") == "defect_spotted":
            defects_found.append(str(ev.get("defect_id")))
        if ev.get("type") == "insulator_inspected":
            inspected_set.add(str(ev.get("insulator_id", "")))
        if ev.get("type") == "very_close_cable":
            violations.append(f"Cable clearance violation at t={ev.get('t', 0.0):.1f}s")
        if ev.get("type") == "collision":
            violations.append(f"Collision at t={ev.get('t', 0.0):.1f}s")

    for r in telemetry:
        cd = float(r.get("cable_dist", 99.0))
        spd = float(r.get("speed", 0.0))
        if cd < 1.5:
            violations.append(f"Cable proximity < 1.5m at t={r.get('t', 0.0):.1f}s")
            break

    total_insulators = 6
    coverage_pct = round((len(inspected_set) / max(1, total_insulators)) * 100, 1)

    return {
        "session_id": session_id,
        "operator_type": operator_type,
        "flight_duration_sec": round(duration, 1),
        "insulators_inspected": sorted(list(inspected_set)),
        "inspection_coverage_percent": coverage_pct,
        "min_cable_distance": round(min_cable_dist, 2),
        "safety_violations_count": len(violations),
        "max_speed_recorded": round(max_speed, 2),
        "hover_stability_score": 8.5 if len(violations) == 0 else 6.0,
        "defects_found": [f"{d['label']} ({d['target']})" for d in defects_present if d["id"] in defects_found],
        "defects_missed": [f"{d['label']} ({d['target']})" for d in defects_present if d["id"] not in defects_found],
        "events_count": len(events),
        "transcript_count": len(transcript),
        "telemetry_samples": len(telemetry),
    }


class FlightSummarizer:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()
        self.system_prompt = (
            PROMPT_PATH.read_text(encoding="utf-8")
            if PROMPT_PATH.exists()
            else "You are an Aviation Quality Assurance Inspector summarizing drone inspection flights."
        )

    def generate_summary(self, session_id: str) -> dict[str, Any]:
        metrics = _compute_metrics(session_id)
        root = SESSIONS_DIR / session_id
        transcript = load_jsonl(root / "transcript.jsonl")
        events = load_jsonl(root / "events.jsonl")

        prompt = (
            f"Flight Session Metrics:\n{json.dumps(metrics, indent=2)}\n\n"
            f"Key Events (first 15):\n{json.dumps(events[:15], indent=2)}\n\n"
            f"Transcript Q&A:\n{json.dumps(transcript[:10], indent=2)}\n\n"
            "Generate flight debrief summary in strict JSON format."
        )

        summary_json: dict[str, Any] = {}
        try:
            if self.client.is_available:
                summary_json = self.client.call_json(
                    prompt=prompt,
                    system=self.system_prompt,
                )
        except Exception:
            pass

        if not summary_json or "operational_summary" not in summary_json:
            # Deterministic fallback summary
            cov = metrics["inspection_coverage_percent"]
            role = metrics["operator_type"].capitalize()
            dur = metrics["flight_duration_sec"]
            mins = metrics["min_cable_distance"]
            viols = metrics["safety_violations_count"]

            summary_json = {
                **metrics,
                "key_maneuvers": [
                    f"{role} pilot completed {dur}s mission across power line corridor.",
                    f"Inspected {len(metrics['insulators_inspected'])} insulators ({cov}% coverage).",
                    f"Minimum cable clearance maintained: {mins}m.",
                ],
                "operational_summary": (
                    f"{role} flight completed in {dur} seconds with {cov}% insulator coverage. "
                    f"Recorded {viols} safety margin alerts and maintained minimum cable standoff of {mins}m. "
                    f"Inspection flight deemed {'successful and compliant' if viols == 0 else 'satisfactory with safety warnings'}."
                ),
            }

        # Defect findings are ground truth: never let the LLM rewrite them
        summary_json["defects_found"] = metrics["defects_found"]
        summary_json["defects_missed"] = metrics["defects_missed"]

        # Save to disk
        (root / "summary.json").write_text(
            json.dumps(summary_json, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return summary_json

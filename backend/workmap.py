from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from backend.guardrails import default_guardrails, validate_guardrails
from backend.recorder import summarize_for_llm

MODEL = "claude-sonnet-5-5"


SCHEMA_HINT = {
    "work_map": {
        "title": "Power line inspection, spans 1-2",
        "steps": [
            {
                "id": 1,
                "t_start": 0.0,
                "t_end": 14.0,
                "action": "...",
                "decision": "...",
                "reason": "...",
                "exceptions": "...",
                "confidence": "confirmed",
            }
        ],
    },
    "guardrails": [],
    "open_questions": [],
}


def _validate_output(obj: dict[str, Any]) -> dict[str, Any]:
    if "work_map" not in obj or "guardrails" not in obj or "open_questions" not in obj:
        raise ValueError("Missing required keys")
    work_map = obj["work_map"]
    steps = work_map.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("work_map.steps must be list")
    for step in steps:
        if step.get("confidence") not in {"confirmed", "inferred"}:
            step["confidence"] = "inferred"
    obj["guardrails"] = validate_guardrails(obj.get("guardrails", []))
    return obj


def _fallback(summary: dict[str, Any]) -> dict[str, Any]:
    events = summary.get("events", [])
    steps = []
    for i, ev in enumerate(events[:6], start=1):
        t = float(ev.get("t", 0.0))
        et = ev.get("type", "event")
        steps.append(
            {
                "id": i,
                "t_start": t,
                "t_end": t + 3.0,
                "action": f"Handle {et}",
                "decision": f"Decision around {et}",
                "reason": None,
                "exceptions": None,
                "confidence": "inferred",
            }
        )
    return {
        "work_map": {"title": "Power line inspection, spans 1-2", "steps": steps},
        "guardrails": default_guardrails(),
        "open_questions": ["Claude generation unavailable; review interview notes manually."],
    }


def _anthropic_client() -> Anthropic:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY")
    return Anthropic(api_key=key)


def generate_work_map(session_id: str) -> dict[str, Any]:
    summary = summarize_for_llm(session_id)
    try:
        client = _anthropic_client()
    except Exception:
        return _fallback(summary)

    prompt = (
        "Convert this drone training session into strict JSON only.\n"
        "Allowed guardrail types: min_distance(target cable/tree/pylon), keep_out_zone(zone road), "
        "max_speed(zone near_pylon/near_cable/global), altitude_range, hover_at(target insulator).\n"
        "Never invent reasons; use null if not provided.\n"
        f"Schema example: {json.dumps(SCHEMA_HINT)}\n"
        f"Session summary: {json.dumps(summary)}"
    )

    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2400,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join([b.text for b in resp.content if hasattr(b, "text")])
            obj = json.loads(text)
            result = _validate_output(obj)
            if not result.get("guardrails"):
                result["guardrails"] = default_guardrails()
            return result
        except Exception:
            if attempt == 1:
                return _fallback(summary)

    return _fallback(summary)


def save_work_map(session_id: str, result: dict[str, Any]) -> None:
    root = Path(__file__).resolve().parents[1] / "data" / "sessions" / session_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "workmap.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.llm.client import LLMClient
from backend.storage.knowledge_store import append_insight, get_knowledge, save_knowledge

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "knowledge_builder.txt"
logger = logging.getLogger("robot-apprentice.knowledge")


class KnowledgeManager:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()
        self.system_prompt = (
            PROMPT_PATH.read_text(encoding="utf-8")
            if PROMPT_PATH.exists()
            else "You are an AI distilling expert operator knowledge into structured rules."
        )

    def process_operator_response(
        self,
        question: str,
        answer: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = context or {}
        telemetry = context.get("telemetry", {})
        t = telemetry.get("t", 0.0)

        prompt = (
            f"Context at t={t:.1f}s:\n{json.dumps(context, indent=2)}\n\n"
            f"Question Asked to Pilot:\n{question}\n\n"
            f"Pilot's Answer:\n{answer}\n\n"
            "Formulate a structured markdown insight for the knowledge base."
        )

        insight_text = ""
        try:
            if self.client.is_available:
                insight_text = self.client.call_multimodal(
                    prompt=prompt,
                    system=self.system_prompt,
                    max_tokens=300,
                )
        except Exception:
            logger.exception("Knowledge distillation failed; saving the raw answer")

        if insight_text.strip().upper().strip(".") == "NONE":
            # small talk / off-topic: nothing to add to knowledge.md
            return {"insight": None, "rejected": True, "question": question, "answer": answer}

        if not insight_text:
            # Fallback deterministic distillation
            comp = context.get("event", {}).get("name", "Maneuver")
            insight_text = f"- *[t={t:.1f}s]* **{comp} Insight**: {answer.strip()}"

        append_insight(insight_text)
        updated_kb = get_knowledge()

        return {
            "insight": insight_text,
            "knowledge_preview": updated_kb,
            "question": question,
            "answer": answer,
        }

"""Turns a pilot's answer into the content of a competence-grid slot (see competence_store)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.llm.client import LLMClient
from backend.storage import competence_store
from backend.storage.knowledge_store import append_insight

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "knowledge_builder.txt"
logger = logging.getLogger("robot-apprentice.knowledge")


class SlotUpdate(BaseModel):
    usable: bool = Field(description="False if the answer has no operational know-how.")
    slot: str = Field(description='The slot the answer fills, as task.slot from all_slots, or "none".')
    rule: str = Field(description="The full merged rule of the slot: when -> what -> why, no times.")
    conditions: list[str] = Field(description="Situations that change the technique, merged with the stored ones.")
    reason: str = Field(description='Why the pilot does it, or "".')
    vague: bool = Field(description="True if the answer lacks what the slot needs.")
    follow_up: str = Field(description='One short spoken follow-up question if vague, else "".')


class KnowledgeManager:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self._all_slots = [
            {"slot": s["key"], "learn": s["learn"]} for s in competence_store.SLOTS.values()
        ]

    def process_operator_response(
        self,
        question: str,
        answer: str,
        target_slot: str | None = None,
        deviation: dict[str, Any] | None = None,
        measured: dict[str, float] | None = None,
        measured_task: str | None = None,
        session: str | None = None,
        t: float = 0.0,
    ) -> dict[str, Any]:
        entries = competence_store.load()
        target = None
        if target_slot in competence_store.SLOTS:
            spec = competence_store.SLOTS[target_slot]
            target = {
                "slot": target_slot,
                "type": spec["type"],
                "must_capture": spec["learn"],
                "already_stored": entries.get(target_slot) and {
                    k: entries[target_slot].get(k) for k in ("rule", "conditions", "reason")
                },
            }
        prompt = json.dumps({
            "question": question,
            "answer": answer,
            "target_slot": target,
            "about_deviation": deviation and {k: deviation[k] for k in ("expected", "now") if k in deviation},
            "measured": {"task": measured_task, **(measured or {})},
            "all_slots": self._all_slots,
        }, ensure_ascii=False)

        result: dict[str, Any] = {"question": question, "answer": answer}
        try:
            update = self.client.parse(prompt=prompt, system=self.system_prompt, output_format=SlotUpdate, max_tokens=600)
        except Exception as e:
            logger.exception("Knowledge distillation failed (the answer stays in the session transcript)")
            return {**result, "insight": None, "rejected": True, "error": str(e)}
        logger.info("knowledge: slot=%s usable=%s vague=%s | %s", update.slot, update.usable, update.vague, update.rule)

        follow_up = update.follow_up.strip() if update.vague else ""
        if not update.usable or not update.rule.strip():
            return {**result, "insight": None, "rejected": True, "follow_up": follow_up}
        if update.slot not in competence_store.SLOTS:
            # Know-how outside the grid: kept in the log, without a timestamp
            append_insight(f"- {update.rule.strip()}")
            return {**result, "insight": update.rule.strip(), "slot": None, "follow_up": follow_up}

        spec = competence_store.SLOTS[update.slot]
        # evidence only from an episode of the slot's own task
        evidence = {m: measured[m] for m in spec.get("evidence", []) if measured and m in measured} \
            if spec["task"] == measured_task else {}
        entry = competence_store.fill(
            update.slot,
            rule=update.rule.strip(),
            conditions=[c.strip() for c in update.conditions if c.strip()],
            reason=update.reason.strip(),
            evidence=evidence,
            question=question,
            answer=answer,
            session=session,
            t=t,
            about_deviation=bool(deviation),
        )
        return {
            **result,
            "insight": competence_store.describe(update.slot, entry),
            "slot": update.slot,
            "slot_name": competence_store.slot_name(update.slot),
            "follow_up": follow_up,
            "coverage": competence_store.coverage(),
        }

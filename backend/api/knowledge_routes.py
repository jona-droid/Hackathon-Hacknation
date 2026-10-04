from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.storage import competence_store
from backend.storage.knowledge_store import get_knowledge, save_knowledge

router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


class KnowledgeUpdateRequest(BaseModel):
    content: str


@router.get("")
async def fetch_knowledge() -> dict[str, Any]:
    content = get_knowledge()
    return {"content": content}


@router.put("")
async def update_knowledge(payload: KnowledgeUpdateRequest) -> dict[str, Any]:
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Knowledge content cannot be empty")
    save_knowledge(payload.content)
    return {"ok": True, "content": payload.content}


@router.get("/competence")
async def fetch_competence() -> dict[str, Any]:
    """The competence grid with what was learned in each slot (the knowledge gauge)."""
    return competence_store.grid_view()


@router.delete("/competence")
async def reset_competence() -> dict[str, Any]:
    """Empty the grid to start learning from scratch (knowledge.md's other sections are kept)."""
    competence_store.reset()
    return competence_store.grid_view()

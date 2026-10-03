from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.core.config import COMPARISONS_DIR


def save_comparison(expert_id: str, novice_id: str, comparison_data: dict[str, Any]) -> str:
    COMPARISONS_DIR.mkdir(parents=True, exist_ok=True)
    comp_id = f"comp_{expert_id}_{novice_id}"
    path = COMPARISONS_DIR / f"{comp_id}.json"
    path.write_text(json.dumps(comparison_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return comp_id


def get_comparison(expert_id: str, novice_id: str) -> dict[str, Any] | None:
    path = COMPARISONS_DIR / f"comp_{expert_id}_{novice_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def list_comparisons() -> list[dict[str, Any]]:
    out = []
    if not COMPARISONS_DIR.exists():
        return out
    for p in sorted(COMPARISONS_DIR.glob("comp_*.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out

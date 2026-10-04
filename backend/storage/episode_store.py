"""Every task episode the expert flew, across flights (data/knowledge/episodes.jsonl).

One line per finished episode: task, session, start, duration, target and the telemetry signature.
The apprentice induces rules from them: when the expert did the same thing several times, it
proposes that rule for confirmation instead of asking an open question.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from backend.core.config import KNOWLEDGE_DIR

EPISODES_PATH = KNOWLEDGE_DIR / "episodes.jsonl"
MAX_PER_TASK = 12  # the most recent episodes of a task are enough to see a habit

_lock = threading.Lock()
_cache: dict[str, list[dict[str, Any]]] | None = None


def _load() -> dict[str, list[dict[str, Any]]]:
    global _cache
    if _cache is None:
        _cache = {}
        if EPISODES_PATH.exists():
            for line in EPISODES_PATH.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ep = json.loads(line)
                    _cache.setdefault(ep["task"], []).append(ep)
    return _cache


def append(session: str | None, task: str, start: float, duration: float, signature: dict[str, float],
           target: str | None = None) -> None:
    record = {"session": session, "task": task, "start": round(start, 1), "duration": round(duration, 1),
              "target": target, "signature": signature}
    with _lock:
        cache = _load()  # before writing, or the new line would be read back and counted twice
        EPISODES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EPISODES_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        cache.setdefault(task, []).append(record)


def by_task(task: str) -> list[dict[str, Any]]:
    """The most recent episodes of a task, oldest first."""
    with _lock:
        return list(_load().get(task, [])[-MAX_PER_TASK:])


def reset() -> None:
    global _cache
    with _lock:
        EPISODES_PATH.unlink(missing_ok=True)
        _cache = {}

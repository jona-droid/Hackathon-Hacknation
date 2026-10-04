"""Token and cost accounting for every Claude call: this flight, and since the server started.

Shown live in the interface so the team can see what the AI costs while it flies.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

# USD per million tokens: input, output, cache write (5 min), cache read.
# Haiku 4.5 is the published price; the Opus 5.5 line is an estimate (Opus-class pricing).
PRICES_PER_MTOK: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
    "claude-opus-5-5": (5.00, 25.00, 6.25, 0.50),
}

_lock = threading.Lock()


def _price(model: str) -> tuple[float, float, float, float]:
    for prefix, price in PRICES_PER_MTOK.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0, 0.0, 0.0)


@dataclass
class UsageMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    by_purpose: dict[str, int] = field(default_factory=dict)  # calls per purpose

    def add(self, model: str, usage: Any, purpose: str) -> None:
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        p_in, p_out, p_write, p_read = _price(model)
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read_tokens += read
        self.cache_write_tokens += write
        self.cost_usd += (inp * p_in + out * p_out + write * p_write + read * p_read) / 1e6
        self.by_purpose[purpose] = self.by_purpose.get(purpose, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "tokens": self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens,
            "cached_tokens": self.cache_read_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "by_purpose": dict(self.by_purpose),
        }


flight = UsageMeter()  # reset at every Start Flight
total = UsageMeter()


def record(model: str, usage: Any, purpose: str) -> None:
    """Count one API response (its .usage) under a purpose: observer, knowledge, tutor, debrief..."""
    if usage is None:
        return
    with _lock:
        flight.add(model, usage, purpose)
        total.add(model, usage, purpose)


def reset_flight() -> None:
    global flight
    with _lock:
        flight = UsageMeter()


def snapshot() -> dict[str, Any]:
    with _lock:
        return {"flight": flight.snapshot(), "total": total.snapshot()}

"""Periodic observation of the expert's flight; pushes questions to the frontend for TTS.

Every OBSERVER_INTERVAL_S:
1. finished task episodes are compared with the learned rules: the same behaviour confirms a
   rule, a different one becomes a deviation to ask about;
2. if the pilot can be asked now, a pending follow-up is asked as is (no LLM call); otherwise
   Claude is called only when there is something to learn (an open slot or a deviation).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.api.ws import broadcast
from backend.core.config import (
    OBSERVER_FRAME_MAX_AGE_S,
    OBSERVER_INTERVAL_S,
    OBSERVER_WINDOW_S,
    QUESTION_COOLDOWN_S,
    UNANSWERED_QUESTION_TIMEOUT_S,
)
from backend.core.state import runtime
from backend.sim.tasks import TASK_METRICS, Episode
from backend.storage import competence_store

logger = logging.getLogger("robot-apprentice.observer")

FOLLOW_UP_MAX_AGE_S = 60.0
DEVIATION_MAX_AGE_S = 40.0
MAX_OPEN_SLOTS = 8


def _awaiting_answer(t: float) -> bool:
    last = runtime.qa_history[-1] if runtime.qa_history else None
    return bool(last and last["answer"] is None and t - last["t"] < UNANSWERED_QUESTION_TIMEOUT_S)


def _can_ask(t: float) -> bool:
    return (
        runtime.mode == "expert"
        and runtime.session_active
        and not runtime.observer_busy
        and runtime.drone.altitude > 0.4
        and runtime.flight_log.duration >= OBSERVER_WINDOW_S
        and t - runtime.last_question_time >= QUESTION_COOLDOWN_S
        and not _awaiting_answer(t)
    )


def _calm() -> bool:
    """A safe moment for a question asked without the observer: slow and clear of every obstacle."""
    s = runtime.drone.snapshot()
    return s["speed"] < 1.5 and min(s["cable_dist"], s["tree_dist"], s["pylon_dist"]) > 2.0


def _session_id() -> str | None:
    return runtime.recorder.session_id if runtime.recorder else None


# ---- closing the loop -------------------------------------------------------------

def review_episodes() -> None:
    """Compare every finished episode with the rules learned for its task."""
    episodes = runtime.flight_log.tasks.pop_unreviewed()
    if runtime.mode != "expert" or not runtime.session_active:
        return
    asked = {qa.get("slot") for qa in runtime.qa_history if qa.get("kind") == "deviation"}
    for ep in episodes:
        for r in competence_store.review_episode(ep.task, ep.signature, ep.start, _session_id()):
            logger.info("rule check %s: %s (expected %s, now %s)", r["slot"], r["status"], r["expected"], r["now"])
            if runtime.recorder:
                runtime.recorder.record_observation({"t": ep.end, "rule_check": r})
            if r["status"] == "deviation" and r["slot"] not in asked:  # once per rule and flight
                runtime.pending_deviation = {**r, "t": ep.end}


# ---- what there is to learn right now ------------------------------------------------

def _episode_view(ep: Episode, t: float, finished: bool) -> dict[str, Any]:
    view: dict[str, Any] = {
        "task": ep.task,
        "duration_s": round(ep.duration, 1),
        "measured": {m: ep.signature[m] for m in TASK_METRICS.get(ep.task, [])},
    }
    if ep.target:
        view["target"] = ep.target
    if finished:
        view["ended_s_ago"] = round(t - ep.end, 1)
    return view


def learning_context(t: float) -> dict[str, Any]:
    log = runtime.flight_log
    entries = competence_store.load()
    cur = log.current_episode()
    recent = list(reversed(log.tasks.recent(t)))
    tasks = list(dict.fromkeys(([cur.task] if cur else []) + [ep.task for ep in recent] + log.tasks.side))

    dev = runtime.pending_deviation
    if dev and t - dev["t"] > DEVIATION_MAX_AGE_S:
        runtime.pending_deviation = dev = None

    cov = competence_store.coverage()
    ctx: dict[str, Any] = {
        "knowledge": f"{cov['filled']}/{cov['total']} slots filled",
        "current_task": (
            {**_episode_view(cur, t, False), "name": competence_store.TASKS[cur.task]["name"]}
            if cur else "none: pausing away from the line"
        ),
        "just_finished": [_episode_view(ep, t, True) for ep in recent],
        "open_slots": [s for task in tasks for s in competence_store.open_slots(task, entries)][:MAX_OPEN_SLOTS],
        "known_rules": {
            key: competence_store.describe(key, e)
            for key, e in entries.items()
            if competence_store.SLOTS[key]["task"] in tasks
        },
    }
    if log.tasks.side:
        ctx["side_tasks"] = log.tasks.side
    if dev:
        ctx["deviation"] = {k: dev[k] for k in ("slot", "rule", "expected", "now")}
    return ctx


def _has_learning_target(learning: dict[str, Any]) -> bool:
    return bool(learning["open_slots"] or learning.get("deviation"))


# ---- asking ---------------------------------------------------------------------------

def _push_question(t: float, question: str, slot: str | None, kind: str, observation: str,
                   deviation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register a question (for the answer to know its slot and episode) and build its payload."""
    task = competence_store.SLOTS[slot]["task"] if slot else None
    ep = runtime.flight_log.latest_episode(task) if task else runtime.flight_log.current_episode()
    runtime.last_question_time = t
    runtime.qa_history.append({
        "t": t,
        "question": question,
        "answer": None,
        "slot": slot,
        "kind": kind,  # "observer" | "deviation" | "follow_up"
        "deviation": deviation,
        "episode": {"task": ep.task, "start": ep.start} if ep else None,
    })
    payload = {
        "question": question,
        "observation": observation,
        "slot": slot,
        "slot_name": competence_store.slot_name(slot) if slot else None,
        "kind": kind,
        "event": {"type": kind, "t": t},
        "telemetry": runtime.drone.snapshot(),
        "t": t,
    }
    runtime.latest_question = payload
    if runtime.recorder:
        runtime.recorder.record_transcript({"role": "apprentice_model", "text": question, "t": t, "slot": slot, "kind": kind})
    return payload


def _ask_follow_up(t: float) -> dict[str, Any] | None:
    """The one follow-up scheduled after a vague answer, at the next calm moment."""
    fu = runtime.pending_follow_up
    if not fu:
        return None
    if t - fu["t"] > FOLLOW_UP_MAX_AGE_S:
        runtime.pending_follow_up = None
        return None
    if not _calm():
        return None
    runtime.pending_follow_up = None
    return _push_question(t, fu["question"], fu["slot"], "follow_up", "Follow-up on a vague answer.")


def _qa_for_prompt() -> list[dict[str, Any]]:
    return [
        {"t": round(qa["t"], 1), "question": qa["question"], "answer": qa["answer"], "slot": qa.get("slot")}
        for qa in runtime.qa_history[-6:]
    ]


async def observe_once(force: bool = False, learning: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Ask the observer about the recent flight; returns the question payload if one was asked."""
    epoch = runtime.session_epoch
    learning = learning or learning_context(runtime.drone.elapsed_time)
    runtime.observer_busy = True
    try:
        flight = runtime.flight_log.observer_context(OBSERVER_WINDOW_S)
        frame = runtime.camera.get_latest_frame()
        frame_age = runtime.drone.elapsed_time - runtime.camera.latest_timestamp
        if frame and not 0 <= frame_age <= OBSERVER_FRAME_MAX_AGE_S:
            frame = None  # stale (or from a previous flight): it would contradict the telemetry
        decision = await runtime.observer.decide(
            flight, learning, _qa_for_prompt(), force=force, frame_b64=frame, frame_age_s=frame_age
        )
    finally:
        runtime.observer_busy = False

    if runtime.session_epoch != epoch or not runtime.session_active:
        logger.info("observer: flight ended during the call, result discarded")
        return None

    t = runtime.drone.elapsed_time
    if runtime.recorder:
        runtime.recorder.record_observation({"t": t, "forced": force, **decision.model_dump()})
    await broadcast({"type": "observation", "t": t, "observation": decision.observation, "asked": decision.ask_question})

    if not (decision.ask_question or force) or not decision.question:
        return None

    dev = learning.get("deviation")
    if dev and decision.target_slot == dev["slot"]:
        runtime.pending_deviation = None
        return _push_question(t, decision.question, decision.target_slot, "deviation", decision.observation, dev)
    return _push_question(t, decision.question, decision.target_slot or None, "observer", decision.observation)


async def observer_loop() -> None:
    while True:
        await asyncio.sleep(OBSERVER_INTERVAL_S)
        try:
            review_episodes()
            t = runtime.drone.elapsed_time
            if not _can_ask(t):
                continue
            payload = None
            if runtime.pending_follow_up:
                payload = _ask_follow_up(t)  # None while waiting for a calm moment
            elif runtime.observer.is_available:
                learning = learning_context(t)
                if _has_learning_target(learning):
                    payload = await observe_once(learning=learning)
            if payload:
                await broadcast({"type": "question", **payload})
        except Exception:
            logger.exception("Observer iteration failed")

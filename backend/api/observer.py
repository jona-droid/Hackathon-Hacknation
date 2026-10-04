"""Expert mode: the apprentice's question loop.

Every OBSERVER_TICK_S, locally and for free:
1. finished task episodes are stored (episode_store) and compared with the learned rules: the same
   behaviour confirms a rule, a different one becomes a deviation to ask about;
2. the attention model (llm/attention.py) scores the moment and picks the slots it could teach.
Then, if the pilot can be asked, a pending follow-up is asked as is (no LLM call); otherwise Claude
is called only when the moment is salient, with the reasons ("why now") and the candidate slots.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.api.ws import broadcast
from backend.core.config import (
    OBSERVER_FRAME_MAX_AGE_S,
    OBSERVER_INTERVAL_S,
    OBSERVER_TICK_S,
    OBSERVER_WINDOW_S,
    QUESTION_COOLDOWN_S,
    QUIET_AFTER_SPEECH_S,
    UNANSWERED_QUESTION_TIMEOUT_S,
)
from backend.core.state import runtime
import uuid

from backend.llm import attention
from backend.sim.tasks import TASK_METRICS
from backend.storage import competence_store, episode_store

logger = logging.getLogger("robot-apprentice.observer")

FOLLOW_UP_MAX_AGE_S = 60.0
AGENT_CONTEXT_S = 10.0
DEVIATION_MAX_AGE_S = 40.0
PILOT_NOTE_MAX_S = 90.0  # a note recording the browser never closed stops blocking questions after this
RETRY_DECLINED_S = 8.0  # the LLM said "not now": the same moment may be offered once more after this
MAX_ATTEMPTS_PER_MOMENT = 2


def _awaiting_answer(t: float) -> bool:
    last = next((qa for qa in reversed(runtime.qa_history) if qa.get("question")), None)
    return bool(last and last["answer"] is None and t - last["t"] < UNANSWERED_QUESTION_TIMEOUT_S)


def _pilot_recording(t: float) -> bool:
    since = runtime.pilot_note_since
    return since is not None and t - since < PILOT_NOTE_MAX_S


def _pilot_talking(t: float) -> bool:
    """The pilot is talking, or stopped less than QUIET_AFTER_SPEECH_S ago: never talk over them."""
    return runtime.pilot_quiet_for(t) < QUIET_AFTER_SPEECH_S


def _pilot_busy() -> bool:
    """A delicate manoeuvre (the expert's equivalent of typing): close to an obstacle or a predicted
    conflict. Checked locally, so no Claude call is spent on a moment the pilot cannot answer."""
    s = runtime.drone.snapshot()
    risky = (runtime.flight_log.prediction or {}).get("risk") in ("medium", "high")
    return risky or min(s["cable_dist"], s["tree_dist"], s["pylon_dist"]) < 2.5


def _can_ask(t: float) -> bool:
    return (
        runtime.mode == "expert"
        and runtime.session_active
        and not runtime.off_record
        and not runtime.observer_busy
        and runtime.drone.altitude > 0.4
        and runtime.flight_log.duration >= OBSERVER_WINDOW_S
        and t - runtime.last_question_time >= QUESTION_COOLDOWN_S
        and not _awaiting_answer(t)
        and not _pilot_recording(t)
        and not _pilot_talking(t)
    )


def _calm() -> bool:
    """A safe moment for a question asked without the observer: slow and clear of every obstacle."""
    s = runtime.drone.snapshot()
    risky = (runtime.flight_log.prediction or {}).get("risk") in ("medium", "high")
    return s["speed"] < 1.5 and min(s["cable_dist"], s["tree_dist"], s["pylon_dist"]) > 2.0 and not risky


def tutor_status() -> str:
    """[STATUS] for the tutor agent: where the novice is and what is left (background, never spoken)."""
    s = runtime.drone.snapshot()
    m = runtime.mission_context()
    nxt = m["remaining_insulators"][0] if m["remaining_insulators"] else None
    text = (f"[STATUS] Flight time {s['t']:.0f} s. Height {s['altitude']:.0f} m, speed {s['speed']:.1f} m/s, "
            f"{s['cable_dist']:.0f} m from the nearest cable, {s['road_dist']:.0f} m from the road. "
            f"Inspected: {', '.join(m['inspected']) or 'none'}. Defects found: {m['defects_spotted']}. ")
    if nxt:
        text += (f"Next: insulator {nxt['id']}, {nxt['distance_m']:.0f} m {nxt['direction']}, "
                 f"{nxt['height_above_drone_m']:+.0f} m above the drone.")
    else:
        text += "All insulators are inspected: fly back and land."
    return text


def _session_id() -> str | None:
    return runtime.recorder.session_id if runtime.recorder else None


# ---- closing the loop -------------------------------------------------------------

def review_episodes() -> None:
    """Store every finished episode and compare it with the rules learned for its task."""
    episodes = runtime.flight_log.tasks.pop_unreviewed()
    if runtime.mode != "expert" or not runtime.session_active:
        return
    asked = {qa.get("slot") for qa in runtime.qa_history if qa.get("kind") == "deviation"}
    off = list(runtime.off_record_windows)
    if runtime.off_record and runtime.off_record_since is not None:
        off.append([runtime.off_record_since, runtime.drone.elapsed_time])
    for ep in episodes:
        if any(a <= ep.end and ep.start <= b for a, b in off):
            continue  # flown off the record: not a habit to learn from, not a rule to check
        episode_store.append(_session_id(), ep.task, ep.start, ep.duration, ep.signature, ep.target)
        for r in competence_store.review_episode(ep.task, ep.signature, ep.start, _session_id()):
            logger.info("rule check %s: %s (expected %s, now %s)", r["slot"], r["status"], r["expected"], r["now"])
            if runtime.recorder:
                runtime.recorder.record_observation({"t": ep.end, "rule_check": r})
            if r["status"] == "deviation" and r["slot"] not in asked:  # once per rule and flight
                runtime.pending_deviation = {**r, "t": ep.end}


# ---- attention ----------------------------------------------------------------------

def assess(t: float, force: bool = False) -> attention.Moment | None:
    dev = runtime.pending_deviation
    if dev and t - dev["t"] > DEVIATION_MAX_AGE_S:
        runtime.pending_deviation = dev = None
    return attention.assess(
        t,
        runtime.flight_log,
        competence_store.load(),
        runtime.qa_history,
        runtime.attention_consumed,
        deviation=dev,
        episodes_for=episode_store.by_task,
        force=force,
    )


def _learning(moment: attention.Moment) -> dict[str, Any]:
    """The moment as the LLM sees it: why now, the slots to choose from, what is already known."""
    entries = competence_store.load()
    tasks = {competence_store.SLOTS[tg.slot]["task"] for tg in moment.targets}
    cur = runtime.flight_log.tasks.current
    if cur:
        tasks.add(cur)
    cov = competence_store.coverage()
    out: dict[str, Any] = {
        "why_now": moment.reasons[:4],
        "targets": [tg.to_prompt() for tg in moment.targets],
        "known_rules": {k: competence_store.describe(k, e) for k, e in entries.items()
                        if competence_store.SLOTS[k]["task"] in tasks},
        "knowledge": f"{cov['filled']}/{cov['total']} slots filled",
    }
    if any(tg.kind == "deviation" for tg in moment.targets) and runtime.pending_deviation:
        out["deviation"] = {k: runtime.pending_deviation[k] for k in ("slot", "rule", "expected", "now")}
    return out


def _qa_for_prompt() -> list[dict[str, Any]]:
    return [
        {"t": round(qa["t"], 1), "question": qa["question"], "answer": qa["answer"], "slot": qa.get("slot")}
        for qa in runtime.qa_history[-5:]
    ]


# ---- asking ---------------------------------------------------------------------------

def _agent_option(target: attention.Target, deviation: dict[str, Any] | None) -> dict[str, Any]:
    """One gap the ElevenLabs agent may ask about: what to learn, the evidence, an example."""
    spec = competence_store.SLOTS[target.slot]
    opt: dict[str, Any] = {"id": target.slot, "topic": competence_store.slot_name(target.slot), "kind": target.kind,
                           "learn": spec["learn"], "why": target.why, "example_question": target.example_question}
    if target.hypothesis:
        opt["observed_habit"] = target.hypothesis["text"]
    if target.kind == "deviation" and deviation:
        opt["deviation"] = {k: deviation[k] for k in ("rule", "expected", "now") if k in deviation}
    return opt


def _agent_cue(question: str, slot: str | None, kind: str, reasons: list[str], ep: Any,
               deviation: dict[str, Any] | None, hypothesis: dict[str, Any] | None,
               targets: list[attention.Target] | None = None) -> dict[str, Any]:
    """What the ElevenLabs agent needs to decide whether to ask now, what to ask and when it has
    understood enough: why now, the open gaps it may ask about, the measured evidence, the rules
    already known. No LLM on our side."""
    if targets:
        options = [_agent_option(tg, deviation) for tg in targets]
    elif slot:
        options = [_agent_option(attention.Target(slot, kind, reasons[0] if reasons else "", question, hypothesis), deviation)]
    else:  # the pilot pressed Ask now and nothing stands out
        options = [{"id": "", "topic": "what the pilot is doing", "kind": "rule", "example_question": question,
                    "learn": "What the pilot is doing now, why, and what a novice should know about it"}]
    cue: dict[str, Any] = {"why_now": reasons[:3], "options": options}
    tasks = {competence_store.SLOTS[o["id"]]["task"] for o in options if o["id"]}
    known = [competence_store.describe(k, e) for k, e in competence_store.load().items()
             if competence_store.SLOTS[k]["task"] in tasks]
    if known:
        cue["known_rules"] = known[:4]
    if ep is not None:
        cue["measured"] = {m: ep.signature[m] for m in TASK_METRICS.get(ep.task, []) if m in ep.signature}
    return cue


def _push_question(t: float, question: str, slot: str | None, kind: str, observation: str,
                   deviation: dict[str, Any] | None = None, hypothesis: dict[str, Any] | None = None,
                   reasons: list[str] | None = None, targets: list[attention.Target] | None = None) -> dict[str, Any]:
    """Register a question (for the answer to know its slot and episode) and build its payload.
    With the ElevenAgents voice, `question` is only an example: the agent words it (see /agent/asked)."""
    task = competence_store.SLOTS[slot]["task"] if slot else None
    ep = runtime.flight_log.latest_episode(task) if task else runtime.flight_log.current_episode()
    runtime.last_question_time = t
    cue_id = uuid.uuid4().hex[:8]
    runtime.qa_history.append({
        "t": t,
        "question": question,
        "answer": None,
        "slot": slot,
        "kind": kind,  # "rule" | "hypothesis" | "deviation" | "follow_up"
        "deviation": deviation,
        "hypothesis": hypothesis,
        "episode": {"task": ep.task, "start": ep.start} if ep else None,
        "cue_id": cue_id,
        # ElevenAgents: the gaps the agent may choose from (save_answer names the one it asked about)
        "options": {tg.slot: {"kind": tg.kind, "hypothesis": tg.hypothesis,
                              "deviation": deviation if tg.kind == "deviation" else None} for tg in targets or []},
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
        "cue_id": cue_id,
    }
    if runtime.voice_agent:
        payload["cue"] = _agent_cue(question, slot, kind, reasons or [observation], ep, deviation, hypothesis, targets)
    runtime.latest_question = payload
    if runtime.recorder and not runtime.voice_agent:  # the agent's own wording is recorded by /agent/asked
        runtime.recorder.record_transcript({"role": "apprentice_model", "text": question, "t": t, "slot": slot, "kind": kind})
    return payload


def cue_moment(t: float, moment: attention.Moment | None, force: bool = False) -> dict[str, Any] | None:
    """ElevenAgents voice: the attention model's moment becomes a cue for the agent, without calling
    Claude. The agent decides whether it is worth asking now and which of the gaps to ask about; the
    default (for the record) is a deviation, then a habit to confirm, then the first open slot."""
    if moment is None:
        return None
    targets = moment.targets
    target = (next((tg for tg in targets if tg.kind == "deviation"), None)
              or next((tg for tg in targets if tg.kind == "hypothesis"), None)
              or (targets[0] if targets else None))
    if target is None and not force:
        return None
    runtime.attention_consumed.add(moment.key)
    runtime.last_observer_call = t
    kind = target.kind if target else "rule"
    dev = runtime.pending_deviation if kind == "deviation" else None
    if kind == "deviation":
        if dev is None:
            kind = "rule"
        runtime.pending_deviation = None
    example = target.example_question if target else "What were you doing just now, and what should a novice know about it?"
    observation = moment.reasons[0] if moment.reasons else "The pilot asked for a question."
    if runtime.recorder:
        runtime.recorder.record_observation({"t": t, "forced": force, "why_now": moment.reasons, "cue_for_agent": True,
                                             "target_slot": target.slot if target else None, "kind": kind})
    payload = _push_question(t, example, target.slot if target else None, kind, observation, deviation=dev,
                             hypothesis=target.hypothesis if target else None, reasons=moment.reasons,
                             targets=[tg for tg in moment.targets if tg.kind != "deviation" or dev])
    if force and "cue" in payload:
        payload["cue"]["asked_by_pilot"] = True  # the pilot pressed Ask now: the agent must ask
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


async def observe_once(force: bool = False, moment: attention.Moment | None = None) -> dict[str, Any] | None:
    """Ask Claude about a moment; returns the question payload if one was asked."""
    epoch = runtime.session_epoch
    t0 = runtime.drone.elapsed_time
    moment = moment or assess(t0, force=force)
    if moment is None:
        return None
    runtime.observer_busy = True
    runtime.last_observer_call = t0
    try:
        flight = runtime.flight_log.observer_context(OBSERVER_WINDOW_S)
        frame = runtime.camera.get_latest_frame()
        frame_age = runtime.drone.elapsed_time - runtime.camera.latest_timestamp
        if frame and not 0 <= frame_age <= OBSERVER_FRAME_MAX_AGE_S:
            frame = None  # stale (or from a previous flight): it would contradict the telemetry
        decision = await runtime.observer.decide(
            flight, _learning(moment), _qa_for_prompt(), force=force, frame_b64=frame, frame_age_s=frame_age
        )
    finally:
        runtime.observer_busy = False

    if runtime.session_epoch != epoch or not runtime.session_active:
        logger.info("observer: flight ended during the call, result discarded")
        return None
    t = runtime.drone.elapsed_time
    if _pilot_recording(t) and not force:
        logger.info("observer: the pilot started a note during the call, question dropped")
        return None
    if runtime.off_record:
        logger.info("observer: the flight went off the record during the call, question dropped")
        return None
    if runtime.recorder:
        runtime.recorder.record_observation({"t": t, "forced": force, "why_now": moment.reasons, **decision.model_dump()})
    await broadcast({"type": "observation", "t": t, "observation": decision.observation, "asked": decision.ask_question})

    attempts = runtime.attention_attempts.get(moment.key, 0) + 1
    runtime.attention_attempts[moment.key] = attempts
    if not (decision.ask_question or force) or not decision.question:
        if attempts >= MAX_ATTEMPTS_PER_MOMENT:
            runtime.attention_consumed.add(moment.key)
        return None
    runtime.attention_consumed.add(moment.key)

    slot = decision.target_slot or None
    target = next((tg for tg in moment.targets if tg.slot == slot), None)
    kind = target.kind if target else "rule"
    dev = runtime.pending_deviation if kind == "deviation" else None
    if kind == "deviation" and dev is None:
        kind = "rule"
    runtime.pending_deviation = None if dev else runtime.pending_deviation
    hyp = target.hypothesis if target and kind == "hypothesis" else None
    return _push_question(t, decision.question, slot, kind, decision.observation, deviation=dev, hypothesis=hyp)


def _publish_attention(t: float, moment: attention.Moment | None) -> dict[str, Any] | None:
    """The apprentice's state for the interface; returned only when it changed."""
    cooldown = max(0.0, QUESTION_COOLDOWN_S - (t - runtime.last_question_time))
    summary = moment.summary() if moment else {"score": 0.0, "ready": False, "reasons": [], "targets": []}
    summary.update({
        "cooldown_s": round(cooldown),
        "thinking": runtime.observer_busy,
        "waiting_answer": _awaiting_answer(t),
        "follow_up": bool(runtime.pending_follow_up),
        "pilot_talking": _pilot_talking(t),
        "off_record": runtime.off_record,
    })
    if summary == runtime.attention:
        return None
    runtime.attention = summary
    return summary


async def observer_loop() -> None:
    while True:
        await asyncio.sleep(OBSERVER_TICK_S)
        try:
            review_episodes()
            if runtime.voice_agent and runtime.session_active and runtime.mode in ("novice", "tutor"):
                t = runtime.drone.elapsed_time
                if t - runtime.last_agent_context >= AGENT_CONTEXT_S:  # background for the tutor agent
                    runtime.last_agent_context = t
                    await broadcast({"type": "agent_context", "text": tutor_status()})
            if runtime.mode != "expert" or not runtime.session_active:
                continue
            t = runtime.drone.elapsed_time
            moment = None if runtime.off_record else assess(t)  # off the record: nothing is noticed
            changed = _publish_attention(t, moment)
            if changed:
                await broadcast({"type": "attention", **changed})
            if not _can_ask(t):
                continue
            payload = None
            if runtime.pending_follow_up:
                payload = _ask_follow_up(t)  # None while waiting for a calm moment
            elif (
                runtime.voice_agent
                and not _pilot_busy()
                and moment is not None
                and moment.score >= attention.ASK_THRESHOLD
                and t - runtime.last_observer_call >= OBSERVER_INTERVAL_S
            ):
                payload = cue_moment(t, moment)  # ElevenAgents words it: no Claude call
            elif (
                not runtime.voice_agent
                and runtime.observer.is_available
                and not _pilot_busy()
                and moment is not None
                and moment.score >= attention.ASK_THRESHOLD
                and t - runtime.last_observer_call >= OBSERVER_INTERVAL_S
                and t - runtime.attention_last_try.get(moment.key, -1e9) >= RETRY_DECLINED_S
            ):
                runtime.attention_last_try[moment.key] = t
                payload = await observe_once(moment=moment)
            if payload:
                await broadcast({"type": "question", **payload})
        except Exception:
            logger.exception("Observer iteration failed")

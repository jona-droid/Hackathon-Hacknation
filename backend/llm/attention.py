"""The apprentice's attention: is this moment worth a question, and about what? (no LLM)

Every second the flight is scored from what just happened: a road crossing, a defect found, a near
miss, a finished task, a habit seen often enough to propose as a rule, the pilot contradicting a
rule they taught... Each reason carries a weight and the competence-grid slots it could teach.
Claude is called only when the best reason clears ASK_THRESHOLD, with those reasons ("why now")
and at most MAX_TARGETS slots to choose from: fewer, cheaper, far more pointed questions than
polling the LLM every few seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.sim.flight_log import FlightLog, _circling, _revisit
from backend.sim.tasks import TASK_METRICS
from backend.storage import competence_store

ASK_THRESHOLD = 3.0
MAX_TARGETS = 3
# preferred order inside a task: limits and cues first, they are what a novice lacks most
TYPE_PRIORITY = {"threshold": 0, "cue": 1, "condition": 2, "abort": 3, "assessment": 4, "procedure": 5}


@dataclass
class Target:
    slot: str
    kind: str  # "rule" (open question) | "hypothesis" (confirm a habit) | "deviation" (what changed?)
    why: str
    example_question: str
    hypothesis: dict[str, Any] | None = None

    def to_prompt(self) -> dict[str, Any]:
        out = {"slot": self.slot, "kind": self.kind, "why": self.why, "example_question": self.example_question}
        if self.hypothesis:
            out["observed_habit"] = self.hypothesis["text"]
        return out


@dataclass
class Moment:
    score: float
    key: str  # identifies the trigger, so the same moment is not asked about twice
    reasons: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Compact view for the interface (the apprentice's "cortex" panel)."""
        return {
            "score": round(self.score, 1),
            "ready": self.score >= ASK_THRESHOLD,
            "reasons": self.reasons[:3],
            "targets": [{"slot": t.slot, "name": competence_store.slot_name(t.slot), "kind": t.kind} for t in self.targets],
        }


@dataclass
class _Reason:
    weight: float
    text: str
    key: str
    slots: list[str]
    kind: str = "rule"
    hypotheses: dict[str, dict[str, Any]] = field(default_factory=dict)
    deviation: dict[str, Any] | None = None


def _latest(log: FlightLog, types: set[str], t: float, within: float) -> dict[str, Any] | None:
    for e in reversed(log.events):
        if t - float(e.get("t", 0.0)) > within:
            return None
        if e.get("type") in types:
            return e
    return None


def _task_name(task: str) -> str:
    return competence_store.TASKS[task]["short"].lower()


def _measured_words(task: str, signature: dict[str, float]) -> str:
    keys = TASK_METRICS.get(task, [])
    return ", ".join(f"{k.replace('_median', '').replace('_min', ' min').replace('_s', '').replace('_', ' ')} {signature[k]}"
                     for k in keys if k in signature)


def assess(
    t: float,
    log: FlightLog,
    entries: dict[str, dict[str, Any]],
    qa_history: list[dict[str, Any]],
    consumed: set[str],
    deviation: dict[str, Any] | None = None,
    episodes_for: Callable[[str], list[dict[str, Any]]] | None = None,
    force: bool = False,
) -> Moment | None:
    """Score the current moment. None when nothing is worth asking (and force is False)."""
    asked = {qa.get("slot") for qa in qa_history if qa.get("slot")}

    def open_slots(task: str) -> list[str]:
        slots = [s["slot"] for s in competence_store.open_slots(task, entries) if s["slot"] not in asked]
        return sorted(slots, key=lambda k: TYPE_PRIORITY.get(competence_store.SLOTS[k]["type"], 9))

    reasons: list[_Reason] = []
    cur = log.current_episode()
    recent = list(reversed(log.tasks.recent(t)))
    tasks_in_view = list(dict.fromkeys(([cur.task] if cur else []) + [ep.task for ep in recent]))
    speed = float(log.rows[-1][4]) if log.rows else 0.0

    if deviation:
        slot = deviation["slot"]
        reasons.append(_Reason(6.0, f"the pilot just flew {_task_name(competence_store.SLOTS[slot]['task'])} differently "
                                    f"from the rule they taught (expected {deviation['expected']}, now {deviation['now']})",
                               f"dev:{slot}:{deviation['t']:.0f}", [slot], "deviation", deviation=deviation))

    crossed = _latest(log, {"road_crossed"}, t, 10.0)
    if crossed:
        slots = open_slots("road_crossing")
        if slots:
            checked = f", after checking {crossed['checked_before_s']:.0f} s before it" if crossed.get("checked_before_s", 0) >= 1.5 else ""
            reasons.append(_Reason(5.0, f"the pilot just crossed the road at {crossed['min_altitude']:.0f} m and "
                                        f"{crossed['mean_speed']:.0f} m/s{checked}", f"road:{crossed['t']:.0f}", slots))
    elif cur and cur.task == "road_crossing" and speed < 1.0 and cur.duration >= 2.0:
        slots = [s for s in open_slots("road_crossing") if s.endswith("traffic_check")] or open_slots("road_crossing")
        if slots:
            reasons.append(_Reason(4.0, f"the pilot stopped {cur.duration:.0f} s just before the road, apparently checking it",
                                   f"roadcheck:{cur.start:.0f}", slots))

    defect = _latest(log, {"defect_spotted"}, t, 15.0)
    if defect:
        slots = open_slots("defect_assessment")
        if slots:
            reasons.append(_Reason(5.0, f"the pilot just spotted a defect: {defect.get('label')} on {defect.get('target')}",
                                   f"defect:{defect.get('defect_id')}", slots))

    near_miss = _latest(log, {"very_close_cable", "conflict_predicted", "cannot_stop", "collision"}, t, 15.0)
    if near_miss and speed < 2.5:
        slots = open_slots("emergency")
        if slots:
            what = {"very_close_cable": f"came within {near_miss.get('cable_dist', 2):.1f} m of a cable",
                    "conflict_predicted": f"was on course to hit the {near_miss.get('hazard', 'line')}",
                    "cannot_stop": f"was too fast to stop before the {near_miss.get('hazard', 'line')}",
                    "collision": "crashed"}[near_miss["type"]]
            reasons.append(_Reason(4.0, f"close call: the drone {what}, and the pilot recovered", f"miss:{near_miss['t']:.0f}", slots))

    compass = _latest(log, {"compass_interference"}, t, 12.0)
    if compass:
        slots = open_slots("interference_wind")
        if slots:
            reasons.append(_Reason(3.0, f"the compass was disturbed {compass.get('cable_dist', 0):.1f} m from a cable",
                                   f"emi:{compass['t']:.0f}", slots))

    if recent:
        ep = recent[0]
        if t - ep.end <= 8.0 and ep.duration >= 3.0:
            slots = open_slots(ep.task)
            if slots:
                reasons.append(_Reason(3.0, f"the pilot just finished {_task_name(ep.task)} "
                                            f"({_measured_words(ep.task, ep.signature)})", f"ep:{ep.task}:{ep.start:.0f}", slots))

    if episodes_for:
        for task in tasks_in_view:
            hyps = {}
            for slot in open_slots(task):
                h = competence_store.hypothesis(slot, episodes_for(task))
                if h:
                    hyps[slot] = h
            if hyps:
                best = max(hyps.values(), key=lambda h: h["n"])
                reasons.append(_Reason(4.0, f"a habit seen {best['n']} times: {best['text']}", f"hyp:{best['slot']}:{best['n']}",
                                       list(hyps), "hypothesis", hypotheses=hyps))

    if cur and cur.duration >= 6.0:
        slots = open_slots(cur.task)
        if slots:
            flight_start = float(log.rows[0][0]) if log.rows else t
            last_q = max((qa["t"] for qa in qa_history if qa.get("question")), default=flight_start)
            quiet = min(1.5, max(0.0, t - last_q) / 30.0)  # silent for 45 s: worth a question
            reasons.append(_Reason(1.5 + quiet, f"the pilot has been on {_task_name(cur.task)} for {cur.duration:.0f} s",
                                   f"cur:{cur.task}:{cur.start:.0f}:{int(cur.duration // 20)}", slots))
            data = log._array()
            pattern = _circling(data) or _revisit(data)
            if pattern:
                reasons.append(_Reason(2.5, "the pilot is circling or back at a spot visited earlier",
                                       f"pattern:{cur.task}:{cur.start:.0f}", slots))

    fresh = [r for r in reasons if r.key not in consumed]
    if not fresh:
        if not force:
            return None
        task = cur.task if cur else (recent[0].task if recent else None)
        slots = open_slots(task) if task else []
        fresh = [_Reason(0.0, "the operator asked for a question now", f"force:{t:.0f}", slots)]

    fresh.sort(key=lambda r: r.weight, reverse=True)
    top = fresh[0]
    score = top.weight + 0.25 * (len(fresh) - 1)
    if score < ASK_THRESHOLD and not force:
        return Moment(score=score, key=top.key, reasons=[r.text for r in fresh])

    targets: list[Target] = []
    for r in fresh[:2]:
        for slot in r.slots:
            if any(tg.slot == slot for tg in targets):
                continue
            spec = competence_store.SLOTS[slot]
            hyp = r.hypotheses.get(slot)
            kind = r.kind if (r.kind != "hypothesis" or hyp) else "rule"
            example = hyp["example_question"] if hyp else spec["ask_example"]
            targets.append(Target(slot, kind, r.text, example, hyp))
            if len(targets) >= MAX_TARGETS:
                break
        if len(targets) >= MAX_TARGETS:
            break
    return Moment(score=score, key=top.key, reasons=[r.text for r in fresh], targets=targets)

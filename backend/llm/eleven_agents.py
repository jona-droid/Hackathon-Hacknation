"""ElevenAgents: the voice of the apprentice (interviewer) and of the tutor.

Two ElevenLabs conversational agents, created and kept up to date through the ElevenLabs API:
- interviewer: asks the expert's questions during the flight, runs the debrief and the teach-back;
- tutor: coaches the novice, asks "what would the expert do here?", judges the answer.
Their LLM is picked in ElevenAgents (ELEVENLABS_AGENT_LLM). The agent decides whether a moment is
worth a question, which gap to ask about, how to word it, when to follow up and when it has
understood enough; it listens (Scribe v2 Realtime, turn-taking) and speaks (Expressive Mode). The
backend only notices the moments and the gaps (attention model, debrief gaps, decision points: no
LLM) and sends a cue; the agent hands the answers back through client tools (save_answer,
teachback_verdict, log_prediction...) that the browser forwards to the backend. Claude only
distils the answers into rules and writes the summaries.

The API key stays on the server: the browser gets a short-lived signed URL per conversation.
Agent and tool ids are cached in data/elevenlabs_agents.json with a hash of their config, so a
changed prompt or tool updates the agents in place instead of creating new ones.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

import httpx2 as httpx

from backend.core.config import (
    DATA_DIR,
    ELEVENLABS_AGENT_LLM,
    ELEVENLABS_AGENT_TTS_MODEL,
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    VOICE_AGENT,
)
from backend.storage import competence_store

logger = logging.getLogger("robot-apprentice.agents")

PROMPTS = Path(__file__).resolve().parent / "prompts"
CACHE_PATH = DATA_DIR / "elevenlabs_agents.json"
TAG = "ai-apprentice"
# Models offered by ElevenAgents that make real tool calls (GLM 5.2 sometimes wrote them as text, spoken aloud)
FALLBACK_LLMS = ["claude-sonnet-5-5", "gemini-3.5-flash", "claude-haiku-4-5", "qwen35-397b-a17b"]
FALLBACK_TTS = ["eleven_v3_conversational", "eleven_flash_v2"]


def _param(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    return {"type": kind, "description": description, **extra}


# Client tools: run in the browser (frontend/src/voiceAgent.ts), which forwards them to the backend
CLIENT_TOOLS: dict[str, dict[str, Any]] = {
    "save_answer": {
        "description": "Call once you have understood enough of the pilot's answer (after your follow-up, if you asked one). Pass everything they answered, verbatim.",
        "parameters": {"type": "object", "required": ["answer", "slot"], "properties": {
            "answer": _param("string", "The pilot's answer (with their answer to your follow-up), verbatim, not summarised."),
            "slot": _param("string", "The id of the option you asked about, copied from the cue."),
            "understood": _param("boolean", "True if you now know the rule, why, and its limit or exception; false if part is still missing.")}},
    },
    "not_now": {
        "description": "Call instead of asking when an [ASK] moment is not worth a question now (the rule is already known, or the pilot is too busy).",
        "parameters": {"type": "object", "required": ["reason"], "properties": {
            "reason": _param("string", "Why you do not ask, in a few words.")}},
    },
    "skip_question": {
        "description": "Call when the pilot does not answer your question, does not know, or does not want to answer.",
        "parameters": {"type": "object", "required": ["reason"], "properties": {
            "reason": _param("string", "Why the question is dropped, in a few words.")}},
    },
    "save_note": {
        "description": "Call when the pilot has finished dictating a note after a [NOTE] cue. Pass the note verbatim.",
        "parameters": {"type": "object", "required": ["note"], "properties": {
            "note": _param("string", "The pilot's note, verbatim.")}},
    },
    "teachback_verdict": {
        "description": "Call when the pilot replied to your teach-back: whether they confirmed it, and every correction they made.",
        "parameters": {"type": "object", "required": ["confirmed", "corrections"], "properties": {
            "confirmed": _param("boolean", "True only if the pilot agrees the explanation is right."),
            "corrections": _param("array", "Each correction or addition, in the pilot's own words, starting with the name of the rule it changes. Empty if confirmed.",
                                  items=_param("string", "One correction in the pilot's words."))}},
    },
    "log_prediction": {
        "description": "Call when the novice answered a [PREDICT] or [WHY] question, before giving your feedback.",
        "parameters": {"type": "object", "required": ["verdict", "novice_answer"], "properties": {
            "verdict": _param("string", "right, partly or wrong", enum=["right", "partly", "wrong"]),
            "novice_answer": _param("string", "The novice's answer, verbatim.")}},
    },
}

ROLES: dict[str, dict[str, Any]] = {
    "interviewer": {
        "name": "AI Apprentice · Interviewer",
        "prompt_file": "agent_interviewer.txt",
        "tools": ["save_answer", "not_now", "skip_question", "save_note", "teachback_verdict"],
        "placeholders": {},
        "temperature": 0.4,
    },
    "tutor": {
        "name": "AI Apprentice · Tutor",
        "prompt_file": "agent_tutor.txt",
        "tools": ["log_prediction"],
        "placeholders": {"expert_rules": "The expert has not taught any rule yet."},
        "temperature": 0.3,
    },
}

_lock = threading.Lock()
_client: httpx.Client | None = None
_last_error: str | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url="https://api.elevenlabs.io", headers={"xi-api-key": ELEVENLABS_API_KEY},
                               timeout=30.0, transport=httpx.HTTPTransport(retries=1))
    return _client


def enabled() -> bool:
    return VOICE_AGENT == "elevenlabs" and bool(ELEVENLABS_API_KEY)


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _tool_config(name: str) -> dict[str, Any]:
    spec = CLIENT_TOOLS[name]
    # pre_tool_speech off: the agent must not talk (e.g. re-read its teach-back) before handing the answer over
    return {"type": "client", "name": name, "description": spec["description"], "parameters": spec["parameters"],
            "expects_response": True, "response_timeout_secs": 20, "pre_tool_speech": "off"}


def _ensure_tool(name: str, cache: dict[str, Any]) -> str:
    cfg = _tool_config(name)
    h = _hash(cfg)
    cached = cache.setdefault("tools", {}).get(name)
    if cached and cached.get("hash") == h:
        return cached["id"]
    if cached:
        r = _http().patch(f"/v1/convai/tools/{cached['id']}", json={"tool_config": cfg})
        if r.status_code < 300:
            cache["tools"][name] = {"id": cached["id"], "hash": h}
            return cached["id"]
        logger.warning("could not update tool %s (%s), creating it again", name, r.text[:200])
    r = _http().post("/v1/convai/tools", json={"tool_config": cfg})
    r.raise_for_status()
    tool_id = r.json()["id"]
    cache["tools"][name] = {"id": tool_id, "hash": h}
    return tool_id


def _agent_body(role: str, tool_ids: list[str], llm: str, tts_model: str) -> dict[str, Any]:
    spec = ROLES[role]
    agent: dict[str, Any] = {
        "first_message": "",  # silent on connect: it speaks only when the flight software cues it
        "language": "en",
        "prompt": {
            "prompt": (PROMPTS / spec["prompt_file"]).read_text(encoding="utf-8"),
            "llm": llm,
            "temperature": spec["temperature"],
            "tool_ids": tool_ids,
            "built_in_tools": {"skip_turn": {"type": "system", "name": "skip_turn", "description": "",
                                             "params": {"system_tool_type": "skip_turn"}}},
        },
    }
    if spec["placeholders"]:
        agent["dynamic_variables"] = {"dynamic_variable_placeholders": spec["placeholders"]}
    return {
        "name": spec["name"],
        "tags": [TAG],
        "conversation_config": {
            "agent": agent,
            "tts": {"voice_id": ELEVENLABS_VOICE_ID, "model_id": tts_model, "stability": 0.5, "similarity_boost": 0.8},
            "turn": {"turn_timeout": 20, "turn_eagerness": "patient"},
            "conversation": {
                "max_duration_seconds": 1800,
                # client_tool_call is not in the default list: without it the browser never sees the tools
                "client_events": ["audio", "interruption", "agent_response", "user_transcript", "agent_response_correction",
                                  "agent_tool_response", "client_tool_call", "vad_score"],
            },
        },
    }


def _ensure_agent(role: str, cache: dict[str, Any]) -> str:
    tool_ids = [_ensure_tool(t, cache) for t in ROLES[role]["tools"]]
    llms = [ELEVENLABS_AGENT_LLM] + [m for m in FALLBACK_LLMS if m != ELEVENLABS_AGENT_LLM]
    ttss = [ELEVENLABS_AGENT_TTS_MODEL] + [m for m in FALLBACK_TTS if m != ELEVENLABS_AGENT_TTS_MODEL]
    want = _hash(_agent_body(role, tool_ids, llms[0], ttss[0]))
    cached = cache.setdefault("agents", {}).get(role)
    if cached and cached.get("hash") == want:
        return cached["agent_id"]

    errors = []
    for llm in llms:
        for tts in ttss:
            body = _agent_body(role, tool_ids, llm, tts)
            if cached:
                r = _http().patch(f"/v1/convai/agents/{cached['agent_id']}", json=body)
                if r.status_code == 404:
                    cached = None  # deleted in the dashboard: create it again
                    r = _http().post("/v1/convai/agents/create", json=body)
            else:
                r = _http().post("/v1/convai/agents/create", json=body)
            if r.status_code < 300:
                agent_id = (r.json() or {}).get("agent_id") or cached["agent_id"]
                cache["agents"][role] = {"agent_id": agent_id, "hash": want, "llm": llm, "tts_model": tts}
                logger.info("ElevenLabs agent %s ready: %s (llm %s, tts %s)", role, agent_id, llm, tts)
                return agent_id
            errors.append(f"{llm}/{tts}: {r.status_code} {r.text[:160]}")
            if r.status_code in (401, 403):
                raise RuntimeError(f"ElevenLabs refused the agent ({r.status_code}): {r.text[:200]}")
    raise RuntimeError("Could not create the ElevenLabs agent: " + " | ".join(errors[:3]))


def ensure_agents() -> dict[str, Any]:
    """Create or update both agents (and their tools); returns {role: {agent_id, llm, tts_model}}."""
    global _last_error
    if not enabled():
        raise RuntimeError("ElevenAgents is off (no ELEVENLABS_API_KEY, or VOICE_AGENT is not 'elevenlabs')")
    with _lock:
        cache = _load_cache()
        try:
            for role in ROLES:
                _ensure_agent(role, cache)
            _last_error = None
        except Exception as e:
            _last_error = str(e)
            raise
        finally:
            _save_cache(cache)
        return {role: {k: v for k, v in cache["agents"][role].items() if k != "hash"} for role in ROLES}


def status() -> dict[str, Any]:
    """Whether the browser can talk to the agents; creates them on the first call."""
    if not enabled():
        return {"available": False, "reason": "ElevenAgents is off (VOICE_AGENT is not 'elevenlabs', or no ElevenLabs key on the server)."}
    try:
        agents = ensure_agents()
        return {"available": True, "agents": agents}
    except Exception as e:
        logger.exception("ElevenAgents unavailable")
        return {"available": False, "reason": str(e)}


def signed_url(role: str) -> str:
    """A short-lived URL for one conversation with the agent (the API key never reaches the browser)."""
    agent_id = ensure_agents()[role]["agent_id"]
    r = _http().get("/v1/convai/conversation/get-signed-url", params={"agent_id": agent_id})
    r.raise_for_status()
    return r.json()["signed_url"]


def expert_rules_text(max_chars: int = 6000) -> str:
    """The expert's rules in their own words, for the tutor's prompt ({{expert_rules}})."""
    entries = competence_store.load()
    lines = []
    for task in competence_store.GRID["tasks"]:
        for s in task["slots"]:
            key = f"{task['id']}.{s['id']}"
            entry = entries.get(key)
            if not entry:
                continue
            line = f"- {task['short']} / {s['label']}: {entry['rule']}"
            for c in entry.get("conditions") or []:
                line += f" Exception: {c}"
            said = competence_store.quote(entry)
            if said:
                line += f' The expert said: "{said["text"][:240]}"'
            lines.append(line)
    text = "\n".join(lines) or "The expert has not taught any rule yet: coach with general safety and say so."
    return text[:max_chars]

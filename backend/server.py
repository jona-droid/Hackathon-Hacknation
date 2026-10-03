from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.expert import EXPERT_INTERVIEW_QUESTIONS
from backend.flight import FlightSim
from backend.guardrails import check, default_guardrails, validate_guardrails
from backend.recorder import append_transcript, replay_state, start_session
from backend.sim import EventDetector, Scene, scene_json, snapshot
from backend.workmap import generate_work_map, save_work_map

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("robot-apprentice")


@dataclass
class SimRuntime:
    scene: Scene = field(default_factory=Scene)
    drone: FlightSim = field(init=False)
    detector: EventDetector = field(init=False)
    mode: str = "expert"
    guardrails: list[dict[str, Any]] = field(default_factory=default_guardrails)
    keys_down: set[str] = field(default_factory=set)
    clients: set[WebSocket] = field(default_factory=set)
    recorder: Any = None

    def __post_init__(self) -> None:
        self.drone = FlightSim(self.scene)
        self.detector = EventDetector(self.scene)

    def reset(self) -> None:
        self.mode = "expert"
        self.keys_down = set()
        self.drone.reset()
        self.detector.reset()


runtime = SimRuntime()
app = FastAPI(title="Robot Apprentice API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _broadcast(payload: dict[str, Any]) -> None:
    stale = []
    for ws in runtime.clients:
        try:
            await ws.send_json(payload)
        except Exception:
            stale.append(ws)
    for ws in stale:
        runtime.clients.discard(ws)


async def _emit_event(ev: dict[str, Any]) -> None:
    await _broadcast({"type": "event", **ev})
    if runtime.recorder:
        runtime.recorder.record_event(ev)


async def sim_loop() -> None:
    tick = 1.0 / 60.0
    frame_dt = 1.0 / 60.0
    last_frame = 0.0
    last_wall = time.perf_counter()
    while True:
        try:
            now = time.perf_counter()
            runtime.drone.set_keys(runtime.keys_down)
            runtime.drone.advance(now - last_wall)
            last_wall = now
            t = runtime.drone.t

            events = runtime.detector.update(runtime.drone, t)
            for ev in events:
                await _emit_event(ev)

            state = snapshot(runtime.drone, runtime.scene)
            state["mode"] = runtime.mode
            state["t"] = t
            state["inspected_count"] = len(runtime.detector.inspected)
            state["violations"] = check(state, runtime.guardrails, runtime.scene)

            if runtime.recorder:
                runtime.recorder.record_state(t, state)

            if t < last_frame or t - last_frame >= frame_dt:
                await _broadcast({"type": "state", **state})
                last_frame = t

            await asyncio.sleep(tick)
        except Exception:
            logger.exception("Sim loop error")
            await asyncio.sleep(0.05)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(sim_loop())


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime.clients.add(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "keys":
                runtime.keys_down = set(data.get("down", []))
    except WebSocketDisconnect:
        runtime.clients.discard(websocket)
    except Exception:
        logger.exception("Websocket handler error")
        runtime.clients.discard(websocket)


@app.post("/session/start")
async def session_start(payload: dict[str, str]) -> dict[str, Any]:
    requested_mode = payload.get("mode", "expert")
    if requested_mode != "expert":
        raise HTTPException(status_code=400, detail="Only Expert mode is currently available")
    runtime.reset()
    runtime.recorder = start_session("expert")
    return {"session_id": runtime.recorder.session_id, "mode": "expert"}


@app.post("/session/stop")
async def session_stop() -> dict[str, Any]:
    if not runtime.recorder:
        raise HTTPException(status_code=400, detail="No active session")
    sid = runtime.recorder.session_id
    runtime.recorder.stop()
    runtime.recorder = None
    return {"session_id": sid}


@app.post("/session/{session_id}/transcript")
async def add_transcript(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if runtime.recorder and runtime.recorder.session_id == session_id:
        runtime.recorder.record_transcript(payload)
    else:
        append_transcript(session_id, payload)
    return {"ok": True}


@app.post("/session/{session_id}/workmap")
async def make_workmap(session_id: str) -> dict[str, Any]:
    result = await asyncio.to_thread(generate_work_map, session_id)
    save_work_map(session_id, result)
    return result


@app.get("/guardrails")
async def get_guardrails() -> dict[str, Any]:
    return {"guardrails": runtime.guardrails}


@app.get("/expert/questions")
async def expert_questions() -> dict[str, Any]:
    return {"questions": EXPERT_INTERVIEW_QUESTIONS}


@app.put("/guardrails")
async def put_guardrails(payload: dict[str, Any]) -> dict[str, Any]:
    guardrails = validate_guardrails(payload.get("guardrails", []))
    if not guardrails:
        raise HTTPException(status_code=400, detail="No valid guardrails")
    runtime.guardrails = guardrails
    return {"guardrails": runtime.guardrails}


@app.get("/scene")
async def get_scene() -> dict[str, Any]:
    return scene_json(runtime.scene)


@app.get("/session/{session_id}/replay")
async def replay(session_id: str, t: float) -> dict[str, Any]:
    state = replay_state(session_id, t)
    if state is None:
        raise HTTPException(status_code=404, detail="No telemetry")
    return state


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": runtime.mode,
        "clients": len(runtime.clients),
        "simulator": "pyflyt",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.server:app", host="0.0.0.0", port=8000, reload=False)

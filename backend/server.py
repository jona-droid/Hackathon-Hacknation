from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.guardrails import check, default_guardrails, validate_guardrails
from backend.mission import MissionPlanner
from backend.mpc import solve_mpc
from backend.predictor import WarningLatch
from backend.recorder import append_transcript, replay_state, start_session
from backend.sim import Drone, EventDetector, Scene, scene_json, snapshot
from backend.workmap import generate_work_map, save_work_map

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("robot-apprentice")


@dataclass
class SimRuntime:
    scene: Scene = field(default_factory=Scene)
    drone: Drone = field(default_factory=Drone)
    detector: EventDetector = field(init=False)
    mode: str = "expert"
    t: float = 0.0
    guardrails: list[dict[str, Any]] = field(default_factory=default_guardrails)
    keys_down: set[str] = field(default_factory=set)
    clients: set[WebSocket] = field(default_factory=set)
    recorder: Any = None
    warning_latch: WarningLatch = field(default_factory=WarningLatch)
    mission: MissionPlanner = field(init=False)
    active_warning: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.detector = EventDetector(self.scene)
        self.mission = MissionPlanner(self.scene, self.guardrails)

    def reset(self, mode: str) -> None:
        self.mode = mode
        self.t = 0.0
        self.drone.reset()
        self.detector.reset()
        self.warning_latch = WarningLatch()
        self.active_warning = None
        self.mission = MissionPlanner(self.scene, self.guardrails)
        self.mission.reset(self.drone.pos.copy())


runtime = SimRuntime()
app = FastAPI(title="Robot Apprentice API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _keys_to_cmd(keys: set[str]) -> np.ndarray:
    k = {x.lower() for x in keys}
    vx = 0.0
    vy = 0.0
    vz = 0.0
    if "w" in k or "arrowup" in k:
        vx += 4.0
    if "s" in k or "arrowdown" in k:
        vx -= 4.0
    if "a" in k or "arrowleft" in k:
        vy += 4.0
    if "d" in k or "arrowright" in k:
        vy -= 4.0
    if " " in k or "space" in k:
        vz += 2.0
    if "shift" in k:
        vz -= 2.0
    return np.array([vx, vy, vz], dtype=float)


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
    sim_dt = 1.0 / 50.0
    frame_dt = 1.0 / 30.0
    last_frame = 0.0
    while True:
        try:
            if runtime.mode == "autonomous":
                mission_state = runtime.mission.current_reference(runtime.drone.pos.copy(), runtime.t)
                p_ref = np.array(mission_state.get("p_ref", runtime.drone.pos.tolist()))
                mpc = solve_mpc(runtime.drone.pos.copy(), runtime.drone.vel.copy(), p_ref, runtime.guardrails, runtime.scene)
                runtime.drone.v_cmd = mpc.v_cmd
                if mission_state.get("done"):
                    runtime.drone.v_cmd = np.zeros(3)
            else:
                cmd = _keys_to_cmd(runtime.keys_down)
                if runtime.mode == "tutor":
                    path, warning = runtime.warning_latch.update(runtime.drone, cmd, runtime.scene, runtime.guardrails)
                    runtime.active_warning = warning
                    if warning:
                        await _broadcast(warning)
                    if warning and warning.get("in_s", 9) < 0.7:
                        cmd = np.zeros(3)
                        await _broadcast({"type": "assist", "enabled": True})
                    await _broadcast({"type": "predicted_path", "points": path})
                runtime.drone.v_cmd = cmd

            runtime.drone.step(sim_dt, wind_enabled=True)
            runtime.t += sim_dt
            events = runtime.detector.update(runtime.drone, runtime.t)
            for ev in events:
                await _emit_event(ev)

            state = snapshot(runtime.drone, runtime.scene)
            state["mode"] = runtime.mode
            state["t"] = runtime.t
            state["inspected_count"] = len(runtime.detector.inspected)
            state["violations"] = check(state, runtime.guardrails, runtime.scene)

            if runtime.recorder:
                runtime.recorder.record_state(runtime.t, state)

            if runtime.t - last_frame >= frame_dt:
                await _broadcast({"type": "state", **state})
                last_frame = runtime.t

            await asyncio.sleep(sim_dt)
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
    mode = payload.get("mode", "expert")
    if mode not in {"expert", "tutor", "autonomous"}:
        raise HTTPException(status_code=400, detail="Invalid mode")
    runtime.reset(mode)
    runtime.recorder = start_session(mode)
    return {"session_id": runtime.recorder.session_id, "mode": mode}


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
    return {"ok": True, "mode": runtime.mode, "clients": len(runtime.clients)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.server:app", host="0.0.0.0", port=8000, reload=False)

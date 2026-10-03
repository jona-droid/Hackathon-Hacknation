from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import dialogue_router, knowledge_router, session_router, ws_router, broadcast
from backend.core.config import BROADCAST_HZ, SIM_HZ
from backend.core.state import runtime
from backend.sim.scene import scene_json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("robot-apprentice.server")

app = FastAPI(title="Robot Apprentice - Electric Cable Inspection API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(session_router)
app.include_router(dialogue_router)
app.include_router(knowledge_router)
app.include_router(ws_router)


QUESTION_TRIGGER_EVENTS = {
    "hover_start",
    "sudden_deceleration",
    "very_close_cable",
    "insulator_inspected",
    "over_road",
}

ADVICE_TRIGGER_EVENTS = {
    "near_cable",
    "very_close_cable",
    "over_road",
    "hover_start",
    "collision",
}


async def _handle_expert_event(ev: dict[str, Any], telemetry: dict[str, Any]) -> None:
    now = time.time()
    if ev.get("type") in QUESTION_TRIGGER_EVENTS and (now - runtime.last_question_time > 8.0):
        runtime.last_question_time = now
        try:
            frame = runtime.camera.get_latest_frame()
            q_res = await asyncio.to_thread(
                runtime.questioner.generate_question,
                event=ev,
                telemetry=telemetry,
                image_b64=frame,
            )
            runtime.latest_question = q_res
            await broadcast({"type": "question", **q_res})
            if runtime.recorder:
                runtime.recorder.record_transcript({
                    "role": "apprentice_model",
                    "text": q_res["question"],
                    "t": telemetry["t"],
                    "event": ev,
                })
        except Exception:
            logger.exception("Error generating expert question")


async def _handle_novice_event(ev: dict[str, Any], telemetry: dict[str, Any]) -> None:
    now = time.time()
    if ev.get("type") in ADVICE_TRIGGER_EVENTS and (now - runtime.last_advice_time > 6.0):
        runtime.last_advice_time = now
        try:
            frame = runtime.camera.get_latest_frame()
            advice_res = await asyncio.to_thread(
                runtime.advisor.advise,
                telemetry=telemetry,
                image_b64=frame,
                event=ev,
            )
            runtime.latest_advice = advice_res
            await broadcast({"type": "advice", **advice_res})
            if runtime.recorder:
                runtime.recorder.record_transcript({
                    "role": "tutor_model",
                    "text": advice_res.get("speech", ""),
                    "t": telemetry["t"],
                    "advice": advice_res,
                })
        except Exception:
            logger.exception("Error generating novice advice")


async def sim_loop() -> None:
    dt = 1.0 / SIM_HZ
    broadcast_interval = 1.0 / BROADCAST_HZ
    last_broadcast = 0.0

    while True:
        try:
            runtime.drone.set_keys(runtime.keys_down)
            runtime.drone.step(dt, wind_enabled=True)

            events = runtime.detector.update(runtime.drone, dt)
            state = runtime.drone.snapshot()
            state["mode"] = runtime.mode
            state["inspected_count"] = len(runtime.detector.inspected)

            for ev in events:
                await broadcast({"type": "event", **ev})
                if runtime.recorder:
                    runtime.recorder.record_event(ev)

                # Event-driven AI interaction
                if runtime.mode == "expert":
                    asyncio.create_task(_handle_expert_event(ev, state))
                elif runtime.mode in {"novice", "tutor"}:
                    asyncio.create_task(_handle_novice_event(ev, state))

            if runtime.recorder:
                runtime.recorder.record_state(state["t"], state)

            now = time.perf_counter()
            if now - last_broadcast >= broadcast_interval:
                await broadcast({"type": "state", **state})
                last_broadcast = now

            await asyncio.sleep(dt)
        except Exception:
            logger.exception("Sim loop iteration error")
            await asyncio.sleep(0.05)


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(sim_loop())


@app.get("/scene")
async def get_scene() -> dict[str, Any]:
    return scene_json(runtime.scene)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": runtime.mode,
        "clients": len(runtime.clients),
        "drone_time": round(runtime.drone.elapsed_time, 2),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.server:app", host="0.0.0.0", port=8000, reload=False)


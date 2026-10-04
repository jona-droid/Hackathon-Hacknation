# Hackathon Hacknation

This project is a drone inspection simulation with a FastAPI backend, a React/Vite frontend, and AI-assisted guidance for expert and novice workflows.

## Project structure

```text
.
├── backend/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dialogue_routes.py
│   │   ├── observer.py
│   │   ├── knowledge_routes.py
│   │   ├── session_routes.py
│   │   └── ws.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   └── state.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── advisor.py
│   │   ├── client.py
│   │   ├── comparator.py
│   │   ├── knowledge_manager.py
│   │   ├── observer.py
│   │   ├── summarizer.py
│   │   └── prompts/
│   ├── sim/
│   │   ├── __init__.py
│   │   ├── camera.py
│   │   ├── defects.py
│   │   ├── detector.py
│   │   ├── drone.py
│   │   ├── flight_log.py
│   │   ├── scene.py
│   │   ├── tasks.py
│   │   └── wind.py
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── comparison_store.py
│   │   ├── competence_store.py
│   │   ├── knowledge_store.py
│   │   └── session_recorder.py
│   ├── __init__.py
│   ├── expert.py
│   ├── guardrails.py
│   ├── recorder.py
│   ├── requirements.txt
│   ├── server.py
│   └── workmap.py
├── data/
│   ├── comparisons/
│   ├── knowledge/
│   └── sessions/
├── frontend/
│   ├── src/
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── ...
├── README.md
└── ...
```

## Overview

The app simulates a drone flying an inspection route around power line infrastructure. The backend runs the simulation, emits telemetry, and triggers AI assistance based on events. The frontend renders the live scene, HUD, minimap, and conversation panels while streaming state from the server.

The project supports different operator modes:

- expert mode: manual flight; an LLM observer watches the telemetry and decides when to ask the pilot a question
- novice mode: AI guidance and advice during flight
- tutor mode: coaching-style guidance for training scenarios

## Backend

The backend is a Python FastAPI service centered on [backend/server.py](backend/server.py). It exposes:

- a websocket stream for live state and event updates
- REST routes for sessions, dialogue, and knowledge
- the simulation loop and event-driven LLM hooks
- storage for sessions, comparisons, and transcripts under [data/](data/)

Key backend areas:

- [backend/api](backend/api): HTTP and websocket API routes
- [backend/core](backend/core): runtime configuration and shared state
- [backend/llm](backend/llm): question generation, advice, knowledge management, and prompts
- [backend/sim](backend/sim): drone, scene, detector, and camera logic
- [backend/storage](backend/storage): persistent session and knowledge storage

### Expert-mode observer (telemetry → Claude → ElevenLabs)

1. Every simulation step, [backend/sim/drone.py](backend/sim/drone.py) snapshots position, speed, acceleration, heading and clearances (altitude, distance and height relative to the nearest cable, distance to the nearest pylon, tree, insulator and road).
2. [backend/sim/flight_log.py](backend/sim/flight_log.py) keeps every sample since takeoff (10 Hz) and computes whole-flight patterns in Python: returning to an earlier position, flying in circles, insulators approached, and a coarse path overview. The same telemetry is also written to `data/sessions/<id>/telemetry.jsonl`.
3. Every `OBSERVER_INTERVAL_S` (3 s), [backend/api/observer.py](backend/api/observer.py) sends the last `OBSERVER_WINDOW_S` (5 s) of telemetry, the whole-flight patterns, the recent questions and answers, and the latest camera frame to `OBSERVER_MODEL` (Claude Haiku 4.5). The browser captures a frame from the 3D view every ~2 s; it is only sent if it is less than `OBSERVER_FRAME_MAX_AGE_S` (5 s) old, otherwise Claude relies on telemetry alone. The prompt is in [backend/llm/prompts/observer.txt](backend/llm/prompts/observer.txt).
4. Claude returns structured output `{observation, ask_question, target_slot, question}`. When it asks, the exact question goes over the websocket and the frontend speaks it word for word through ElevenLabs text-to-speech (`/elevenlabs/tts`).
5. When the question has been spoken, a beep sounds and the microphone opens for one answer only ([frontend/src/recordAnswer.ts](frontend/src/recordAnswer.ts)). It closes after 2 s of silence, after 8 s if nothing is said, or after 30 s at most. **Enter** finishes early and **Esc** skips. The mic is off the rest of the time. Permission is asked once, on **Start Flight**.
6. The recording goes to `/dialogue/voice-answer`, ElevenLabs speech-to-text (`scribe_v2`) transcribes it in any language, and Claude Opus 5.5 turns it into an English entry in `knowledge.md`. Answers with no usable know-how (small talk, off-topic remarks) are rejected. Typed answers use `/dialogue/answer` and follow the same path.

ElevenLabs is only used as a voice, to speak and to transcribe; no ElevenLabs agent or LLM is involved. Claude models: Haiku 4.5 for the observer and the novice tutor (frequent calls), Opus 5.5 for knowledge, flight summary and comparison.

The observer only runs during an active expert session, while the drone is airborne. It skips a round while a question is waiting for an answer (`UNANSWERED_QUESTION_TIMEOUT_S`, default 30 s) and for `QUESTION_COOLDOWN_S` (default 15 s) after each question. Every decision is logged to `data/sessions/<id>/observer.jsonl`. "Ask Question Now" forces a question. Each call sends about 4k input tokens; with Haiku 4.5 that is roughly $0.004 per call, at most about $5 per hour of continuous flying.

## Frontend

The frontend lives in [frontend](frontend) and is built with Vite + React + TypeScript. It includes the live 3D scene, HUD, voice panel, knowledge viewer, work map, and simulation controls.

Main frontend entry points:

- [frontend/src/App.tsx](frontend/src/App.tsx)
- [frontend/src/Hud.tsx](frontend/src/Hud.tsx)
- [frontend/src/Scene3D.tsx](frontend/src/Scene3D.tsx)
- [frontend/src/KnowledgeViewer.tsx](frontend/src/KnowledgeViewer.tsx)
- [frontend/src/useSimSocket.ts](frontend/src/useSimSocket.ts)

## Local setup

### 1) Create a Python environment

```bash
cd /Users/jules/Desktop/Hackathon-Hacknation
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### 2) Install frontend dependencies

```bash
cd frontend
npm install
```

## Run the app

### Start the backend

From the project root:

```bash
source .venv/bin/activate
uvicorn backend.server:app --reload --port 8000
```

The API is served by FastAPI on:

- http://localhost:8000
- websocket: ws://localhost:8000/ws

### Start the frontend

```bash
cd frontend
npm run dev
```

Then open the Vite URL shown in the terminal, usually:

- http://localhost:5173

## Deploy on Render

[render.yaml](render.yaml) defines two services: `robot-apprentice-api` (FastAPI web service) and `robot-apprentice-frontend` (static site). In Render, choose **New > Blueprint**, select this repo, and fill in the secret env vars. Set `VITE_API_URL` on the frontend to the backend's public URL (e.g. `https://robot-apprentice-api.onrender.com`). It is baked in at build time, so redeploy the frontend whenever you change it.

## Data and session storage

Runtime artifacts are stored under [data](data):

- [data/sessions](data/sessions): recorded sessions, transcripts, telemetry, frames, and summaries
- [data/knowledge](data/knowledge): knowledge base content
- [data/comparisons](data/comparisons): comparison outputs and stored examples

## Notes

- Keep secrets and local environment variables out of version control.
- The backend currently boots the simulation on startup and broadcasts repeated state updates to connected clients.
- The project is organized around modular AI and simulation components, so most feature work happens in the backend LLM/simulation layer and the frontend presentation layer.

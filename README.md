# Hackathon Hacknation

This project is a drone inspection simulation with a FastAPI backend, a React/Vite frontend, and AI-assisted guidance for expert and novice workflows.

## Project structure

```text
.
├── backend/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dialogue_routes.py
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
│   │   ├── questioner.py
│   │   ├── summarizer.py
│   │   └── prompts/
│   ├── sim/
│   │   ├── __init__.py
│   │   ├── camera.py
│   │   ├── detector.py
│   │   ├── drone.py
│   │   └── scene.py
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── comparison_store.py
│   │   ├── knowledge_store.py
│   │   └── session_recorder.py
│   ├── __init__.py
│   ├── expert.py
│   ├── flight.py
│   ├── guardrails.py
│   ├── recorder.py
│   ├── requirements.txt
│   ├── server.py
│   └── sim.py
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

- expert mode: manual flight and event-triggered questions
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

## Data and session storage

Runtime artifacts are stored under [data](data):

- [data/sessions](data/sessions): recorded sessions, transcripts, telemetry, frames, and summaries
- [data/knowledge](data/knowledge): knowledge base content
- [data/comparisons](data/comparisons): comparison outputs and stored examples

## Notes

- Keep secrets and local environment variables out of version control.
- The backend currently boots the simulation on startup and broadcasts repeated state updates to connected clients.
- The project is organized around modular AI and simulation components, so most feature work happens in the backend LLM/simulation layer and the frontend presentation layer.

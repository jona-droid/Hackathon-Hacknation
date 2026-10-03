# Robot Apprentice

Voice-powered AI apprentice for power-line drone inspection simulation.

## Stack
- Simulation: [PyFlyt](https://pypi.org/project/PyFlyt/) (PyBullet) quadrotor physics, all installed from PyPI
- Backend: Python + FastAPI + NumPy + Anthropic
- Frontend: Vite + React + TypeScript + three.js; first-person (POV) view, HUD and minimap

## Run backend
```bash
python3 -m venv .venv
source .venv/bin/activate
CFLAGS="-Dfdopen=fdopen" pip install -r backend/requirements.txt
uvicorn backend.server:app --reload --port 8000
```

`CFLAGS="-Dfdopen=fdopen"` is needed on recent macOS: pybullet has no macOS
wheel and its bundled zlib does not compile against the current SDK without it.

The backend runs a PyFlyt `QuadX` drone (flight mode 4: forward/left velocity,
yaw rate, altitude hold). Pylons, insulators and the tree are PyBullet collision
bodies; touching one (or a cable) cuts the motors and the drone falls.

## Run frontend
```bash
cd frontend
npm install
npm run dev
```

## Current mode

- Expert only: manual first-person flight — W/S or ↑/↓ forward/back, A/D strafe, Q/E or ←/→ turn, Space up, Shift down
- Tutor is intentionally deferred until the Expert interview/question flow is stable.
- Autonomous flight and MPC have been removed.

## Architecture
- WebSocket `ws://localhost:8000/ws` for state/events/keys
- REST endpoints for session lifecycle, transcripts, work map generation, guardrails, scene, replay
- Session recordings stored in `data/sessions/<session_id>/`

## Notes
- Keep secrets in `.env`; never commit secrets.
- If Anthropic call fails, default guardrails and fallback work map are used.

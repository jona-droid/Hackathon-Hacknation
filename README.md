# Robot Apprentice

Voice-powered AI apprentice for power-line drone inspection simulation.

## Stack
- Backend: Python 3.11 + FastAPI + NumPy + CVXPY/OSQP + Anthropic
- Frontend: Vite + React + TypeScript + react-three-fiber + ElevenLabs React SDK

## Run backend
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.server:app --reload --port 8000
```

## Run frontend
```bash
cd frontend
npm install
npm run dev
```

## Modes
- Expert: manual keyboard flight (WASD/arrows, Space up, Shift down)
- Tutor: expert guardrails + predicted violation warnings
- Autonomous: mission with MPC tracking and guardrail constraints

## Architecture
- WebSocket `ws://localhost:8000/ws` for state/events/keys
- REST endpoints for session lifecycle, transcripts, work map generation, guardrails, scene, replay
- Session recordings stored in `data/sessions/<session_id>/`

## Notes
- Keep secrets in `.env`; never commit secrets.
- If Anthropic call fails, default guardrails and fallback work map are used.

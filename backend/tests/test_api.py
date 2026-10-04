from fastapi.testclient import TestClient

from backend.core.state import runtime
from backend.server import app

client = TestClient(app)  # without "with": the simulation and observer loops are not started


def test_scene_health_and_knowledge():
    assert len(client.get("/scene").json()["trees"]) == 3
    assert client.get("/health").json()["ok"]
    grid = client.get("/knowledge/competence").json()
    assert grid["coverage"]["total"] == 29 and len(grid["tasks"]) == 10


def test_flight_lifecycle_note_and_debrief():
    start = client.post("/session/start", json={"mode": "expert"}).json()
    assert runtime.session_active and start["mode"] == "expert"
    assert client.post("/dialogue/pilot-note?active=true").json()["ok"]
    assert runtime.pilot_note_since is not None
    answer = client.post("/dialogue/answer", json={"answer": "I keep five metres from the strings"}).json()
    assert answer["ok"] and answer["rejected"]  # no API key in tests: nothing is stored, nothing breaks
    assert client.post("/dialogue/pilot-note?active=false").json()["ok"] and runtime.pilot_note_since is None
    stop = client.post("/session/stop").json()
    assert stop["summary"]["session_id"] == start["session_id"]
    assert "guardian_interventions" in stop["summary"] and stop["summary"]["operational_summary"]
    assert not runtime.session_active


def test_guardian_switch():
    client.post("/session/start", json={"mode": "novice"})
    assert client.post("/guardian", json={"enabled": True}).json()["armed"]
    assert not client.post("/guardian", json={"enabled": False}).json()["armed"]
    client.post("/guardian", json={"enabled": True})
    client.post("/session/stop")
    assert client.get("/ai/usage").json()["flight"]["calls"] == 0

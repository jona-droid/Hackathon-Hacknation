import json

from backend.llm.advisor import coaching_hint, predictive_alert, safety_alert
from backend.sim.detector import EventDetector
from backend.sim.flight_log import FlightLog
from backend.tests.conftest import fly_path


def test_predictive_alerts_are_short_and_actionable():
    road = predictive_alert({"type": "road_ahead", "in_s": 2.1, "alt": 12.0, "low": True, "action": "brake_climb"})
    assert "Road ahead" in road["speech"] and "climb" in road["speech"]
    hit = predictive_alert({"type": "conflict_predicted", "hazard": "tower", "in_s": 1.6, "action": "brake"})
    assert hit["urgency"] == "high" and "tower" in hit["speech"] and "brake" in hit["speech"]
    assert predictive_alert({"type": "guardian_engaged", "action": "brake_climb"})["speech"].startswith("Guardian")
    assert predictive_alert({"type": "hover_start"}) is None


def test_cable_alarm_and_coaching():
    state = {"cable_dist": 1.5, "pos": [10, -4.4, 22], "nearest_cable_point": [10, -3, 22], "vel": [0, 0, 0], "yaw": 0.0}
    assert safety_alert(state)["urgency"] == "high"
    hint = coaching_hint({"remaining_insulators": [{"id": "i3", "distance_m": 40, "direction": "ahead to your left",
                                                     "height_above_drone_m": 8}]})
    assert "i3" in hint["speech"] and "above you" in hint["speech"]


def test_observer_context_is_compact_json(calm_drone, scene):
    log = FlightLog()
    fly_path(calm_drone, log, EventDetector(scene), [(1, -10, -10, 0), (5, -10, -10, 26), (8, 60, -12, 26), (2, 96, -12, 12)])
    log.prediction = {"risk": "low", "path": [[0, 0, 0]], "stop_dist": 3.0}
    text = json.dumps(log.observer_context(5.0))
    ctx = json.loads(text)
    assert ctx["flight_story"] and "path" not in (ctx["prediction"] or {})
    assert len(text) < 6000  # about 1.5k tokens

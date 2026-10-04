from backend.llm import attention
from backend.sim.detector import EventDetector
from backend.sim.flight_log import FlightLog
from backend.storage import competence_store, episode_store
from backend.tests.conftest import fly_path


def _log(calm_drone, scene, waypoints):
    log = FlightLog()
    fly_path(calm_drone, log, EventDetector(scene), waypoints)
    return log


def setup_function():
    competence_store.reset()
    episode_store.reset()


def test_road_crossing_triggers_a_road_question(calm_drone, scene):
    log = _log(calm_drone, scene, [(1, -10, -10, 0), (5, -10, -10, 26), (8, 60, -12, 26), (3, 76, -12, 26),
                                   (3, 76, -12, 26), (2, 96, -12, 26), (1, 98, -12, 26)])
    m = attention.assess(calm_drone.elapsed_time, log, {}, [], set())
    assert m is not None and m.score >= 5.0
    assert m.targets[0].slot.startswith("road_crossing.")
    assert "crossed the road" in m.reasons[0]
    assert attention.assess(calm_drone.elapsed_time, log, {}, [], {m.key}).key != m.key  # asked once only


def test_pause_far_from_the_line_is_not_worth_a_question(calm_drone, scene):
    log = _log(calm_drone, scene, [(1, -10, -10, 0), (4, -10, -10, 18), (10, -10, -10, 18)])
    m = attention.assess(calm_drone.elapsed_time, log, {}, [], set())
    assert m is None or m.score < attention.ASK_THRESHOLD


def test_deviation_comes_first(calm_drone, scene):
    log = _log(calm_drone, scene, [(1, -10, -10, 0), (5, -10, -10, 28), (4, -6, -6, 27), (2, -5.6, -3, 26), (8, -5.6, -3, 26)])
    dev = {"slot": "insulator_inspection.inspection_standoff", "expected": {"insulator_dist_median": 5.6},
           "now": {"insulator_dist_median": 2.9}, "rule": "Hold 5 to 6 m.", "t": calm_drone.elapsed_time}
    m = attention.assess(calm_drone.elapsed_time, log, {}, [], set(), deviation=dev)
    assert m.targets[0].kind == "deviation" and m.score >= 6.0


def test_habit_becomes_a_hypothesis(calm_drone, scene):
    for start in (10.0, 40.0):
        episode_store.append("old", "insulator_inspection", start, 9.0, {"insulator_dist_median": 5.6, "cable_dz_median": 1.0, "duration_s": 9.0})
    log = _log(calm_drone, scene, [(1, -10, -10, 0), (5, -10, -10, 28), (4, -6, -6, 27), (2, -5.6, -3, 26), (8, -5.6, -3, 26)])
    m = attention.assess(calm_drone.elapsed_time, log, {}, [], set(), episodes_for=episode_store.by_task)
    hyp = [t for t in m.targets if t.kind == "hypothesis"]
    assert hyp and "I noticed" in hyp[0].example_question

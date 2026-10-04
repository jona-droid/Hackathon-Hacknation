from backend.sim.detector import EventDetector
from backend.sim.flight_log import FlightLog
from backend.tests.conftest import fly_path


def _flight(calm_drone, scene, waypoints):
    log, det = FlightLog(), EventDetector(scene)
    fly_path(calm_drone, log, det, waypoints)
    eps = [ep for ep in log.tasks.finished if ep.duration >= 2.0]
    cur = log.current_episode()
    return log, eps + ([cur] if cur else [])


def test_take_off_approach_and_insulator_inspection(calm_drone, scene):
    log, eps = _flight(calm_drone, scene, [(1, -10, -10, 0), (5, -10, -10, 28), (4, -6, -6, 27), (2, -5.6, -3, 26), (8, -5.6, -3, 26)])
    assert [ep.task for ep in eps] == ["preflight_takeoff", "structure_approach", "insulator_inspection"]
    ins = eps[-1]
    assert ins.target == "i1" and 5.0 < ins.signature["insulator_dist_median"] < 6.5


def test_leaving_a_tower_is_not_an_approach(calm_drone, scene):
    _, eps = _flight(calm_drone, scene, [(1, -10, -10, 0), (5, -10, -10, 28), (4, -6, -6, 27), (2, -5.6, -3, 26),
                                         (6, -5.6, -3, 26), (3, -5.6, -14, 28)])
    assert [ep.task for ep in eps].count("structure_approach") == 1


def test_pause_before_the_road_then_crossing(calm_drone, scene):
    log, eps = _flight(calm_drone, scene, [(1, -10, -10, 0), (5, -10, -10, 26), (8, 60, -12, 26), (3, 76, -12, 26),
                                           (4, 76, -12, 26), (2, 96, -12, 26), (1, 98, -12, 26)])
    assert "road_crossing" in [ep.task for ep in eps]
    crossed = [e for e in log.events if e["type"] == "road_crossed"]
    assert len(crossed) == 1 and crossed[0]["checked_before_s"] >= 3.0 and not crossed[0]["low"]

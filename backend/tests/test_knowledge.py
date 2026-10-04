import json

from backend.storage import competence_store, episode_store
from backend.storage.knowledge_store import get_knowledge

SLOT = "insulator_inspection.inspection_standoff"


def _reset():
    competence_store.reset()
    episode_store.reset()


def test_fill_coverage_and_markdown():
    _reset()
    competence_store.fill(SLOT, "Hold 5 to 6 m from the string.", ["With no wind, you may go closer."], "compass",
                          {"insulator_dist_median": 5.6, "cable_dz_median": 1.0}, "q", "a", "s1", 10.0, False)
    assert competence_store.coverage()["filled"] == 1
    md = get_knowledge()
    assert "## 5. Expert Competence Grid" in md and "Hold 5 to 6 m" in md and "With no wind" in md


def test_later_episodes_confirm_or_contradict_the_rule():
    _reset()
    competence_store.fill(SLOT, "Hold 5 to 6 m.", [], "", {"insulator_dist_median": 5.6, "cable_dz_median": 1.0},
                          "q", "a", "s1", 10.0, False)
    same = competence_store.review_episode("insulator_inspection", {"insulator_dist_median": 5.4, "cable_dz_median": 1.2}, 30.0, "s1")
    assert same[0]["status"] == "confirmed"
    assert competence_store.load()[SLOT]["confidence"] == "confirmed"
    closer = competence_store.review_episode("insulator_inspection", {"insulator_dist_median": 2.9, "cable_dz_median": 0.5}, 0.0, "s2")
    assert closer[0]["status"] == "deviation"
    learned_from = competence_store.review_episode("insulator_inspection", {"insulator_dist_median": 9.0, "cable_dz_median": 0.0}, 5.0, "s1")
    assert learned_from == []  # the episode the rule came from is never checked against it


def test_hypothesis_needs_a_consistent_habit():
    eps = [{"signature": {"insulator_dist_median": v, "cable_dz_median": 1.0}} for v in (5.6, 5.2, 5.9)]
    h = competence_store.hypothesis(SLOT, eps)
    assert h and h["n"] == 3 and h["evidence"]["insulator_dist_median"] == 5.6
    assert "is that your rule" in h["example_question"]
    assert competence_store.hypothesis(SLOT, eps + [{"signature": {"insulator_dist_median": 2.5, "cable_dz_median": 1.0}}]) is None
    assert competence_store.hypothesis(SLOT, eps[:1]) is None


def test_confirmed_hypothesis_starts_confirmed():
    _reset()
    entry = competence_store.fill(SLOT, "Hold 5 to 6 m.", [], "", {"insulator_dist_median": 5.6}, "q", "yes", "s1", 1.0,
                                  False, observed_times=3)
    assert entry["confidence"] == "confirmed" and entry["confirmations"] == 2


def test_episode_store_counts_each_episode_once():
    _reset()
    episode_store.append("s1", "insulator_inspection", 1.0, 8.0, {"insulator_dist_median": 5.5})
    episode_store.append("s1", "insulator_inspection", 30.0, 9.0, {"insulator_dist_median": 5.7})
    assert len(episode_store.by_task("insulator_inspection")) == 2
    lines = episode_store.EPISODES_PATH.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["task"] == "insulator_inspection"

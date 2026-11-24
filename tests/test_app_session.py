"""Member D: decision loop (C -> B), decision log, map rendering and the Streamlit page."""
import importlib.util
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app import session as game
from app.map_view import build_map, build_probability_history, destroyed_towers
from src.game.engine import load_scenario, resolve_action
from src.modeling.predict import predict_blue_win

LOG_FIELDS = {  # docs/data_contract.md "Decision log"
    "session_id", "round", "scenario_version", "before_state", "action_id", "success_chance",
    "random_draw", "outcome", "after_state", "blue_win_before", "blue_win_after", "random_seed",
    "model_version", "feature_order", "simulator_version",
}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.log = Path(self.tmp.name) / "decisions.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_state_is_valid_and_predicted(self):
        g = game.new_session(seed=1)
        self.assertEqual(g["round"], 0)
        self.assertAlmostEqual(g["history"][0]["p_blue"], predict_blue_win(game.SAMPLE_STATE, game.MODEL_PATH))
        self.assertEqual(set(game.available_actions(g)), set(load_scenario()["actions"]))

    def test_action_resolves_predicts_and_logs(self):
        g = game.new_session(seed=1)
        before = g["state"]
        record = game.take_action(g, "contest_dragon", log_path=self.log)
        expected = resolve_action(before, "contest_dragon", 1)
        self.assertEqual(record["after_state"], expected["state"])
        self.assertEqual(record["outcome"], expected["outcome"])
        self.assertGreater(record["after_state"]["elapsed_seconds"], before["elapsed_seconds"])
        self.assertAlmostEqual(record["blue_win_after"], predict_blue_win(expected["state"], game.MODEL_PATH))
        self.assertTrue(0 <= record["blue_win_after"] <= 1)
        self.assertEqual(g["state"], record["after_state"])
        self.assertEqual([h["round"] for h in g["history"]], [0, 1])
        self.assertTrue(LOG_FIELDS <= set(record))
        logged = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(logged, [record])

    def test_both_outcomes_reachable_through_app(self):
        for action_id in load_scenario()["actions"]:
            outcomes = set()
            for seed in (1, 2):  # C documents seed 1 = success, seed 2 = failure
                g = game.new_session(seed=seed)
                outcomes.add(game.take_action(g, action_id, log_path=None)["outcome"])
            self.assertEqual(outcomes, {"success", "failure"}, action_id)

    def test_same_seed_reproduces(self):
        a = game.take_action(game.new_session(seed=7), "push_mid_tower", log_path=None)
        b = game.take_action(game.new_session(seed=7), "push_mid_tower", log_path=None)
        for key in ("outcome", "random_draw", "after_state", "blue_win_after"):
            self.assertEqual(a[key], b[key])

    def test_one_decision_per_event(self):
        g = game.new_session(seed=1)
        game.take_action(g, "contest_dragon", log_path=None)
        self.assertEqual(game.available_actions(g), {})
        with self.assertRaises(game.DecisionError):
            game.take_action(g, "contest_dragon", log_path=None)
        self.assertEqual(len(g["decisions"]), 1)

    def test_unknown_action_rejected(self):
        with self.assertRaises(game.DecisionError):
            game.take_action(game.new_session(seed=1), "no_such_action", log_path=None)


class MapTests(unittest.TestCase):
    def test_tower_counts_map_to_destroyed_towers(self):
        down = destroyed_towers(game.SAMPLE_STATE)
        self.assertEqual(down["red"], {"bot_outer"})  # blue has destroyed one red tower
        self.assertEqual(down["blue"], set())

    def test_map_updates_after_decision(self):
        g = game.new_session(seed=1)
        start = build_map(g["state"], pending_event=True)
        record = game.take_action(g, "push_mid_tower", log_path=None)
        self.assertEqual(record["outcome"], "success")
        after = build_map(g["state"], last_decision=record, pending_event=False)
        self.assertIn("mid_outer", destroyed_towers(g["state"])["red"])
        texts = [a.text for a in after.layout.annotations]
        self.assertTrue(any("Push mid tower" in (t or "") for t in texts))
        self.assertNotEqual(start.to_json(), after.to_json())
        hist = build_probability_history(g["history"])
        self.assertEqual(len(hist.data[0].y), 2)


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "streamlit not installed")
class StreamlitPageTests(unittest.TestCase):
    def test_click_action_updates_page(self):
        from streamlit.testing.v1 import AppTest

        with TemporaryDirectory() as tmp:
            os.environ["DECISION_LOG_PATH"] = str(Path(tmp) / "log.jsonl")
            try:
                at = AppTest.from_file(str(Path(game.ROOT) / "app" / "streamlit_app.py"),
                                       default_timeout=60).run()
                self.assertFalse(at.exception)
                at.button(key="act_contest_dragon").click().run()
                self.assertFalse(at.exception)
                self.assertTrue(at.success or at.error)  # outcome box rendered
                labels = [b.label for b in at.button]
                self.assertFalse(any(label.startswith("Contest dragon") for label in labels))
            finally:
                os.environ.pop("DECISION_LOG_PATH", None)


if __name__ == "__main__":
    unittest.main()

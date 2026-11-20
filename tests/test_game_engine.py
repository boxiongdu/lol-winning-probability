import copy
import unittest

from src.features.state import features
from src.game.engine import load_scenario, resolve_action
from src.modeling.predict import predict_blue_win


def sample_state():
    return {
        "elapsed_seconds": 720,
        "blue": {"total_gold": 22000, "kills": 9, "towers_destroyed": 1,
                 "dragons_secured": 1, "barons_secured": 0},
        "red": {"total_gold": 20000, "kills": 7, "towers_destroyed": 0,
                "dragons_secured": 0, "barons_secured": 0},
    }


class EngineTests(unittest.TestCase):
    def test_scenario_config_is_valid(self):
        scenario = load_scenario()
        self.assertGreaterEqual(len(scenario["actions"]), 2)
        for action in scenario["actions"].values():
            self.assertTrue(0 <= action["success_chance"] <= 1)
            for outcome in ("success", "failure"):
                self.assertGreater(action[outcome]["seconds"], 0)

    def test_every_action_reaches_both_outcomes(self):
        for action_id in load_scenario()["actions"]:
            outcomes = {resolve_action(sample_state(), action_id, seed)["outcome"]
                        for seed in (1, 2)}
            self.assertEqual(outcomes, {"success", "failure"}, action_id)

    def test_outcome_applies_configured_effects_and_time(self):
        scenario = load_scenario()
        for action_id, action in scenario["actions"].items():
            for seed in (1, 2):
                before = sample_state()
                result = resolve_action(before, action_id, seed)
                effects = action[result["outcome"]]
                after = result["state"]
                self.assertEqual(after["elapsed_seconds"],
                                 before["elapsed_seconds"] + effects["seconds"])
                for team in ("blue", "red"):
                    for stat, value in before[team].items():
                        self.assertEqual(after[team][stat],
                                         value + effects[team].get(stat, 0))

    def test_result_is_valid_model_input(self):
        for action_id in load_scenario()["actions"]:
            for seed in (1, 2):
                state = resolve_action(sample_state(), action_id, seed)["state"]
                features(state)
                self.assertTrue(0 <= predict_blue_win(state) <= 1)

    def test_same_seed_reproduces_result(self):
        first = resolve_action(sample_state(), "contest_dragon", 7)
        second = resolve_action(sample_state(), "contest_dragon", 7)
        self.assertEqual(first, second)

    def test_input_state_not_modified(self):
        state = sample_state()
        original = copy.deepcopy(state)
        resolve_action(state, "push_mid_tower", 1)
        self.assertEqual(state, original)

    def test_invalid_inputs_rejected(self):
        with self.assertRaises(ValueError):
            resolve_action(sample_state(), "no_such_action", 1)
        bad = sample_state()
        bad["red"]["kills"] = -1
        with self.assertRaises(ValueError):
            resolve_action(bad, "contest_dragon", 1)


if __name__ == "__main__":
    unittest.main()

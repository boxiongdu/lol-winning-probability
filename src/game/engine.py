"""Resolve one action in the dragon scenario."""

import json
import random
from copy import deepcopy
from pathlib import Path
from src.features.state import TEAM_FIELDS, features

SCENARIO_PATH = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "scenarios"
    / "dragon.json"
)
SIMULATOR_VERSION = 1


def load_scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))

def resolve_action(state, action_id, seed):
    """
    Apply one action to a copy of the state and return the result
    """
    features(state)
    scenario = load_scenario()
    if action_id not in scenario["actions"]:
        raise ValueError(f"Unknown action: {action_id}")
    action = scenario["actions"][action_id]
    success_chance = action["success_chance"]
    draw = random.Random(seed).random()
    outcome = "success" if draw < success_chance else "failure"
    changes = action[outcome]
    new_state = deepcopy(state)
    new_state["elapsed_seconds"] += changes["seconds"]
    for team in ("blue", "red"):
        for stat, amount in changes[team].items():
            if stat not in TEAM_FIELDS:
                raise ValueError(f"Unknown team stat: {stat}")
            new_state[team][stat] += amount
    features(new_state)
    return {
        "state": new_state,
        "action_id": action_id,
        "outcome": outcome,
        "success_chance": success_chance,
        "draw": draw,
        "seed": seed,
        "scenario_id": scenario["id"],
        "scenario_version": scenario["version"],
        "simulator_version": SIMULATOR_VERSION,
    }

"""UI-independent glue between C's simulator and B's model (Member D, Week 8).

The Streamlit page only renders what this module returns, so the whole decision
loop (resolve -> predict -> log) can be tested without a browser.

Week 8 scope: one event (C's dragon scenario), one decision per session. The
engine does not track objective availability yet, so a second click on the same
event would award the same dragon twice; take_action() refuses it.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.features.state import FEATURES, features
from src.game.engine import SCENARIO_PATH, load_scenario, resolve_action
from src.modeling.predict import predict_blue_win

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = Path(os.getenv("MODEL_PATH", ROOT / "models" / "week8.json"))
DEFAULT_LOG = "default"
APP_VERSION = "week8-d1"
DECISIONS_PER_SESSION = 1  # Week 9: three sequential rounds once C adds them.

# 12:00 state from C's dragon_demo.ipynb. When C adds "starting_state" to the
# scenario JSON, that value is used instead (see starting_state()).
SAMPLE_STATE = {
    "elapsed_seconds": 720,
    "blue": {"total_gold": 22000, "kills": 9, "towers_destroyed": 1,
             "dragons_secured": 1, "barons_secured": 0},
    "red": {"total_gold": 20000, "kills": 7, "towers_destroyed": 0,
            "dragons_secured": 0, "barons_secured": 0},
}


def decision_log_path() -> Path:
    """Read at call time so tests and containers can redirect the log."""
    return Path(os.getenv("DECISION_LOG_PATH", ROOT / "logs" / "decisions.jsonl"))


class DecisionError(RuntimeError):
    """The requested decision is not allowed in the current session."""


def _sha12(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def model_info(model_path: Path = MODEL_PATH) -> dict:
    """Identify the published model so every logged probability is traceable."""
    model = json.loads(Path(model_path).read_text())
    return {
        "path": str(model_path),
        "model_version": _sha12(model_path),
        "model_format_version": model["version"],
        "features": list(model["features"]),
        "metrics": model.get("metrics", {}),
    }


def starting_state(scenario: dict | None = None) -> dict:
    scenario = scenario or load_scenario()
    state = deepcopy(scenario.get("starting_state", SAMPLE_STATE))
    features(state)  # fail fast on an invalid fixture
    return state


def predict(state: dict, model_path: Path = MODEL_PATH) -> float:
    return predict_blue_win(state, model_path)


def new_session(seed: int, model_path: Path = MODEL_PATH, state: dict | None = None) -> dict:
    """Start a fresh session at the scenario's starting state."""
    scenario = load_scenario()
    state = deepcopy(state) if state is not None else starting_state(scenario)
    p_blue = predict(state, model_path)
    return {
        "session_id": str(uuid.uuid4()),
        "seed": int(seed),
        "model_path": str(model_path),
        "scenario": scenario,
        "state": state,
        "round": 0,
        "history": [{"round": 0, "label": "Start", "elapsed_seconds": state["elapsed_seconds"],
                     "p_blue": p_blue}],
        "decisions": [],
    }


def round_seed(base_seed: int, round_number: int) -> int:
    """Each round gets its own reproducible seed derived from the session seed."""
    return int(base_seed) + round_number - 1


def available_actions(session: dict) -> dict:
    if session["round"] >= DECISIONS_PER_SESSION:
        return {}
    return session["scenario"]["actions"]


def take_action(session: dict, action_id: str, log_path: Path | str | None = DEFAULT_LOG) -> dict:
    """Resolve one action, predict from the new state, log it and advance the session.

    Mutates ``session`` and returns the decision record (the data-contract log row).
    log_path=None skips logging; the default appends to decision_log_path().
    """
    if action_id not in available_actions(session):
        if session["round"] >= DECISIONS_PER_SESSION:
            raise DecisionError("This event is already resolved. Restart to try again.")
        raise DecisionError(f"Action not available: {action_id}")

    model_path = Path(session["model_path"])
    before = deepcopy(session["state"])
    p_before = session["history"][-1]["p_blue"]
    number = session["round"] + 1
    seed = round_seed(session["seed"], number)

    result = resolve_action(before, action_id, seed)
    after = result["state"]
    p_after = predict(after, model_path)  # model sees the state only after time advances
    info = model_info(model_path)
    action = session["scenario"]["actions"][action_id]

    record = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session["session_id"],
        "round": number,
        "scenario_id": result["scenario_id"],
        "scenario_version": result["scenario_version"],
        "scenario_sha": _sha12(SCENARIO_PATH),
        "action_id": action_id,
        "action_label": action.get("label", action_id),
        "success_chance": result["success_chance"],
        "random_draw": result["draw"],
        "outcome": result["outcome"],
        "seconds_spent": after["elapsed_seconds"] - before["elapsed_seconds"],
        "before_state": before,
        "after_state": after,
        "blue_win_before": p_before,
        "blue_win_after": p_after,
        "session_seed": session["seed"],
        "random_seed": result["seed"],
        "model_version": info["model_version"],
        "model_format_version": info["model_format_version"],
        "feature_order": list(FEATURES),
        "simulator_version": result["simulator_version"],
        "app_version": APP_VERSION,
    }

    session["state"] = after
    session["round"] = number
    session["decisions"].append(record)
    session["history"].append({"round": number, "label": record["action_label"],
                               "elapsed_seconds": after["elapsed_seconds"], "p_blue": p_after})
    if log_path is not None:
        append_log(record, decision_log_path() if log_path == DEFAULT_LOG else Path(log_path))
    return record


def append_log(record: dict, path: Path) -> None:
    """Append one JSON line. A database table replaces this once A/C agree its schema."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=False) + "\n")


def format_clock(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"

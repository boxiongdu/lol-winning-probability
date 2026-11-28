"""Inference needs only Python and the model JSON produced by training."""
import json
import math
from pathlib import Path

from src.features.state import FEATURES, features
from src.modeling.champion_predict import ChampionPredictor, predict_win_probability

DEFAULT_MODEL = Path('models/week8.json')


def predict_blue_win(state, model_path=DEFAULT_MODEL):
    model = json.loads(Path(model_path).read_text())
    if model['version'] != 1 or tuple(model['features']) != FEATURES:
        raise ValueError('Incompatible model or feature order')
    score = model['intercept'] + sum(
        weight * (value - center) / scale
        for value, weight, center, scale in zip(features(state), model['weights'],
            model['mean'], model['scale'], strict=True)
    )
    exp = math.exp(-abs(score))
    return 1 / (1 + exp) if score >= 0 else exp / (1 + exp)

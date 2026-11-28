"""Pure-Python inference; load an immutable artifact once per game session."""
import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from src.features.composition import CompositionEncoder, validate_composition
from src.features.champion_state import FEATURE_VERSION, elapsed_seconds, encode, feature_order

DEFAULT_BUNDLE = Path(__file__).resolve().parents[2] / 'models' / 'champion_v1' / 'bundle.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def model_identifier(model):
    return f"{model['stage']}-v1-{digest({k: v for k, v in model.items() if k != 'model_version'})[:20]}"


def probability(model, values):
    score = model['intercept'] + sum(w * (x - m) / s for x, w, m, s in
        zip(values, model['weights'], model['mean'], model['scale'], strict=True))
    if not math.isfinite(score):
        raise ValueError('Prediction score overflowed; state is outside the numeric domain')
    e = math.exp(-abs(score))
    return 1 / (1 + e) if score >= 0 else e / (1 + e)


class ChampionPredictor:
    def __init__(self, model_path=DEFAULT_BUNDLE, *, artifact=None):
        self._artifact = copy.deepcopy(artifact) if artifact is not None else json.loads(Path(model_path).read_text())
        a = self._artifact
        if a['schema_version'] != 1 or a['feature_version'] != FEATURE_VERSION:
            raise ValueError('Unsupported model bundle')
        self.encoder = CompositionEncoder.from_dict(a['encoder'])
        for stage in ('opening', 'in_game'):
            model = a['models'][stage]
            if (model['stage'] != stage or model['feature_version'] != FEATURE_VERSION
                    or model['dataset_version'] != a['dataset_version']
                    or model['feature_order'] != list(feature_order(self.encoder, stage))
                    or model['model_version'] != model_identifier(model)):
                raise ValueError('Model version/feature order mismatch')
            size = len(model['feature_order'])
            for key in ('weights', 'mean', 'scale'):
                if len(model[key]) != size or any(type(x) not in (int, float) or not math.isfinite(x) for x in model[key]):
                    raise ValueError('Invalid model parameters')
            if (any(s <= 0 for s in model['scale']) or type(model['intercept']) not in (int, float)
                    or not math.isfinite(model['intercept'])):
                raise ValueError('Invalid model preprocessing/intercept')
            start = 0 if stage == 'opening' else 6
            if any(x != 0 for x in model['mean'][start:]) or any(x != 1 for x in model['scale'][start:]):
                raise ValueError('Champion indicators must remain uncentered and unscaled')
        time_range = a['training_time_range_seconds']
        if (len(time_range) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in time_range)
                or not 0 < time_range[0] <= time_range[1]):
            raise ValueError('Invalid training time range')

    @property
    def versions(self):
        return {'features': FEATURE_VERSION, 'dataset': self._artifact['dataset_version'],
                **{f'{stage}_model': self._artifact['models'][stage]['model_version'] for stage in ('opening', 'in_game')}}

    def bind(self, blue_champion_ids, red_champion_ids):
        blue, red = validate_composition(blue_champion_ids, red_champion_ids)
        return BoundPredictor(self, blue, red)

    def predict_opening(self, blue_champion_ids, red_champion_ids):
        return self.bind(blue_champion_ids, red_champion_ids).predict({'elapsed_seconds': 0})

    def predict(self, state):
        return self.bind(state['blue_champion_ids'], state['red_champion_ids']).predict(state)

    def _predict(self, blue, red, state):
        seconds = elapsed_seconds(state)
        stage = 'opening' if seconds == 0 else 'in_game'
        values, warnings = encode(self.encoder, blue, red, state, stage)
        model = self._artifact['models'][stage]
        if stage == 'in_game':
            low, high = self._artifact['training_time_range_seconds']
            if not low <= seconds <= high:
                warnings.append(f'timestamp_outside_training_range:{low}:{high}')
        return dict(p_blue=probability(model, values), stage=stage,
                    model_version=model['model_version'], coverage_warnings=warnings)


@dataclass
class BoundPredictor:
    _predictor: ChampionPredictor
    _blue: tuple
    _red: tuple
    _last_seconds: float = -1

    @property
    def versions(self):
        return self._predictor.versions

    def predict(self, state):
        # State can omit picks after binding. If present, both sides must match.
        if 'blue_champion_ids' in state or 'red_champion_ids' in state:
            if not all(k in state for k in ('blue_champion_ids', 'red_champion_ids')):
                raise ValueError('Supply both teams or omit both after binding')
            if validate_composition(state['blue_champion_ids'], state['red_champion_ids']) != (self._blue, self._red):
                raise ValueError('Champion composition changed within a game')
        seconds = elapsed_seconds(state)
        if seconds < self._last_seconds:
            raise ValueError('Session timestamps cannot move backward')
        result = self._predictor._predict(self._blue, self._red, state)
        self._last_seconds = seconds
        return result


def predict_win_probability(state, model_path=DEFAULT_BUNDLE):
    """One-shot interface. Prefer bind() for successive states and version pinning."""
    return ChampionPredictor(model_path).predict(state)

"""The only feature construction entry point for champion-aware models."""
import math

from src.features.state import FEATURES, features

FEATURE_VERSION = 'champion-state-v1'


def elapsed_seconds(state):
    value = state.get('elapsed_seconds')
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('elapsed_seconds must be finite and nonnegative')
    return value


def feature_order(encoder, stage):
    if stage not in ('opening', 'in_game', 'statistics_only'):
        raise ValueError('Unknown model stage')
    if stage == 'opening':
        return encoder.feature_order
    return FEATURES + (() if stage == 'statistics_only' else encoder.feature_order)


def encode(encoder, blue, red, state=None, stage='opening'):
    composition, warnings = encoder.transform(blue, red)
    feature_order(encoder, stage)  # Validate stage even when state is absent.
    if stage == 'opening':
        return composition, warnings
    if state is None or elapsed_seconds(state) <= 0:
        raise ValueError('In-game features require a positive timestamp')
    statistics = features(state)
    return statistics + ([] if stage == 'statistics_only' else composition), warnings

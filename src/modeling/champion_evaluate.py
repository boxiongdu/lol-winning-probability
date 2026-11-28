"""Proper scoring rules and calibration with each match given equal mass."""
from collections import Counter
import numpy as np


def losses(y, p):
    y, p = np.asarray(y), np.asarray(p)
    if len(y) == 0 or y.shape != p.shape or not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError('Invalid predictions for evaluation')
    clipped = np.clip(p, 1e-15, 1 - 1e-15)
    return {'brier': (p - y) ** 2,
            'log_loss': -(y * np.log(clipped) + (1 - y) * np.log1p(-clipped))}


def match_weights(ids):
    counts = Counter(ids)
    return np.array([1 / counts[m] for m in ids])


def metrics(y, p, ids):
    y, p = np.asarray(y), np.asarray(p)
    w = match_weights(ids)
    values = losses(y, p)
    out = {'matches': len(set(ids)), 'observations': len(y),
           'match_weighted': {k: float(np.average(v, weights=w)) for k, v in values.items()},
           'frame_weighted': {k: float(v.mean()) for k, v in values.items()}}
    bins, ece = [], 0.0
    assignment = np.minimum((p * 10).astype(int), 9)
    for i in range(10):
        selected = assignment == i
        row = {'lower': i / 10, 'upper': (i + 1) / 10, 'count': int(selected.sum()),
               'match_mass': float(w[selected].sum()), 'mean_probability': None, 'blue_win_rate': None}
        if selected.any():
            row['mean_probability'] = float(np.average(p[selected], weights=w[selected]))
            row['blue_win_rate'] = float(np.average(y[selected], weights=w[selected]))
            ece += w[selected].sum() / w.sum() * abs(row['mean_probability'] - row['blue_win_rate'])
        bins.append(row)
    out['calibration'] = {'equal_width_bins': bins, 'ece_10_match_weighted': float(ece),
                          'mean_probability': float(np.average(p, weights=w)),
                          'blue_win_rate': float(np.average(y, weights=w))}
    return out


def paired_comparison(y, champion, baseline, ids, repetitions=2000):
    """Resample entire matches, never treat correlated frames as independent."""
    names, inverse = np.unique(ids, return_inverse=True)
    counts = np.bincount(inverse)
    a, b = losses(y, champion), losses(y, baseline)
    rng = np.random.default_rng(20261127)
    draws = rng.integers(0, len(names), size=(repetitions, len(names)))
    result = {}
    for key in a:
        delta = np.bincount(inverse, weights=a[key] - b[key]) / counts
        ci = np.quantile(delta[draws].mean(axis=1), [.025, .975]).tolist()
        result[key] = dict(champion_minus_baseline=float(delta.mean()), bootstrap_95_percent_ci=ci,
                           evidence_of_improvement=bool(ci[1] < 0))
    result['method'] = 'paired match bootstrap; 2000 replicates; seed 20261127; repeated players may still be dependent'
    return result

"""Run from the repo root with python -m src.modeling.train."""
import json
import os
from itertools import groupby
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import psycopg
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

from src.features.state import FEATURES, TEAM_FIELDS, features
from src.modeling.predict import DEFAULT_MODEL

SQL = Path(__file__).parents[1] / 'features' / 'snapshots.sql'


def training_rows(db_rows):
    for key, group in groupby(db_rows, key=lambda row: row[:4]):
        match, start, win, timestamp = key
        sides = list(group)
        if [row[4] for row in sides] != [100, 200]:
            raise ValueError(f'Expected both teams at {match}/{timestamp}')
        state = {'elapsed_seconds': timestamp / 1000}
        for name, row in zip(('blue', 'red'), sides):
            state[name] = dict(zip(TEAM_FIELDS, row[5:], strict=True))
        yield match, start, int(win), features(state)


def fit(rows):
    rows = list(rows)
    matches = {}
    for match, start, win, _ in rows:
        if matches.setdefault(match, (start, win)) != (start, win):
            raise ValueError(f'Inconsistent start or label for {match}')
    ordered = sorted(matches, key=lambda match: (matches[match][0], match))
    n = len(ordered)
    if n < 4:
        raise ValueError('Need at least four eligible matches')
    first = min(n - 2, max(2, int(.7 * n)))
    second = min(n - 1, max(first + 1, int(.85 * n)))
    splits = dict(train=ordered[:first], validation=ordered[first:second], test=ordered[second:])
    def arrays(ids):
        ids = set(ids)
        selected = [(x, win) for match, _, win, x in rows if match in ids]
        x, y = zip(*selected)
        return np.array(x), np.array(y)

    x, y = arrays(splits['train'])
    if len(set(y)) < 2:
        raise ValueError('Chronological training matches must include wins for both sides')
    scaler = StandardScaler().fit(x)
    clf = LogisticRegression(max_iter=1000).fit(scaler.transform(x), y)
    prior = float(np.mean([matches[m][1] for m in splits['train']]))
    metrics = {}
    for name in ('validation', 'test'):
        x, y = arrays(splits[name])
        metrics[name] = {'frames': len(y)}
        for label, p in [('logistic', clf.predict_proba(scaler.transform(x))[:, 1]),
                         ('baseline', np.full(len(y), prior))]:
            metrics[name][label] = dict(brier=float(brier_score_loss(y, p)),
                log_loss=float(log_loss(y, p, labels=[0, 1])))
    return dict(version=1, features=FEATURES, weights=clf.coef_[0].tolist(),
                intercept=float(clf.intercept_[0]), mean=scaler.mean_.tolist(),
                scale=scaler.scale_.tolist(), baseline=prior, splits=splits, metrics=metrics)


def train_model(database_url=None, model_path=DEFAULT_MODEL):
    """Airflow entry point. Publish only after training and evaluation succeed."""
    with psycopg.connect(database_url or os.environ['DATABASE_URL']) as conn:
        artifact = fit(training_rows(conn.execute(SQL.read_text())))
    path = Path(model_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as tmp:
        json.dump(artifact, tmp, indent=2, allow_nan=False)
    Path(tmp.name).replace(path)
    return {'model_path': str(path), 'matches': {k: len(v) for k, v in artifact['splits'].items()},
            'metrics': artifact['metrics']}


if __name__ == '__main__':
    print(json.dumps(train_model(), indent=2))

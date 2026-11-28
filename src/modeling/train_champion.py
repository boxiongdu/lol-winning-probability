"""Reproducible opening/in-game training; validation selects, test evaluates once."""
import argparse
import hashlib
import json
import platform
import subprocess
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import scipy
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from src.features.composition import CompositionEncoder
from src.features.champion_dataset import load_dataset, validate_records
from src.features.champion_state import FEATURE_VERSION, encode, feature_order
from src.modeling.champion_predict import ChampionPredictor, digest, model_identifier, probability
from src.modeling.champion_evaluate import metrics, paired_comparison

C_GRID = (0.0001, 0.001, 0.01, 0.1, 1.0, 10.0)


def chronological_split(records):
    ordered = sorted(records, key=lambda m: (m['game_start_ms'], m['match_id']))
    n = len(ordered)
    if n < 20:
        raise ValueError('Need at least 20 eligible matches for three chronological splits')
    # Keep identical start timestamps in one split, not arbitrarily across a boundary.
    cuts = [i for i in range(1, n) if ordered[i-1]['game_start_ms'] < ordered[i]['game_start_ms']]
    if len(cuts) < 2:
        raise ValueError('Need at least three distinct start-time groups')
    first = min(cuts[:-1], key=lambda i: (abs(i - .7*n), i))
    second = min((i for i in cuts if i > first), key=lambda i: (abs(i - .85*n), i))
    return {name: [m['match_id'] for m in values] for name, values in
            [('train', ordered[:first]), ('validation', ordered[first:second]), ('test', ordered[second:])]}


def arrays(records, encoder, stage):
    rows, labels, ids, times = [], [], [], []
    for m in records:
        states = [None] if stage == 'opening' else m['states']
        for state in states:
            x, _ = encode(encoder, m['blue_champion_ids'], m['red_champion_ids'], state, stage)
            rows.append(x)
            labels.append(m['blue_win'])
            ids.append(m['match_id'])
            times.append(0 if state is None else state['elapsed_seconds'])
    return np.asarray(rows, dtype=float), np.asarray(labels), ids, np.asarray(times)


def fit_model(x, y, encoder, stage, c, dataset_version, legacy=False):
    if len(set(y)) != 2:
        raise ValueError('Training set must contain both outcomes')
    mean, scale = np.zeros(x.shape[1]), np.ones(x.shape[1])
    if stage != 'opening':
        scaler = StandardScaler().fit(x[:, :6])
        mean[:6], scale[:6] = scaler.mean_, scaler.scale_
    # Champion columns remain exactly -1/0/+1, including zero for absence/unseen.
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        max_iter, tolerance = (1000, 1e-4) if legacy else (2000, 1e-8)
        clf = LogisticRegression(C=c, solver='lbfgs', max_iter=max_iter, tol=tolerance, random_state=0)
        clf.fit((x - mean) / scale, y)
    model = dict(stage=stage, feature_order=list(feature_order(encoder, stage)),
                 weights=clf.coef_[0].tolist(), intercept=float(clf.intercept_[0]),
                 mean=mean.tolist(), scale=scale.tolist(), C=c,
                 feature_version=FEATURE_VERSION, dataset_version=dataset_version,
                 estimator=f'sklearn LogisticRegression; l2; lbfgs; max_iter={max_iter}; tol={tolerance}',
                 calibration='native_logistic; no post-hoc calibration fitted')
    model['model_version'] = model_identifier(model)
    return model


def predict_array(model, x):
    # Exercise the actual production scorer, including its saved preprocessing.
    return np.array([probability(model, row) for row in x])


def train(data):
    records = sorted(data['matches'], key=lambda m: (m['game_start_ms'], m['match_id']))
    validate_records(records)
    splits = chronological_split(records)
    by_id = {m['match_id']: m for m in records}
    train_records = [by_id[i] for i in splits['train']]
    validation_records = [by_id[i] for i in splits['validation']]
    encoder = CompositionEncoder.fit((m['blue_champion_ids'], m['red_champion_ids']) for m in train_records)
    dataset_version = f'dataset-v1-{digest(data)}'
    prior = float(np.mean([m['blue_win'] for m in train_records]))
    selected, candidates, legacy = {}, {}, None
    with threadpool_limits(limits=1):
        # Test features/predictions are not constructed during selection.
        for stage in ('opening', 'in_game', 'statistics_only'):
            x, y, _, _ = arrays(train_records, encoder, stage)
            vx, vy, vids, _ = arrays(validation_records, encoder, stage)
            candidates[stage] = []
            best_score = float('inf')
            for c in C_GRID:
                model = fit_model(x, y, encoder, stage, c, dataset_version)
                scores = metrics(vy, predict_array(model, vx), vids)
                candidates[stage].append({'C': c, 'validation': scores})
                score = scores['match_weighted']['log_loss']
                if score < best_score:
                    best_score, selected[stage] = score, model
            if stage == 'statistics_only':
                legacy = fit_model(x, y, encoder, stage, 1.0, dataset_version, legacy=True)
    selected['statistics_only_legacy_C1'] = legacy
    selection = dict(objective='validation match-weighted log loss; smaller C breaks exact ties',
                     C_grid=list(C_GRID), train_weighting='one vote per snapshot (legacy statistics-model training convention)',
                     selected_C={k: v['C'] for k, v in selected.items()}, candidates=candidates,
                     refit_on_validation=False, posthoc_calibration=False)
    evaluation, comparisons, predictions = {}, {}, []
    # Selection is complete. Both held-out sets use precisely the same frozen models.
    for split_name in ('validation', 'test'):
        current = [by_id[i] for i in splits[split_name]]
        evaluation[split_name], comparisons[split_name] = {}, {}
        for stage in ('opening', 'in_game'):
            x, y, ids, times = arrays(current, encoder, stage)
            p = predict_array(selected[stage], x)
            baseline = np.full(len(y), prior)
            stage_metrics = {'champion': metrics(y, p, ids), 'win_rate_baseline': metrics(y, baseline, ids)}
            probabilities = {'champion': p, 'win_rate_baseline': baseline}
            if stage == 'in_game':
                sx, _, sids, _ = arrays(current, encoder, 'statistics_only')
                if sids != ids:
                    raise AssertionError('Comparators do not share rows')
                for key in ('statistics_only', 'statistics_only_legacy_C1'):
                    probabilities[key] = predict_array(selected[key], sx)
                    stage_metrics[key] = metrics(y, probabilities[key], ids)
                comparisons[split_name][stage] = {
                    key: paired_comparison(y, p, probabilities[key], ids)
                    for key in ('statistics_only', 'statistics_only_legacy_C1')}
                stage_metrics['time_bands'] = {}
                for lo, hi in ((0, 600), (600, 1200), (1200, 1800), (1800, 10**9)):
                    mask = (times >= lo) & (times < hi)
                    if mask.any():
                        band_ids = [m for m, included in zip(ids, mask) if included]
                        stage_metrics['time_bands'][f'{lo}-{hi}'] = {
                            k: metrics(y[mask], v[mask], band_ids) for k, v in probabilities.items()}
            else:
                comparisons[split_name][stage] = paired_comparison(y, p, baseline, ids)
            evaluation[split_name][stage] = stage_metrics
            for i, mid in enumerate(ids):
                predictions.append(dict(split=split_name, stage=stage, match_id=mid,
                                        elapsed_seconds=float(times[i]), blue_win=int(y[i]),
                                        **{k: float(v[i]) for k, v in probabilities.items()}))
    coverage = {}
    for split_name, ids in splits.items():
        picked = [by_id[i] for i in ids]
        unseen, sparse, affected = Counter(), Counter(), 0
        for m in picked:
            _, warnings_ = encoder.transform(m['blue_champion_ids'], m['red_champion_ids'])
            affected += bool(warnings_)
            for warning in warnings_:
                (unseen if warning.startswith('unseen') else sparse)[warning.split(':')[2]] += 1
        coverage[split_name] = dict(matches=len(ids), matches_with_warnings=affected,
            unseen_champion_appearances=dict(unseen), sparse_champion_appearances=dict(sparse),
            patch_counts=dict(Counter(m['patch'] for m in picked)),
            first_start_ms=picked[0]['game_start_ms'], last_start_ms=picked[-1]['game_start_ms'])
    all_train_times = [s['elapsed_seconds'] for m in train_records for s in m['states']]
    bundle = dict(schema_version=1, feature_version=FEATURE_VERSION, dataset_version=dataset_version,
                  encoder=encoder.to_dict(), models=selected, training_match_win_rate=prior,
                  training_time_range_seconds=[min(all_train_times), max(all_train_times)],
                  handover='opening at elapsed_seconds == 0; in_game at >0; no continuity guarantee')
    # Loadability/serving agreement is part of publication, not just a unit fixture.
    predictor = ChampionPredictor(artifact=bundle)
    replay_max_error = 0.0
    for row in predictions:
        m = by_id[row['match_id']]
        state = {'elapsed_seconds': 0} if row['stage'] == 'opening' else next(s for s in m['states'] if s['elapsed_seconds'] == row['elapsed_seconds'])
        actual = predictor.bind(m['blue_champion_ids'], m['red_champion_ids']).predict(state)['p_blue']
        replay_max_error = max(replay_max_error, abs(actual - row['champion']))
    if replay_max_error > 1e-12:
        raise AssertionError('Training/serving discrepancy')
    report = dict(schema_version=1, dataset_version=dataset_version, dataset_metadata=data['metadata'],
                  split_counts={k: {'matches': len(v), 'positive_snapshots': sum(len(by_id[i]['states']) for i in v)} for k, v in splits.items()},
                  selection=selection, metrics=evaluation, comparisons=comparisons, coverage=coverage,
                  replay_max_absolute_error=replay_max_error,
                  environment=dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__, sklearn=sklearn.__version__),
                  limitations=['Observational associations, not causal champion/action effects.',
                    'Limited match counts cannot establish reliable effects for every champion, role, matchup or synergy.',
                    'Current Challenger-roster sampling is not representative of all ranks; repeated players may cross match splits.',
                    'Simulated states, early sub-minute states and future patches may be outside the training distribution.',
                    'Calibration is evaluated, not guaranteed. No smoothing or manual bonuses.'])
    manifest = dict(policy='chronological by game_start_ms then match_id; keep tied starts together; approximately 70/15/15',
                    dataset_version=dataset_version, splits=splits,
                    matches=[{k: m[k] for k in ('match_id', 'game_start_ms', 'patch')} for m in records])
    return bundle, report, manifest, predictions


def publish(data, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new artifact directory; published versions are immutable')
    bundle, report, manifest, predictions = train(data)
    output.mkdir(parents=True)
    for name, value in [('bundle.json', bundle), ('metrics.json', report), ('split_manifest.json', manifest),
                        ('encoder.json', bundle['encoder']), ('model_selection.json', report['selection'])]:
        (output / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    with (output / 'heldout_predictions.jsonl').open('w') as handle:
        for row in predictions:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
    try:
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = 'unavailable'
    source_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for base in ('src/features', 'src/modeling') for p in sorted(Path(base).glob('*')) if p.is_file()}
    (output / 'provenance.json').write_text(json.dumps(dict(base_git_revision=revision, source_sha256=source_hashes,
        dataset_version=bundle['dataset_version'], training_command='python -m src.modeling.train_champion --dataset DATASET --output NEW_DIRECTORY',
        environment=report['environment']), indent=2, sort_keys=True) + '\n')
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
    (output / 'checksums.json').write_text(json.dumps(checksums, indent=2, sort_keys=True) + '\n')
    return {'output': str(output), 'versions': ChampionPredictor(output / 'bundle.json').versions,
            'split_counts': report['split_counts'], 'test_comparisons': report['comparisons']['test']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(load_dataset(args.dataset), args.output), indent=2))

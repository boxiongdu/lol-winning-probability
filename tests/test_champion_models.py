import copy
import json
import pytest

from src.features.composition import CompositionEncoder, validate_composition
from src.features.champion_dataset import build_states, validate_records
from src.features.champion_state import encode
from src.features.dump_dataset import extract_dump, unescape
from src.features.state import TEAM_FIELDS
from src.modeling.champion_evaluate import metrics
from src.modeling.champion_predict import ChampionPredictor, model_identifier, probability
from src.modeling.train_champion import arrays, chronological_split, publish, train


def state(seconds=60):
    return {'elapsed_seconds': seconds,
            'blue': dict(zip(TEAM_FIELDS, [5000, 1, 0, 0, 0])),
            'red': dict(zip(TEAM_FIELDS, [4900, 0, 0, 0, 0]))}


def dataset(n=40):
    matches = []
    for i in range(n):
        blue, red = list(range(1, 6)), list(range(6, 11))
        if i % 3 == 0:
            blue, red = red, blue
        snapshots = [state(t) for t in (60, 120)]
        for s in snapshots:
            s['blue']['total_gold'] += i * 10
        matches.append(dict(match_id=f'TEST_{i:03}', game_start_ms=100000+i*1000,
            duration_seconds=600, patch='synthetic-test-only', blue_win=i % 2,
            blue_champion_ids=blue, red_champion_ids=red, states=snapshots))
    return {'metadata': {'schema': 'champion-dataset-v1', 'synthetic_test_only': True}, 'matches': matches}


@pytest.fixture(scope='module')
def fitted():
    return train(dataset())


def test_signed_order_and_training_counts_are_match_based():
    encoder = CompositionEncoder.fit([(list(range(1,6)), list(range(6,11)))])
    assert encoder.vocabulary == tuple(range(1,11))
    assert encoder.counts == (1,)*10
    x, _ = encoder.transform([5,4,3,2,1], [10,9,8,7,6])
    assert x == [1]*5 + [-1]*5
    swapped, _ = encoder.transform([10,9,8,7,6], [5,4,3,2,1])
    assert swapped == [-v for v in x]
    assert CompositionEncoder.from_dict(encoder.to_dict()) == encoder


def test_unseen_has_no_feature_or_learned_effect(fitted):
    encoder = CompositionEncoder.fit([(list(range(1,6)), list(range(6,11)))])
    a, warnings = encoder.transform([1,2,3,4,100], [6,7,8,9,10])
    b, _ = encoder.transform([1,2,3,4,200], [6,7,8,9,10])
    assert a == b and a[4] == 0
    assert 'unseen_champion:blue:100:no_learned_effect' in warnings
    predictor = ChampionPredictor(artifact=fitted[0])
    assert predictor.predict_opening([1,2,3,4,100], [6,7,8,9,10])['p_blue'] == predictor.predict_opening([1,2,3,4,200], [6,7,8,9,10])['p_blue']
    zero, warnings = encoder.transform([101,102,103,104,105], [106,107,108,109,110])
    assert zero == [0]*10 and len(warnings) == 10
    assert any(w.startswith('unseen_champion:red:') for w in warnings)


@pytest.mark.parametrize('blue,red', [([1,2,3,4],[6,7,8,9,10]), ([1,2,3,4,4],[6,7,8,9,10]),
    ([1,2,3,4,5],[5,7,8,9,10]), ([True,2,3,4,5],[6,7,8,9,10]),
    ([0,2,3,4,5],[6,7,8,9,10]), (['1',2,3,4,5],[6,7,8,9,10])])
def test_invalid_picks(blue, red):
    with pytest.raises(ValueError):
        validate_composition(blue, red)


def test_opening_ignores_all_future_statistics_and_stage_handover(fitted):
    p = ChampionPredictor(artifact=fitted[0])
    session = p.bind([1,2,3,4,5], [6,7,8,9,10])
    opening = session.predict({'elapsed_seconds': 0})
    assert opening == session.predict({'elapsed_seconds': 0, 'blue_win': 1, 'blue': {'total_gold': 99999999}})
    after = session.predict(state(.001))
    assert opening['stage'] == 'opening' and after['stage'] == 'in_game'
    assert opening['model_version'] != after['model_version']
    assert after['coverage_warnings'][-1].startswith('timestamp_outside_training_range:')
    with pytest.raises(ValueError, match='backward'):
        session.predict({'elapsed_seconds': 0})


def test_fixed_composition_and_pinned_artifact(fitted):
    a = copy.deepcopy(fitted[0])
    p = ChampionPredictor(artifact=a)
    blue, red = [1,2,3,4,5], [6,7,8,9,10]
    session = p.bind(blue, red)
    blue[0] = 100
    a['models']['opening']['intercept'] = 100
    first = session.predict({'elapsed_seconds': 0})
    assert first == p.predict_opening([1,2,3,4,5], red)
    with pytest.raises(ValueError, match='composition changed'):
        session.predict(dict(state(), blue_champion_ids=blue, red_champion_ids=red))
    session.predict(dict(state(), blue_champion_ids=[5,4,3,2,1], red_champion_ids=red))


@pytest.mark.parametrize('seconds', [-1, float('nan'), float('inf'), True, '60'])
def test_bad_clock(fitted, seconds):
    with pytest.raises(ValueError):
        ChampionPredictor(artifact=fitted[0]).bind([1,2,3,4,5],[6,7,8,9,10]).predict({'elapsed_seconds': seconds})


def test_features_and_saved_preprocessing_identical(fitted):
    bundle, report, _, predictions = fitted
    assert report['replay_max_absolute_error'] == 0
    encoder = CompositionEncoder.from_dict(bundle['encoder'])
    m = dataset()['matches'][0]
    x, _, _, _ = arrays([m], encoder, 'in_game')
    raw, _ = encode(encoder, m['blue_champion_ids'], m['red_champion_ids'], m['states'][0], 'in_game')
    assert x[0].tolist() == raw
    model = bundle['models']['in_game']
    assert model['mean'][6:] == [0] * 10 and model['scale'][6:] == [1] * 10
    p = ChampionPredictor(artifact=bundle).bind(m['blue_champion_ids'],m['red_champion_ids']).predict(m['states'][0])
    assert p['p_blue'] == probability(model, raw)
    assert set(p) == {'p_blue','stage','model_version','coverage_warnings'}


def test_splits_are_whole_chronological_and_keep_ties(fitted):
    records = dataset()['matches']
    records[28]['game_start_ms'] = records[27]['game_start_ms']
    splits = chronological_split(list(reversed(records)))
    lookup = {m['match_id']: m['game_start_ms'] for m in records}
    ids = [set(splits[k]) for k in ('train','validation','test')]
    assert len(set.union(*ids)) == len(records)
    assert all(not ids[i] & ids[j] for i,j in ((0,1),(0,2),(1,2)))
    assert max(lookup[i] for i in ids[0]) < min(lookup[i] for i in ids[1])
    assert max(lookup[i] for i in ids[1]) < min(lookup[i] for i in ids[2])


def test_test_data_cannot_change_fit_preprocessing_or_selection(fitted):
    data = dataset()
    for m in data['matches'][34:]:
        m['blue_win'] = 1 - m['blue_win']
        m['blue_champion_ids'][0] = 999
        m['states'][0]['blue']['total_gold'] = 1000000
    modified, report, _, _ = train(data)
    assert 999 not in modified['encoder']['vocabulary']
    assert modified['encoder'] == fitted[0]['encoder']
    assert report['selection'] == fitted[1]['selection']
    for stage in fitted[0]['models']:
        for key in ('C','mean','scale','weights','intercept'):
            assert modified['models'][stage][key] == fitted[0]['models'][stage][key]


def test_future_events_and_terminal_frames_excluded():
    m = {'match_id': 'M', 'duration_seconds': 300}
    frames = [(t,team,3000,5) for t in (0,60000,120000,180000,240000) for team in (100,200)]
    events = [(60000,100,'CHAMPION_KILL',None), (60001,200,'CHAMPION_KILL',None),
              (60000,100,'BUILDING_KILL','INHIBITOR_BUILDING'),
              (120000,200,'ELITE_MONSTER_KILL','DRAGON'), (180000,None,'GAME_END',None)]
    states = build_states(m, frames, events)
    assert [s['elapsed_seconds'] for s in states] == [60,120]
    assert states[0]['blue']['kills'] == 1 and states[0]['red']['kills'] == 0
    assert states[1]['red']['kills'] == 1 and states[1]['red']['dragons_secured'] == 1
    assert states[0]['blue']['towers_destroyed'] == 0
    with pytest.raises(ValueError):
        build_states(m, [(60000,100,3000,4)], [])


def test_duplicate_matches_and_terminal_snapshots_fail():
    data = dataset()['matches']
    with pytest.raises(ValueError):
        validate_records(data + data[:1])
    data[0]['states'][0]['elapsed_seconds'] = 600
    with pytest.raises(ValueError):
        validate_records(data)


def test_calibration_and_match_weighting():
    result = metrics([0,0,1], [.5,.5,.9], ['A','A','B'])
    assert result['match_weighted']['brier'] == pytest.approx((.25 + .01)/2)
    assert result['frame_weighted']['brier'] == pytest.approx((.25+.25+.01)/3)
    assert sum(r['count'] for r in result['calibration']['equal_width_bins']) == 3
    assert result['calibration']['blue_win_rate'] == .5


def test_artifact_rejects_tampered_order_or_weights(fitted):
    for mutate in ('order','weights'):
        bad = copy.deepcopy(fitted[0])
        m = bad['models']['opening']
        if mutate == 'order':
            m['feature_order'].reverse()
        else:
            m['weights'][0] += 1
        with pytest.raises(ValueError):
            ChampionPredictor(artifact=bad)


def test_copy_escaping():
    assert unescape(r'\N') is None
    assert unescape(r'a\tb\n') == 'a\tb\n'
    assert unescape(r'a\\b') == 'a\\b'


def test_dump_export_enforces_sample_membership(monkeypatch, tmp_path):
    sample = '4f67be78-4f12-4dc5-ae52-9a3760fcbf8b'
    tables = {
        'sampling_runs': [dict(sample_id=sample, selection_sha256='a'*64, status='complete')],
        'sampling_matches': [dict(sample_id=sample, match_id='M'), dict(sample_id='other', match_id='OUTSIDE')],
        'matches': [dict(match_id=mid, patch='p', game_start_ms='1000', duration_seconds='300',
                         queue_id='420', map_id='11', blue_win='t', early_surrender='f') for mid in ('M','OUTSIDE')],
        'participants': [dict(match_id='M', participant_id=str(i), team_id=str(100 if i<=5 else 200), champion_id=str(i)) for i in range(1,11)],
        'participant_frames': [dict(match_id='M', timestamp_ms=str(t), participant_id=str(i), total_gold='500') for t in (0,60000,120000) for i in range(1,11)],
        'events': [],
    }
    monkeypatch.setattr('src.features.dump_dataset.table_rows', lambda path, table, binary: iter(tables[table]))
    path = tmp_path/'fixture.dump'
    path.write_bytes(b'synthetic-fixture')
    result = extract_dump(path, sample)
    assert [m['match_id'] for m in result['matches']] == ['M']
    assert result['matches'][0]['states'][0]['blue']['total_gold'] == 2500
    assert result['metadata']['selected_matches'] == 1
    assert result['metadata']['missing_normalized_ids'] == []


def test_publication_roundtrip_and_no_overwrite(tmp_path):
    output = tmp_path / 'version'
    publish(dataset(), output)
    p = ChampionPredictor(output / 'bundle.json')
    assert 0 < p.predict_opening([1,2,3,4,5],[6,7,8,9,10])['p_blue'] < 1
    assert (output / 'checksums.json').exists()
    with pytest.raises(ValueError, match='immutable'):
        publish(dataset(), output)

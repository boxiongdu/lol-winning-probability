"""Read A's normalized tables without migrations or future feature statistics."""
import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

from src.features.composition import validate_composition
from src.features.state import TEAM_FIELDS, features
from src.modeling.champion_predict import digest

DATASET_SCHEMA = 'champion-dataset-v1'


def build_states(match, frames, events):
    """frames: (ms, team, total_gold, player_count); events: (ms, team, kind, subtype).

    Include events at the timestamp, exclude future events, final frame, duration
    endpoint and GAME_END. No interpolation from later frames.
    """
    snapshots = defaultdict(dict)
    for ms, team, gold, count in frames:
        if team not in (100, 200) or count != 5 or team in snapshots[ms]:
            raise ValueError(f'Incomplete or duplicate team frame: {match["match_id"]}/{ms}')
        snapshots[ms][team] = int(gold)
    if not snapshots:
        return []
    end = min(match['duration_seconds'] * 1000, max(snapshots),
              min((e[0] for e in events if e[2] == 'GAME_END'), default=float('inf')))
    counters = {team: dict.fromkeys(TEAM_FIELDS, 0) for team in (100, 200)}
    ordered = sorted(events, key=lambda e: e[0])
    index, states = 0, []
    for ms, golds in sorted(snapshots.items()):
        if ms <= 0 or ms >= end:
            continue
        if set(golds) != {100, 200}:
            raise ValueError('Missing team in historical frame')
        while index < len(ordered) and ordered[index][0] <= ms:
            _, team, kind, subtype = ordered[index]
            field = ('kills' if kind == 'CHAMPION_KILL' else
                     'towers_destroyed' if kind == 'BUILDING_KILL' and subtype == 'TOWER_BUILDING' else
                     'dragons_secured' if kind == 'ELITE_MONSTER_KILL' and subtype == 'DRAGON' else
                     'barons_secured' if kind == 'ELITE_MONSTER_KILL' and subtype == 'BARON_NASHOR' else None)
            if field and team in counters:
                counters[team][field] += 1
            index += 1
        state = {'elapsed_seconds': ms / 1000}
        for name, team in (('blue', 100), ('red', 200)):
            state[name] = {**counters[team], 'total_gold': golds[team]}
        features(state)
        states.append(state)
    return states


def validate_records(records):
    seen = set()
    for m in records:
        if not isinstance(m['match_id'], str) or not m['match_id'] or m['match_id'] in seen or m['match_id'].startswith('DEMO_'):
            raise ValueError('Duplicate, invalid, or synthetic historical match ID')
        seen.add(m['match_id'])
        if type(m['game_start_ms']) is not int or m['game_start_ms'] <= 0:
            raise ValueError('Invalid match start')
        if type(m['blue_win']) is not int or m['blue_win'] not in (0, 1):
            raise ValueError('Invalid binary label')
        if type(m['duration_seconds']) is not int or m['duration_seconds'] <= 0:
            raise ValueError('Invalid match duration')
        validate_composition(m['blue_champion_ids'], m['red_champion_ids'])
        if not m['states']:
            raise ValueError('Every modeling match needs a positive nonterminal snapshot')
        previous = 0
        for s in m['states']:
            features(s)
            if not previous < s['elapsed_seconds'] < m['duration_seconds']:
                raise ValueError('Unordered, duplicate, zero, or terminal snapshot')
            previous = s['elapsed_seconds']
            if 'blue_champion_ids' in s or 'red_champion_ids' in s:
                if validate_composition(s['blue_champion_ids'], s['red_champion_ids']) != validate_composition(m['blue_champion_ids'], m['red_champion_ids']):
                    raise ValueError('Historical composition changed')


def extract(database_url, sample_id):
    import psycopg
    from psycopg.rows import dict_row
    from uuid import UUID
    sample_id = str(UUID(sample_id))
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        run = conn.execute('SELECT sample_id, selection_sha256, status FROM sampling_runs WHERE sample_id=%s', (sample_id,)).fetchone()
        if run is None:
            raise ValueError('sample_id not found in A sampling_runs')
        selected = conn.execute('SELECT match_id FROM sampling_matches WHERE sample_id=%s ORDER BY match_id', (sample_id,)).fetchall()
        rows = conn.execute('''SELECT m.* FROM matches m JOIN sampling_matches sm USING(match_id)
            WHERE sm.sample_id=%s ORDER BY m.game_start_ms,m.match_id''', (sample_id,)).fetchall()
        ids = [r['match_id'] for r in rows]
        if not ids:
            raise ValueError('No normalized matches in requested sample')
        participants = defaultdict(lambda: {100: [], 200: []})
        for r in conn.execute('SELECT match_id,team_id,champion_id FROM participants WHERE match_id=ANY(%s) ORDER BY match_id,participant_id', (ids,)):
            participants[r['match_id']][r['team_id']].append(r['champion_id'])
        frames, events = defaultdict(list), defaultdict(list)
        with conn.cursor(name='champion_frames') as cur:
            cur.execute('''SELECT f.match_id, f.timestamp_ms, p.team_id, sum(f.total_gold) AS gold,
                count(*) AS players FROM participant_frames f JOIN participants p USING(match_id,participant_id)
                WHERE f.match_id=ANY(%s) GROUP BY f.match_id,f.timestamp_ms,p.team_id
                ORDER BY f.match_id,f.timestamp_ms,p.team_id''', (ids,))
            for r in cur:
                frames[r['match_id']].append((r['timestamp_ms'], r['team_id'], r['gold'], r['players']))
        with conn.cursor(name='champion_events') as cur:
            cur.execute('''SELECT match_id,timestamp_ms,credited_team_id,event_type,
                CASE WHEN event_type='BUILDING_KILL' THEN payload->>'buildingType'
                     ELSE payload->>'monsterType' END AS subtype FROM events
                WHERE match_id=ANY(%s) AND event_type IN ('CHAMPION_KILL','BUILDING_KILL','ELITE_MONSTER_KILL','GAME_END')
                ORDER BY match_id,timestamp_ms,frame_index,event_index''', (ids,))
            for r in cur:
                events[r['match_id']].append((r['timestamp_ms'], r['credited_team_id'], r['event_type'], r['subtype']))
    return assemble_dataset(rows, participants, frames, events, selected, run, sample_id)


def assemble_dataset(rows, participants, frames, events, selected, run, sample_id):
    ids = [r['match_id'] for r in rows]
    records, exclusions = [], []
    for row in rows:
        mid = row['match_id']
        if mid.startswith('DEMO_') or row['queue_id'] != 420 or row['map_id'] != 11 or row['early_surrender'] or row['duration_seconds'] < 300:
            exclusions.append({'match_id': mid, 'reason': 'outside_ranked_solo_nonremake_scope'})
            continue
        try:
            blue, red = validate_composition(participants[mid][100], participants[mid][200])
            m = {k: row[k] for k in ('match_id', 'game_start_ms', 'patch', 'duration_seconds')}
            m.update(blue_win=int(row['blue_win']), blue_champion_ids=list(blue), red_champion_ids=list(red))
            m['states'] = build_states(m, frames[mid], events[mid])
            validate_records([m])
            records.append(m)
        except ValueError as exc:
            exclusions.append({'match_id': mid, 'reason': str(exc)})
    validate_records(records)
    records.sort(key=lambda m: (m['game_start_ms'], m['match_id']))
    metadata = dict(schema=DATASET_SCHEMA, sample_id=sample_id, selection_sha256=run['selection_sha256'],
                    sampling_status=run['status'], selected_matches=len(selected), normalized_matches=len(rows),
                    missing_normalized_ids=sorted({s['match_id'] for s in selected} - set(ids)), exclusions=exclusions,
                    eligible_matches=len(records), snapshot_policy='positive_time; before final frame, duration and GAME_END; events <= timestamp')
    return {'metadata': metadata, 'matches': records}


def load_dataset(path):
    data = json.loads(Path(path).read_text())
    if data['metadata']['schema'] != DATASET_SCHEMA:
        raise ValueError('Unsupported dataset schema')
    validate_records(data['matches'])
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = extract(os.environ['DATABASE_URL'], args.sample_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False))
    print(json.dumps({'matches': len(data['matches']), 'dataset_sha256': digest(data), 'metadata': data['metadata']}, indent=2))

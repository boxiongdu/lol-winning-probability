"""Export only modeling fields from A's pg_dump archive; never execute dump SQL.

Requires a compatible PostgreSQL pg_restore binary, not a running database.
Raw responses/player identities/session tables are never exported.
"""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from src.features.champion_dataset import assemble_dataset


def unescape(value):
    if value == r'\N':
        return None
    escape = {'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v', '\\': '\\'}
    def replace(match):
        s = match.group(1)
        if s.startswith('x') and len(s) > 1:
            return chr(int(s[1:], 16))
        if s[0] in '01234567':
            return chr(int(s, 8))
        return escape.get(s, s)
    return re.sub(r'\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3}|.)', replace, value)


def table_rows(dump_path, table, pg_restore='pg_restore'):
    # Only allow the explicitly needed tables; do not execute any SQL from input.
    if table not in ('sampling_runs', 'sampling_matches', 'matches', 'participants', 'participant_frames', 'events'):
        raise ValueError('Table is outside the modeling export')
    with tempfile.TemporaryFile(mode='w+') as err:
        process = subprocess.Popen([str(pg_restore), '--data-only', '--no-owner', '--no-privileges',
            '--file', '-', '--table', table, str(dump_path)], stdout=subprocess.PIPE, stderr=err, text=True, encoding='utf-8')
        columns, found = None, False
        try:
            for line in process.stdout:
                if line.startswith('COPY public.' + table + ' ('):
                    columns = line.split('(', 1)[1].split(')', 1)[0].split(', ')
                    found = True
                elif columns is not None:
                    if line.rstrip('\n') == r'\.':
                        columns = None
                        continue
                    values = line.rstrip('\n').split('\t')
                    if len(values) != len(columns):
                        raise ValueError(f'Malformed COPY record in {table}')
                    # Leave COPY escapes intact until the specific field is used.
                    yield dict(zip(columns, values, strict=True))
            if process.wait() != 0:
                err.seek(0)
                raise ValueError('pg_restore could not read archive: ' + err.read(2000))
            if not found:
                raise ValueError(f'Missing table data: {table}')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
                process.wait()


def extract_dump(dump_path, sample_id, pg_restore='pg_restore'):
    sample_id = str(UUID(sample_id))
    run = None
    for row in table_rows(dump_path, 'sampling_runs', pg_restore):
        if row['sample_id'] == sample_id:
            run = {k: unescape(row[k]) for k in ('selection_sha256', 'status')}
    if run is None:
        raise ValueError('sample_id not found in archive')
    selected = [{'match_id': unescape(r['match_id'])} for r in table_rows(dump_path, 'sampling_matches', pg_restore) if r['sample_id'] == sample_id]
    selected_ids = {r['match_id'] for r in selected}
    rows = []
    for row in table_rows(dump_path, 'matches', pg_restore):
        if unescape(row['match_id']) not in selected_ids:
            continue
        m = {k: unescape(row[k]) for k in ('match_id', 'patch')}
        m.update({k: int(row[k]) for k in ('game_start_ms', 'duration_seconds', 'queue_id', 'map_id')})
        m.update({k: row[k] == 't' for k in ('blue_win', 'early_surrender')})
        rows.append(m)
    ids = {r['match_id'] for r in rows}
    participants = defaultdict(lambda: {100: [], 200: []})
    player_teams = {}
    for row in table_rows(dump_path, 'participants', pg_restore):
        mid = unescape(row['match_id'])
        if mid in ids:
            team = int(row['team_id'])
            participants[mid][team].append(int(row['champion_id']))
            key = (mid, int(row['participant_id']))
            if key in player_teams:
                raise ValueError('Duplicate participant in archive')
            player_teams[key] = team
    aggregate = defaultdict(lambda: [0, 0])
    participant_keys = set()
    for row in table_rows(dump_path, 'participant_frames', pg_restore):
        mid = unescape(row['match_id'])
        if mid in ids:
            ts, pid = int(row['timestamp_ms']), int(row['participant_id'])
            key = (mid, ts, pid)
            if key in participant_keys:
                raise ValueError('Duplicate participant frame in archive')
            participant_keys.add(key)
            total = aggregate[mid, ts, player_teams[mid, pid]]
            total[0] += int(row['total_gold'])
            total[1] += 1
    frames = defaultdict(list)
    for (mid, ts, team), (gold, count) in aggregate.items():
        frames[mid].append((ts, team, gold, count))
    del aggregate, participant_keys
    events = defaultdict(list)
    for row in table_rows(dump_path, 'events', pg_restore):
        mid, kind = unescape(row['match_id']), row['event_type']
        if mid in ids and kind in ('CHAMPION_KILL', 'BUILDING_KILL', 'ELITE_MONSTER_KILL', 'GAME_END'):
            payload = json.loads(unescape(row['payload'])) if kind in ('BUILDING_KILL', 'ELITE_MONSTER_KILL') else {}
            team = unescape(row['credited_team_id'])
            events[mid].append((int(row['timestamp_ms']), int(team) if team is not None else None,
                               kind, payload.get('buildingType' if kind == 'BUILDING_KILL' else 'monsterType')))
    data = assemble_dataset(rows, participants, frames, events, selected, run, sample_id)
    h = hashlib.sha256()
    with Path(dump_path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            h.update(block)
    data['metadata']['archive_sha256'] = h.hexdigest()
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dump', type=Path, required=True)
    parser.add_argument('--sample-id', required=True)
    parser.add_argument('--pg-restore', default='pg_restore')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = extract_dump(args.dump, args.sample_id, args.pg_restore)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False))
    print(json.dumps(data['metadata'], indent=2))

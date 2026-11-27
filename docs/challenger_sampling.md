# Member A: Challenger-roster sampling

## Design
Use every unique player in the returned Challenger solo/duo roster, then randomly
select up to 10 detail-eligible matches per player. Use the actual roster size;
300 players would mean up to 3,000 player-match selections, not 3,000 unique games.
Overlap is removed globally before timelines are downloaded.

Roster endpoint (platform host):
`https://na1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5`
Match history/details/timelines use the corresponding regional host (`americas`
for na1). The collector expects current roster entries containing `puuid` and
fails clearly rather than silently skipping players whose identifiers are missing.

No player IDs or seeds.local.json need to be entered manually for this command.
A Riot API key and PostgreSQL are still required. No model, simulation, app or
Airflow changes are part of this feature.

## 1. Use a separate database for the modeling dataset

The normal database can contain older handpicked matches. Sample membership is
stored explicitly, but B's current model trainer reads the entire normalized
database. Prefer a clean Challenger database to prevent accidentally mixing them:

```bash
docker compose up -d --wait db
docker compose exec -T db createdb -U rift rift_challenger
export DATABASE_URL=postgresql://rift:rift_local@localhost:5432/rift_challenger
python -m src.ingestion init-db
```

Run createdb only once. If the database already exists, reuse it deliberately;
do not drop it to resume. Substitute your actual password if changed. The exported
DATABASE_URL overrides the root .env; persist it in .env if this is now your working
dataset. The existing rift database is preserved. Activate the project's Python
3.11 environment and install requirements.txt before running the commands.

## 2. Choose a fixed retrospective window and collect

Set RIOT_API_KEY locally. Example dates below are illustrative UTC boundaries;
agree the actual date/patch scope with B. The end date is exclusive.

```bash
python -m src.ingestion collect-challenger \
  --platform na1 \
  --start-date 2026-11-01 \
  --end-date 2026-11-27 \
  --matches-per-player 10 \
  --sampling-seed 42 \
  --manifest data/raw/challenger_november.json
```

Optional `--patch 16.23` restricts to an agreed patch (example only). Alternatively
use --start-time and --end-time as Unix seconds, without the date flags. Platform
determines the Match-V5 regional route for collect-challenger. It always uses
queue 420 and map 11. Regular later surrenders remain eligible; early surrender
or duration below --min-duration (default 300 seconds) is excluded.

--target and --per-player belong to manual-seed collection; use
--matches-per-player for this sampler. There is no random player selection: every
player in the saved roster snapshot is processed.

## 3. Understand what is randomized

For each roster player:
1. Page through the bounded ranked-solo match-ID history.
2. Sort/deduplicate IDs and shuffle using a stable hash of sampling seed + PUUID.
3. Inspect the shuffled candidates until 10 pass detail eligibility or the pool
   is exhausted. Cached match details are reused across players and reruns.
4. Save each inspection, rejection and selection to the local manifest.

Taking the first k eligible IDs in a uniformly shuffled pool samples eligible
matches without always selecting the newest games. It avoids downloading details
for every unused candidate. Selected timelines are then fetched and fully validated
using A's existing normalizer. If a selected timeline is missing or invalid, retain
its membership and report a shortfall; do not silently draw a replacement.

By default, --history-limit 1000 is a per-player safety cap. An extra ID probes for
a larger window. The collector refuses a truncated pool instead of claiming it
sampled the entire window. If the cap is exceeded, narrow the window or increase
the cap using a new manifest path. API retention/availability still bounds what
can be retrieved, even below this cap.

## 4. Resume exactly the same selection

Rerun the identical command with the same database and manifest path. The roster,
per-player history pools, cursors and selections are reused; saved raw responses
are not downloaded again. Authentication/transient errors stop collection and
preserve checkpoints. Detail 404s are recorded as unavailable candidates.

The selection SHA256 becomes immutable once sampling finishes. Changed arguments
or inconsistent manifests fail rather than silently resampling. To create a new
sample, choose a new manifest path. Same seed alone is not enough for reproduction:
retain the roster snapshot, pools, manifest, raw data and code version as well.

Use one collector per API key/database at a time. A PostgreSQL session advisory
lock prevents concurrent use of the same manifest path within one database and
releases automatically on connection loss. Do not run the same manifest against
different databases concurrently.

## 5. Verify and hand off

The final JSON prints sample_id, selection_sha256, actual roster size, total
player-match selections, unique selected IDs, overlap removed, player shortfalls,
detail rejection counts and ingestion metrics. Progress on stderr omits PUUIDs.
Exit 0 means all roster players supplied the requested number and all selected
matches validated. Exit 2 means shortfalls/partial coverage. Exit 1 means a fatal
input/API/database error. Up to 3,000 selections is a ceiling, not a target guarantee.

```bash
python -m src.ingestion status
python -m src.ingestion audit \
  --region americas --sample-id SAMPLE_UUID_FROM_OUTPUT \
  > data/processed/challenger_coverage.json
```

Create data/processed first if needed. Apply the same optional patch/date filters
when comparing scoped reports. The manifest contains player identities and is
stored under gitignored data/raw by default. Keep it private alongside the database
backup; never commit it. API keys, headers and DATABASE_URL are never saved in it.

Database additions from migration 003:
- sampling_runs: frozen full manifest, selection hash, latest status and metrics.
- sampling_matches: unique match membership and number of selecting seed players.

To inspect only this sample in a database containing other records:

```sql
SELECT c.*
FROM match_compositions c
JOIN sampling_matches sm USING (match_id)
WHERE sm.sample_id = 'SAMPLE_UUID_FROM_OUTPUT'
  AND c.composition_valid;
```

B must apply sample membership when using a mixed database; the current model
trainer is unchanged. On a dedicated database, normal historical tables contain
only normalized matches selected by this sampler. Inspected-but-rejected raw match
details may still be cached and must not be treated as sample members.

Share a full database backup plus the manifest, audit, code SHA, and final JSON.
The backup retains sampling membership. The existing export/restore README applies.

## Sampling limitations
This is a current Challenger-roster seed sample from one server, not a uniform
sample of all ranked matches or proof that every participant was Challenger at
match time. Shared games have more opportunities to enter the union. Repeated
players, rank changes, API availability and patch/date scope remain limitations.
The manifest exposes overlap and per-player selection counts for B's evaluation.
No rank-based modeling weights or new model-training behavior are implemented here.

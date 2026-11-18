# Member A — Week 8 ingestion and storage

## Status
Implemented: Riot-ID/PUUID discovery, pagination, conservative request pacing,
429 waits, bounded transient retries, raw JSONB caching, normalized PostgreSQL
storage, validation, duplicate prevention, resume/rebuild, run metrics and tests.

**Pending acceptance evidence:** a real API key and local seed identities are needed
for the initial 100 complete historical matches. The included DEMO_1 payloads are
synthetic test data, not collected Riot matches or evidence of model performance.

## 1. Prepare Python and PostgreSQL
From the repository root on `feature/data-ingestion`:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d --wait db
python -m src.ingestion init-db
```

Windows PowerShell: activate with `.venv\Scripts\Activate.ps1`; copy using
`Copy-Item .env.example .env`. Use Docker Compose v2. If PostgreSQL is already
available, skip Docker and set DATABASE_URL to a dedicated project database.
The example password is for a loopback-only local development database. If you
change POSTGRES_PASSWORD, update DATABASE_URL to match. Existing Docker volumes
retain the password set at initial creation.

All commands load the root `.env`, preserving exported environment values.
The SQL migration is idempotent and invoked by every command.

## 2. Test the offline path first
Use a demo/test database separate from real collection:

```bash
python -m src.ingestion load-fixture --directory data/sample
python -m src.ingestion status
python -m src.ingestion rebuild
python -m src.ingestion status
```

Expected synthetic counts: 2 raw responses, 1 match, 10 participants,
210 participant frames, 4 events. The `team_frame_states` view has 40 rows
(20 nonterminal timestamps times 2 teams). Repeated loads and rebuilds preserve
these counts. Raw responses keep their original fetched time and hash.

## 3. Configure real collection
Use a clean database without DEMO_1. Set RIOT_API_KEY in your local `.env`.
Copy `configs/seeds.example.json` to `configs/seeds.local.json` and replace the
placeholder with real Riot IDs. You can instead provide `{"puuid": "..."}` per
seed. Seed files must be a nonempty JSON array. Do not commit private seeds.
Use multiple players if one history cannot yield 100 eligible complete matches.

```bash
python -m src.ingestion collect --seeds configs/seeds.local.json --target 100 --per-player 200
python -m src.ingestion status
```

Default policy: regional route `americas`, queue 420, map 11, modern Match-V5
payloads with gameDuration in seconds, minimum duration 300 seconds, and exclude
early surrender. This is an operational remake/short-game heuristic, not an exact
remake label. Regular later surrenders remain eligible. Patch is recorded, not
filtered; B can choose a patch range. Seed-player sampling is not representative
of all players or skill levels.

Optional `--start-time` and `--end-time` use Unix seconds and are applied both to
discovery and acceptance. `--queue`, `--map`, `--min-duration`, `--region` also work
for rebuild. Keep one dataset policy per database. Rerunning with another policy
re-evaluates cached records and removes previously normalized excluded matches.

The target is the number of accepted matches visited from this database's regional
queue, including cached matches. It is not 100 additional matches per invocation.
All discovered IDs remain queued for future runs. Exit 0 means target met with no
processed failures, 2 means partial coverage, and 1 means configuration/API/database
failure. Check JSON metrics and `status`, not just process completion.

## 4. Resume and diagnose
Rerun the same collect command after a failure. Match details saved before a failed
timeline request are reused. History discovery runs again to find new IDs; raw
match/timeline downloads are skipped when already cached. Only one collector
should use a database/region at a time. Other processes sharing the same Riot key
can still exhaust limits. HTTP 401/403 stops collection; renew/check the key.
HTTP 429 waits at least Retry-After, then stops if the retry budget is exhausted.
HTTP 404 marks that match failed and continues to other candidates.

`collection_matches` records pending, complete, excluded and failed states with
safe error reasons. Invalid payloads remain in `raw_responses` for diagnosis;
no incomplete match enters structured tables. Raw data contains player identifiers
and belongs in the local database, not a public Git commit. Successful cached
responses are immutable: after a genuine API correction, an operator must explicitly
remove the affected cached row(s) before recollecting. Do not delete unrelated data.

## 5. Rebuild and inspect without Riot access

```bash
python -m src.ingestion rebuild
python -m src.ingestion status
```

Rebuild uses only cached response pairs. Re-normalization is atomic per match;
retries do not multiply frames or events. Missing pairs remain pending. Existing
valid data survives a database error while replacing that match.

With psql or another database client:

```sql
SELECT status, count(*) FROM collection_matches GROUP BY status;
SELECT match_id, count(*) FROM participant_frames GROUP BY match_id;
SELECT * FROM team_frame_states ORDER BY match_id, timestamp_ms, team_id LIMIT 10;
SELECT command, status, started_at, finished_at, metrics FROM ingestion_runs
ORDER BY started_at DESC;
```

## 6. Tests and teammate handoff

```bash
python -m pytest -q
```

Database tests require TEST_DATABASE_URL pointing to a dedicated PostgreSQL test
database. They create and drop an isolated temporary schema per test. Without it,
PostgreSQL tests are explicitly skipped; HTTP and validation tests still run.
GitHub Actions provisions PostgreSQL and runs the entire suite without Riot keys.
The synthetic fixture is only for plumbing tests, never model training.

Member B can read matches, participants, participant_frames, events and the
nonterminal team_frame_states view. Use event timestamps <= the selected state
for cumulative objectives. `credited_team_id` handles destroyed-building owner
semantics; inspect monster/building payload fields to distinguish objective types.
A raw count of BUILDING_KILL events also includes inhibitors, not only towers.
Do not expose final blue_win or duration as input features.

Member D can call `discover`, `process` and Database methods from Airflow task
wrappers or use the CLI and capture its JSON metrics. No Airflow runtime is added
in Member A's scope. Review the SQL contract with B/C before extending it.

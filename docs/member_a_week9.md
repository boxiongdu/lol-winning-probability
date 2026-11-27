# Member A — Week 9 data quality and session persistence

## Scope and status
A supplies historical data, audits, ordered migrations and PostgreSQL session storage.
This change does not implement champion encoding/training (B), the three-event
simulation (C), or UI/Airflow integration (D). The existing Week 8 application
continues to write JSONL until C/D explicitly call the new SessionStore API.

No production database, API key or seed list is available to this implementation
session. Actual expanded collection and coverage numbers must be measured on A's
local database. The commands below are ready; no new real dataset is claimed.

## 1. Upgrade without resetting data
Back up your database using src/ingestion/README.md, then activate Python 3.11:

```bash
python -m pip install -r requirements-dev.txt
docker compose up -d --wait db
python -m src.ingestion init-db
python -m src.ingestion status
```

initialize() now applies ordered SQL files using schema_migrations, checksums and
an advisory transaction lock. A legacy Week 8 database without a ledger safely
reapplies unchanged 001 (idempotent), then applies 002. Existing raw responses,
normalized matches and team_frame_states remain. Subsequent startup skips applied
files. Editing an applied file fails clearly; add a numbered migration instead.

002 adds match_compositions, game_sessions and game_transitions. Its positive
champion CHECK is NOT VALID so old invalid rows do not block upgrading; new writes
are checked. Audit/rebuild legacy data before validating the constraint manually.
Do not delete Docker volumes to apply migrations.

## 2. Audit champion coverage

```bash
mkdir -p data/processed
python -m src.ingestion audit --region americas \
  > data/processed/champion_coverage.json
```

Optional common scope for collection/rebuild/audit:

```bash
python -m src.ingestion audit --region americas --patch 16.22
```

The example patch is illustrative; agree the actual patch scope with B. Reports
include scope, eligible real matches, invalid composition IDs, full-version patch
counts, date coverage, observed champion frequencies by side, sparse appearances,
and regional collection status/rejection counts. DEMO_1 is explicitly excluded.
An empty database produces a valid zero-coverage report, not proof of acceptance.
Only observed champions appear: this is not a full champion catalog.

Audit reads a consistent snapshot and does not delete or repair records. It expects
ten unique positive champion IDs, five on each team. Historical IDs are preserved
as observed; catalog membership and names need a patch-matched catalog from B/D.

## 3. Expand collection as API access permits
Add multiple seed players to the private configs/seeds.local.json, preferably
across the intended rank population. Set a valid local key and then, for example:

```bash
python -m src.ingestion collect --seeds configs/seeds.local.json \
  --region americas --target 1000 --per-player 200 --patch 16.22
python -m src.ingestion audit --region americas --patch 16.22 \
  > data/processed/champion_coverage.json
```

1000 is an example collection goal, not a guarantee of adequate modeling coverage.
Use the actual agreed patch. Date bounds use --start-time/--end-time in Unix seconds.
Changing collection policy reevaluates/removes normalized records outside that policy;
raw cache remains intact. Use a separate database for a separate dataset policy.

Ingestion now rejects duplicate/nonpositive champion IDs. Rebuild cached data to
apply validation without API calls. Raw payloads remain for investigation; invalid
records are quarantined through collection_matches. Run metrics now record scope.

## 4. B's historical-data handoff

```sql
SELECT match_id, game_start_ms, patch, blue_champion_ids, red_champion_ids, blue_win
FROM match_compositions
WHERE composition_valid AND match_id <> 'DEMO_1'
ORDER BY game_start_ms, match_id;
```

Composition arrays are ordered by participant_id, **not lane role**. B owns encoding,
features, splits, calibration, opening/in-game models and warnings. No model changes
are included. blue_win is the label only. See docs/data_dictionary.md and audit output.

## 5. C/D persistence contract (version 1)

```python
from uuid import uuid4
from src.config import database_url
from src.storage.database import Database
from src.storage.sessions import SessionStore

versions = {
    "dataset": "immutable-dataset-id",
    "opening_model": "opening-artifact-sha",
    "in_game_model": "in-game-artifact-sha",
    "features": "encoder-version",
    "scenario": "scenario-content-sha",
    "simulator": "simulator-version",
    "champion_catalog": "catalog-version",
}
# C supplies initial_state: see the shape below.
with Database(database_url()) as db:
    db.initialize()
    store = SessionStore(db)
    session = store.create_session(
        uuid4(), initial_state=initial_state, random_seed=42, versions=versions
    )
```

Initial state shape (illustrative IDs only):

```json
{
  "elapsed_seconds": 0,
  "blue_champion_ids": [1, 2, 3, 4, 5],
  "red_champion_ids": [6, 7, 8, 9, 10],
  "blue": {"total_gold": 2500, "kills": 0, "towers_destroyed": 0, "dragons_secured": 0, "barons_secured": 0},
  "red":  {"total_gold": 2500, "kills": 0, "towers_destroyed": 0, "dragons_secured": 0, "barons_secured": 0},
  "objective_availability": {"dragon": false}
}
```

The actual initial resource rule and objective availability belong to C. Storage
checks shape, nonnegative integer statistics, finite clock, five positive IDs per
side, ten unique IDs, equal initial team statistics and a 00:00 start. It does not
verify champion names/catalog membership, lane roles or objective eligibility.
Additional JSON fields are preserved. Phase, event_count and revision are stored
in the session envelope; do not duplicate them inside current_state.

Prediction payload supplied by B:

```json
{"p_blue": 0.5, "stage": "opening", "model_version": "opening-artifact-sha", "coverage_warnings": []}
```

For later states use stage=in_game and the pinned in_game_model identifier.
Model identifiers must match versions captured at creation. New model publication
must not change an existing session's prediction source. Versions are references;
B/C/D must retain the actual referenced artifacts for executable replay.

### Functions

| API | Behavior |
| --- | --- |
| create_session(session_id, initial_state=..., random_seed=..., versions=...) | Creates a draft after picks are complete; identical retries return current saved session; conflicting creation data fails |
| load_session(session_id) | Latest committed state, phase, revision, event_count, winner, pinned versions and latest prediction |
| transitions(session_id) | All committed transitions ordered by sequence; UUIDs and timestamps returned as strings |
| append_transition(session_id, transition_id, expected_revision=..., kind=..., after_state=..., prediction=..., ...) | Atomic append and snapshot replacement; returns the committed transition |

One SessionStore per Database connection; do not share a connection across threads.
Every mutating call owns its transaction and returns after commit. Reads use the
Database connection's dict rows. No implicit database initialization in SessionStore.

### Transition protocol

1. **start:** draft -> in_progress. Preserve the exact initial state at 00:00;
   supply the opening prediction. event/action/resolution/winner fields are absent.
2. **progression:** supply C's later state and B's in-game prediction. Time must
   advance. This records background progression without consuming an event.
3. **decision:** supply next event_index (1, 2, 3), unique event_id, action_id,
   resulting state and in-game prediction. Supply resolution={random_seed,
   random_draw, success_chance, outcome}. Clock advances; picks cannot change.
4. Repeat progression/decision as C requires; exactly three decision indices exist.
5. **finish:** after three decisions, supply an end state with later time and
   winner='blue' or 'red'. Supply resolution={random_seed, random_draw,
   terminal_p_blue}. Retain the previous prediction unchanged (final pre-resolution
   probability); terminal_p_blue must equal its p_blue. Storage records the supplied
   winner and draw; it does not sample the winner or recompute effects.

For example, the application service can persist an already-computed decision:

```python
saved = store.load_session(session_id)
transition = store.append_transition(
    session_id, transition_id,  # allocate one UUID per logical operation, reuse on retry
    expected_revision=saved["revision"],
    kind="decision",
    after_state=resolved_state,  # from C
    prediction=prediction_result,  # from B
    event_index=next_event_index,
    event_id=event_id,
    action_id=action_id,
    resolution={"random_seed": round_seed, "random_draw": draw,
                "success_chance": chance, "outcome": outcome},
)
```

### Failure and retry requirements
- Submit a fixed transition UUID and the original full request on network retry.
  An exact retry returns the original record, even after later transitions.
- A reused UUID with different data fails with SessionConflict.
- Every new operation needs the latest expected_revision. Stale writes fail;
  reload before choosing how to proceed. Do not automatically reapply stale effects.
- The session row is locked during writes: concurrent submissions cannot both
  consume the same revision. Event index and event identity also have unique keys.
- Database exceptions roll back both transition insertion and session replacement.
  Update UI state only after a successful return; reload to recover after uncertainty.
- SessionNotFound indicates missing session; StorageInputError indicates invalid
  payload. SessionConflict indicates revision, idempotency, phase or ordering errors.
- C owns action eligibility and outcome/draw consistency, including success chance
  rules and winner sampling. A validates supplied metadata ranges and persistence.

## 6. Verify A's checkpoint

```bash
TEST_DATABASE_URL=postgresql://rift:rift_local@localhost:5432/rift_checkpoint_test \
  python -m pytest -q
```

Use a dedicated existing test database. Tests create/drop isolated schemas. Without
TEST_DATABASE_URL, database tests skip explicitly. Coverage includes legacy upgrade,
migration checksum checks, duplicate picks, patch filtering, audit counts, full
supplied-session persistence, stale/conflicting retries, concurrent clicks and
rollback after a failed snapshot write. No Riot key is used by tests.

Production acceptance additionally requires A to run the audit on real data and
share its report with B. C/D integration and the actual three-event game remain
other agents' work; these tests only supply fixed states to verify A's storage.

## Automatic Challenger sampling extension

The new `collect-challenger` command discovers every player in a saved Challenger
roster and samples up to 10 eligible matches per player. Manual seed files remain
supported by the original collect command. Migration 003 records frozen sample
membership; audit can filter with --sample-id. See challenger_sampling.md for the
actual run and handoff procedure. B/C/D code is unchanged.

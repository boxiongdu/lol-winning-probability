# Member D — Week 8: first playable interaction

## Status
Checkpoint met: a user picks an action, sees the sampled outcome, the updated
state on the map and stats panel, and the new Blue win probability in one page.
Each decision is logged. The Airflow DAG runs A's ingestion and B's training and
publishes the model only after validation. Compose defines db, app and Airflow.

## Files
| Path | What it does |
| --- | --- |
| `app/session.py` | UI-free decision loop: C's `resolve_action` → B's `predict_blue_win` → decision log |
| `app/map_view.py` | Plotly Summoner's Rift (drawn shapes, no Riot assets) and probability history chart |
| `app/streamlit_app.py` | Page: map, event panel, action buttons, team stats, probability bar/metric, seed/restart |
| `dags/rift_pipeline.py` | DAG `rift_pipeline`: init_db → ingest → check_training_data → train_candidate → publish_model |
| `dags/rift_tasks.py` | Plain-Python task bodies (testable without Airflow) |
| `docker/*.Dockerfile`, `compose.yaml` | `app` (Streamlit) and `airflow` (+ its own metadata DB) next to A's `db` |
| `tests/test_app_session.py`, `tests/test_dag_tasks.py` | 18 tests: loop, log fields, map, page click, ingest modes, publish gate, DAG shape |

## Run the game locally
```bash
python -m pip install -r requirements-app.txt
streamlit run app/streamlit_app.py        # http://localhost:8501
```
Needs only `models/week8.json` (committed by B). No database required.

## Run the full stack
```bash
cp .env.example .env                       # Linux: also set AIRFLOW_UID=$(id -u)
docker compose up -d --build
```
- Game: http://localhost:8501
- Airflow: http://localhost:8080 → `rift_pipeline` → Trigger. Params:
  `ingest_mode` (`auto` collects when `RIOT_API_KEY` and `configs/seeds.local.json`
  exist, otherwise rebuilds from cached raw data; `fixture` loads DEMO_1),
  `target_matches`, `min_matches` (default 10), `require_beats_baseline`.
- To train on A's handoff, restore it into the compose `db` first
  (`pg_restore ... --dbname=postgresql://rift:rift_local@localhost:5432/rift rift_handoff.dump`).

Airflow records each task's status, duration and errors; each task returns its
counts/metrics as XCom. With too few eligible matches `check_training_data` is
**skipped** (not failed) and the current model stays published. `publish_model`
refuses a candidate whose feature order differs, whose split manifest has a
match in two splits, whose sample prediction is outside [0, 1], or (by default)
whose validation log loss is worse than the baseline. It archives the previous
model to `models/archive/` and swaps the file atomically; the app reads the
model on every prediction, so no restart is needed.

## How it was verified
- Page driven in headless Chrome and via Streamlit's AppTest; screenshots before/after both actions.
- `airflow dags test rift_pipeline` on Airflow 3.1.8 against PostgreSQL 16 with 30 synthetic
  matches cloned from DEMO_1: all five tasks succeeded, 21/4/5 split, model published and the
  old one archived. A second run on an empty database skipped training and left the model unchanged.
- Fresh env from `requirements-dev.txt` with `TEST_DATABASE_URL`: 63 passed, 1 skipped (Airflow test).
- `docker compose config` is valid and the Airflow image's pip install resolves. **Images were not
  built or started** (Docker Hub unreachable from the dev machine): please run `docker compose up --build` once.

## Decision log
One JSON line per decision in `logs/decisions.jsonl` (gitignored; override with
`DECISION_LOG_PATH`). Fields cover the data contract: session_id, round,
scenario id/version/hash, action, success chance, random draw, outcome,
before/after state, probabilities before/after, session and round seed,
model version (SHA-256 prefix of the model file), feature order, simulator and
app versions. Week 9: move into a PostgreSQL table once A and C agree its schema.

## Display conventions (not model inputs)
The state stores tower *counts*, so the map shows destroyed towers in a fixed
order per side (`FALL_ORDER`), chosen to match the scenario: blue's existing
tower is red bot outer and the push takes red mid outer. Positions are
approximate Riot map units (0–15000, blue base bottom-left).

## For teammates
- **C:** please add `"starting_state"` to the scenario JSON; the app uses it
  automatically and falls back to the notebook's 12:00 state. Week 8 allows one
  decision per session (no objective availability yet); `take_action` enforces it.
  Rounds will use seed `session_seed + round − 1`.
- **B:** confirmed in the UI: a successful mid-tower push *lowers* Blue's
  probability (63.4% → 55.5%) because `tower_diff` has a negative weight.
  Users will notice in a demo. The committed `models/week8.json` has no `splits`
  manifest; retrained models do, and the publish gate requires it.
- **A:** `.env.example` was deleted in `ff2f79e` but the runbooks still copy it;
  restored with the same values plus `RANDOM_SEED` and `AIRFLOW_UID`. The DAG
  calls your CLI in-process, so `ingestion_runs` bookkeeping is unchanged.
- **All:** `requirements-dev.txt` now includes the modeling and app requirements,
  so CI also runs B's model tests and my tests (before, CI lacked scikit-learn).

## Week 9
Three rounds with history and recap, restart/replay from the log, DAG metrics
table and offline mode, fresh-clone instructions.

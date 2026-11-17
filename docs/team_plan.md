# Team responsibilities and weekly plan

## Overall description
Build a three-decision strategy simulation backed by historical Riot data.
The engineering pipeline ingests API data, stores it in PostgreSQL, validates and
transforms it, trains a win-probability model, and publishes artifacts.
The game samples action success, updates the state and time, predicts from the
new state, and displays the result on an interactive map.

## Ownership
| Member | Role | Key aspects |
| --- | --- | --- |
| A | Ingestion and storage | APIs, data engineering, database design |
| B | Features and prediction | Feature engineering, ML, evaluation, EDA |
| C | Decision engine | Simulation, backend logic, state management |
| D | Map and execution | Visualization, integration, orchestration |

Names will be assigned by the team. Each member owns tests and documentation
for their component and roughly one-quarter of the final presentation.
A/B provide executable pipeline functions; D wires them into Airflow.

## Week 8: first working decision
- **A:** Agree schema with B; implement discovery, pagination, retries and raw storage;
  normalize frames/events; collect about 100 complete matches; deduplicate records.
- **B:** Agree model inputs with C; build timestamped features; split whole matches
  chronologically; train a baseline and initial model; expose a prediction function.
- **C:** Define state and one event with at least two actions; configure success and
  failure effects and durations; implement seeded sampling; define logs with A.
- **D:** Render a sample map; add events, controls and probability display; connect C
  to B; establish container setup and initial DAG using A/B task functions.
- **Checkpoint:** One stored scenario supports action selection, sampled outcome,
  valid state update, model prediction, map display and database logging.

## Week 9: complete and stabilize
- **A:** Expand toward 1,000–3,000 matches as practical; test interrupted-run
  recovery; validate coverage; freeze a reviewed offline sample and data dictionary.
- **B:** Evaluate baseline/model with Brier score, log loss and calibration; produce
  gold/objective EDA; freeze feature order, model, split manifest and versions.
- **C:** Complete three sequential rounds; enforce objective availability and
  cumulative effects; test both outcomes for each action; log seeds/draws and versions.
- **D:** Synchronize map/state/probability history; add restart and recap; complete
  DAG metrics and Docker setup; integrate offline mode and fresh-clone instructions.
- **Midweek checkpoint:** Three decisions work, expanded data and evaluation exist.
- **End-week checkpoint:** Full pipeline, offline game, EDA and clean setup pass.
  Freeze features and reserve Week 10 for corrections.

## Week 10: verify and present
- **A:** Fresh-clone test; verify schema/data; present API and database quality.
- **B:** Verify loaded model and input ordering; present model and actual findings.
- **C:** Exercise every branch; verify restart/replay; explain simulator assumptions.
- **D:** Rehearse audience demo; show orchestration; record backup and coordinate release.
- **All:** Review code, finalize PowerPoint, tag the tested repository release.

## Acceptance criteria
- Actual API ingestion and database storage demonstrated.
- Airflow records task status, duration, counts and failures.
- No duplicate loads or match overlap across model splits.
- Historical features exclude future information and terminal outcomes.
- Every simulated transition maintains all model inputs and advances time.
- Decisions and probabilities are reproducible from recorded versions and state.
- Offline demo and analysis run from documented setup.

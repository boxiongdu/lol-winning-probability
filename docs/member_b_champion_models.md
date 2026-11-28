# Member B: champion-aware probability handoff, v1

The opening and in-game interfaces are implemented and trained on A's 2,443-match
sample. **Champion features did not establish an improvement on the held-out test
set.** Treat these models as an experimental release. The existing Week 8
`predict_blue_win` API and artifact remain available; this release does not change
C's engine, D's application, action probabilities, progression, or persistence.

## C and D: executable calls

From the repository root, Python 3.11+ needs no external packages for inference:

```bash
python -m src.modeling.example_champion
```

```python
from src.modeling.predict import ChampionPredictor, predict_win_probability

# Load this exact bundle once; keep its files for replay of saved sessions.
predictor = ChampionPredictor("models/champion_v1/bundle.json")
game = predictor.bind(
    blue_champion_ids=[11, 22, 43, 64, 103],
    red_champion_ids=[18, 54, 59, 89, 112],
)
versions = game.versions  # use these in A's versions envelope at session creation

opening = game.predict({"elapsed_seconds": 0})
# p_blue=0.47483158118849783; stage="opening"; coverage_warnings=[]

# C supplies the fully updated cumulative state after an event.
after_event = game.predict({
    "elapsed_seconds": 720,
    "blue": {"total_gold": 22000, "kills": 9, "towers_destroyed": 1,
             "dragons_secured": 1, "barons_secured": 0},
    "red": {"total_gold": 20000, "kills": 7, "towers_destroyed": 0,
            "dragons_secured": 0, "barons_secured": 0},
})
# p_blue=0.6745836756984929; stage="in_game"; coverage_warnings=[]
# D displays 100 * result["p_blue"] and preserves the full payload in logs.
```

The result always has exactly `p_blue`, `stage`, `model_version`, and
`coverage_warnings`, matching A's Week 9 prediction payload. Red probability is
`1 - p_blue`. `game.versions` contains `dataset`, `features`, `opening_model` and
`in_game_model`; C/D supply the other scenario/simulator/catalog versions required
by A. Each session must retain this bundle, not reload a newly published default.

`predictor.predict_opening(blue_ids, red_ids)` is an explicit opening entry point.
`predictor.predict(state)` or `predict_win_probability(state, model_path=...)`
is a one-shot entry point requiring `blue_champion_ids` and `red_champion_ids` in
the state. Use `bind()` for successive states: picks are copied and fixed, both
optional state pick arrays must match the bound teams, and timestamps cannot go
backward. Reordered picks are equivalent because this encoder has no role order.
Equal-time retries are allowed. Use one bound object per game, not across sessions.

At exactly 00:00 only the opening model runs; no statistics are required or read.
At any positive timestamp the in-game model uses the six existing state inputs
plus the fixed composition. **The handover can jump; continuity, monotonicity and
smoothness are not promised.** No artificial smoothing or champion/action bonuses
are applied. Positive times below the earliest training snapshot (60 seconds) or
above 3060.88 seconds return a coverage warning. Nonterminal model predictions do
not decide the sampled winner or override the game's terminal resolution.

## Feature and validation contract

- Exactly five positive integer champion IDs per side; ten distinct IDs overall.
  Strings, booleans, duplicate picks and incomplete teams fail with `ValueError`.
- Shared `CompositionEncoder` uses the sorted **training-only** vocabulary of 173
  observed IDs. Each column is +1 blue, -1 red, 0 absent. It is neither centered nor
  standardized. Order and version are saved in `encoder.json` and `bundle.json`.
- IDs unseen during training get no column and contribute zero, without a learned
  unknown-champion coefficient. A warning such as
  `unseen_champion:blue:999999:no_learned_effect` identifies the side and ID.
- Champions with fewer than 20 training-match appearances return
  `sparse_champion:blue:ID:train_matches=N`. This threshold is a diagnostic rule;
  having 20 or more observations does not establish reliability. Counts are per
  match, never inflated by repeated frames. Nine training champions are sparse.
- In-game order: `elapsed_seconds`, `gold_diff`, `kill_diff`, `tower_diff`,
  `dragon_diff`, `baron_diff`, then the same champion columns. All differences are
  blue minus red. Only the first six columns use training-fitted mean/scale.
- The existing state contract requires cumulative nonnegative integer team totals
  and finite nonnegative elapsed seconds. Extra state fields, final outcomes,
  durations, positions and action labels are not model inputs.
- Coverage warnings describe known gaps, not a calibrated uncertainty interval.
  The additive model does not learn roles, pair interactions, counterpicks or
  explicit patch effects. Coefficients are observational associations, not causal
  effects of picking a champion or taking an action.

## Reproduce from A's handoff

Use the exact archive whose SHA256 is
`5b143c532497d3862acf9597dc4218e1d425f6318402db1ef59851f1cce7d1d2`.
The supplied UUID is A's **sample_id**:
`4f67be78-4f12-4dc5-ae52-9a3760fcbf8b`.
Its selection SHA256 is recorded in the metrics; the batch status is `partial`,
but all 2443 selected matches are normalized, with no missing IDs or exclusions.
This does not mean all original roster players supplied the requested quota.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-champion.txt

# Requires a compatible pg_restore on PATH (verified with PostgreSQL 18.6).
# Extracts table data without executing archive SQL or starting a database.
python -m src.features.dump_dataset \
  --dump /path/to/rift_handoff.dump \
  --sample-id 4f67be78-4f12-4dc5-ae52-9a3760fcbf8b \
  --output data/processed/challenger_2443.json

# Use a new directory; published artifact directories are not overwritten.
python -m src.modeling.train_champion \
  --dataset data/processed/challenger_2443.json \
  --output models/champion_reproduced
```

An alternative read-only database exporter is available for A's already-restored
Week 9 database (including sampling tables):

```bash
python -m src.features.champion_dataset \
  --sample-id 4f67be78-4f12-4dc5-ae52-9a3760fcbf8b \
  --output data/processed/challenger_db.json
```

Set `DATABASE_URL` locally for that command. It uses a read-only repeatable-read
snapshot, filters sample membership, and never applies migrations. Database and
archive exports share state construction; provenance differs because only archive
export includes the archive checksum. Keep private raw responses, player sampling
manifests, credentials and the backup out of Git. The compact exported modeling
dataset is also gitignored; reproduction requires A's exact archived input.

## Evaluation protocol fixed before test evaluation

One opening composition/outcome record per match. In-game data uses positive-time
snapshots before the last timeline frame, the recorded duration, and GAME_END.
Only events at or before each snapshot are counted; inhibitor events are not
towers. Outcome is a label only. The opening and in-game models share eligible
matches and the chronological 1710 / 367 / 366 split. Tied start timestamps stay
together. All frames of each match stay in its split. Match IDs and dates are
recorded in `split_manifest.json`.

L2 logistic regression candidates use C in
`[0.0001, 0.001, 0.01, 0.1, 1, 10]`. Select the lowest validation **match-weighted
log loss**, breaking exact ties toward the smaller C. Vocabulary, statistics
scalers and coefficients fit training data only. There is no refit on validation,
test-based tuning, or fitted post-hoc calibration. Selected C values are opening
0.01, in-game 0.001, and tuned statistics-only 0.01.

The constant baseline is the training-match blue-win rate, 0.4812865497.
Statistics-only comparisons use the exact same positive snapshots and match
splits. One retrains the original Week 8 recipe (C=1, scaler, lbfgs, default
tolerance); the other tunes C on the same grid. The old 100-match trained weights
are not a fair comparator for this different dataset and are not used here.

Training retains the legacy one-vote-per-snapshot convention. Primary evaluation
gives each match equal total weight so long matches do not dominate scores.
Frame-weighted scores are also provided for compatibility. Brier, log loss,
ten-bin reliability tables, ECE and time-band metrics are saved. Paired bootstrap
intervals resample whole matches (2000 repetitions, fixed seed), not frames.
Repeated players may still cross splits; these intervals do not account for that
dependence or sampling bias. Binned calibration/ECE does not prove calibration in
new populations or patches.

## Versioned artifacts

`models/champion_v1/` contains the portable `bundle.json`, encoder/vocabulary,
training selection table, evaluation and calibration tables, split manifest,
held-out predictions, source hashes and environment versions, plus file checksums.
The bundle stores all preprocessing and feature order; inference uses pure Python.

| Identifier | Value |
| --- | --- |
| Encoder | `champion-indicators-v1` |
| Features | `champion-state-v1` |
| Opening | `opening-v1-5acaef492d6d28a6cc1e` |
| In-game | `in_game-v1-c515d7861cb80473a0a0` |
| Dataset | `dataset-v1-ca6d65f0ddab40dd40dadd3f51e35992dc8104c3ff61dc34dcbbe16cdb1e4dc5` |

See [measured results](../reports/champion_v1.md). In-game champion features
underperform both statistics comparators on validation as well as test. There is
no basis to promote this experimental model over statistics-only for accuracy.
Publish new IDs and rerun a new declared experiment when expanding data/features;
do not reuse this test set for iterative model selection.

## Verification

```bash
python -m pip install pytest==9.0.2
python -m pytest tests/test_champion_models.py tests/test_week8_model.py -q
```

Tests cover unseen/sparse IDs, label and future-stat exclusion, source sample
filtering, whole-match/tied-date splits, test-set changes not affecting fitted
parameters or selected C, frozen session picks, handover, artifact checks and
training/serving parity. Full-project tests additionally need the existing
requirements-dev dependencies; database integration tests need TEST_DATABASE_URL.
The new archive-extraction path was exercised on the actual 2443-match backup.

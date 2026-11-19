# Week 8 win probability model

## Run on the handoff database

Use a new, empty PostgreSQL database to restore A's custom-format backup with a compatible pg_restore client. Set DATABASE_URL to the connection URI for that database in the shell. From the repository root:

```bash
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$DATABASE_URL" rift_handoff.dump
python -m pip install -r requirements-modeling.txt
python -m src.modeling.train
python -m unittest discover -s tests -p 'test_week8_model.py'
```

If the backup is already restored, start at the install step. D can call `src.modeling.train.train_model(database_url, model_path)` from Airflow. The return value includes split counts, evaluation metrics and the published path. Mount the same model file in the app container.

## C and D interface

```python
from src.modeling.predict import predict_blue_win

state = {
    'elapsed_seconds': 720,
    'blue': {'total_gold': 22000, 'kills': 9, 'towers_destroyed': 1,
             'dragons_secured': 1, 'barons_secured': 0},
    'red': {'total_gold': 20000, 'kills': 7, 'towers_destroyed': 0,
            'dragons_secured': 0, 'barons_secured': 0},
}
p_blue = predict_blue_win(state)  # defaults to models/week8.json
```

Each side reports cumulative nonnegative integer totals. The simulator can include additional fields. Call the predictor after applying the outcome and advancing elapsed time. The UI can display 100 * p_blue as a percentage.

## Training and evaluation

Use A's nonterminal team_frame_states view for gold and match each team's credited events through the snapshot timestamp. Count champion kills, enemy towers destroyed, dragons, and Barons. The same function creates the six ordered inputs at training and prediction time. The synthetic DEMO_1 fixture is excluded.

Sort distinct matches by game start, breaking ties by match ID. Allocate about 70%, 15%, 15% to training, validation and test respectively. All timestamps of one match share its split. Standardize features on training frames and fit logistic regression. The constant baseline is the fraction of training matches won by blue. Brier score and log loss are calculated over held-out frames, so longer matches contribute more observations. Lower scores are better. The JSON contains the split manifest, learned coefficients, standardization parameters, baseline and metrics. It is generated locally under models/.

## Verified run on A's November 18 PostgreSQL handoff

The handoff contains 100 eligible matches and 2,828 nonterminal prediction timestamps. Split membership covers every eligible match exactly once. The latest start time in each split precedes or equals the earliest in the next.

| Split | Matches | Snapshots | Logistic Brier | Baseline Brier | Logistic log loss | Baseline log loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Train | 70 | 2,009 | — | — | — | — |
| Validation | 15 | 418 | 0.1503 | 0.2500 | 0.4634 | 0.6931 |
| Test | 15 | 401 | 0.2196 | 0.2500 | 0.6120 | 0.6931 |

The constant training-match baseline is 0.50. Logistic regression improved both metrics on both held-out splits. Each held-out split contains only 15 independent matches. Replaying the saved JSON through the prediction interface reproduced held-out log loss to within 1e-10. The example state above returns 0.634293.

The handoff ZIP uses repository-relative paths. Extract it into the project root to load models/week8.json without retraining.

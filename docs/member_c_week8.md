# Member C — Week 8 game engine

## Status
One event (dragon) with two actions. Seeded success/failure, state update, and draft decision log. Tests pass.

## Files
- `configs/scenarios/dragon.json`: event, actions, success chances, effects, time costs
- `src/game/engine.py`: loads the scenario and resolves an action
- `tests/test_game_engine.py`: engine tests
- `dragon_demo.ipynb`: walkthrough of both actions and a sample decision log

## For D
```python
from src.game.engine import load_scenario, resolve_action
from src.modeling.predict import predict_blue_win

scenario = load_scenario()            # event text + actions (for the buttons)
result = resolve_action(state, "contest_dragon", seed=1)
p_blue = predict_blue_win(result["state"])
```
For a starting state, use `starting_state` from `dragon_demo.ipynb`.

`resolve_action` returns the new state, outcome, success chance, random draw, seed, and scenario and simulator versions. It never changes the state passed in.

**Week 8:** allow one click per event. The engine doesn't track objective availability yet, so calling it again can award the same dragon twice.

## Run tests
From the repo root:
```bash
python -m unittest discover -s tests -p 'test_game_engine.py'
```

## Known limits (Week 9)
- Only one event; three sequential decisions to come
- No objective availability tracking yet
- Actions don't change gold; amounts to agree with B
- Decision log table to design with A

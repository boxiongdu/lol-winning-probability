"""Run `python -m src.modeling.example_champion`; no database or sklearn needed."""
import json

from src.modeling.champion_predict import ChampionPredictor


def example():
    predictor = ChampionPredictor()
    game = predictor.bind([11, 22, 43, 64, 103], [18, 54, 59, 89, 112])
    opening = game.predict({'elapsed_seconds': 0})
    after_event = game.predict({
        'elapsed_seconds': 720,
        'blue': {'total_gold': 22000, 'kills': 9, 'towers_destroyed': 1,
                 'dragons_secured': 1, 'barons_secured': 0},
        'red': {'total_gold': 20000, 'kills': 7, 'towers_destroyed': 0,
                'dragons_secured': 0, 'barons_secured': 0},
    })
    return {'versions': game.versions, 'opening': opening, 'after_event': after_event}


if __name__ == '__main__':
    print(json.dumps(example(), indent=2))

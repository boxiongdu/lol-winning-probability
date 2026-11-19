import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.features.state import TEAM_FIELDS, features
from src.modeling.predict import predict_blue_win
from src.modeling.train import fit, training_rows


def sample_state():
    return {'elapsed_seconds': 720, 'blue': dict(zip(TEAM_FIELDS, [22000, 9, 1, 1, 0])),
            'red': dict(zip(TEAM_FIELDS, [20000, 7, 0, 0, 0]))}


class ModelTests(unittest.TestCase):
    def test_shared_features(self):
        state = sample_state()
        self.assertEqual(features(state), [720, 2000, 2, 1, 1, 0])
        db = [('M', 1000, True, 720000, i, *state[side].values())
              for i, side in [(100, 'blue'), (200, 'red')]]
        self.assertEqual(list(training_rows(db))[0][-1], features(state))
        with self.assertRaises(ValueError):
            list(training_rows(db[:1]))

    def test_invalid_state(self):
        state = sample_state()
        state['red']['kills'] = -1
        with self.assertRaises(ValueError):
            features(state)

    def test_split_and_inference(self):
        rows = [(f'M{i}', i, i % 2, [t, i % 2 * t, 0, 0, 0, 0])
                for i in reversed(range(20)) for t in [0, 60]]
        artifact = fit(rows)
        ids = list(artifact['splits'].values())
        self.assertEqual(ids, [[f'M{i}' for i in r] for r in (range(14), range(14,17), range(17,20))])
        self.assertEqual(artifact['baseline'], .5)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.json'
            path.write_text(json.dumps(artifact))
            self.assertTrue(0 <= predict_blue_win(sample_state(), path) <= 1)

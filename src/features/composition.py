"""Versioned, train-only champion vocabulary shared by fitting and prediction."""
from collections import Counter
from dataclasses import dataclass

ENCODER_VERSION = 'champion-indicators-v1'
SPARSE_THRESHOLD = 20  # Diagnostic threshold, not evidence of reliable effects.


def validate_composition(blue, red):
    teams = []
    for name, values in (('blue', blue), ('red', red)):
        if not isinstance(values, (list, tuple)) or len(values) != 5:
            raise ValueError(f'{name} must contain five champion IDs')
        if any(type(v) is not int or v <= 0 for v in values):
            raise ValueError('Champion IDs must be positive integers')
        teams.append(tuple(sorted(values)))
    if len(set(teams[0] + teams[1])) != 10:
        raise ValueError('Expected ten unique champion IDs')
    return tuple(teams)


@dataclass(frozen=True)
class CompositionEncoder:
    vocabulary: tuple
    counts: tuple

    def __post_init__(self):
        if (not self.vocabulary or self.vocabulary != tuple(sorted(set(self.vocabulary)))
                or any(type(v) is not int or v <= 0 for v in self.vocabulary)
                or len(self.counts) != len(self.vocabulary)
                or any(type(n) is not int or n < 1 for n in self.counts)):
            raise ValueError('Invalid champion vocabulary/counts')

    @classmethod
    def fit(cls, compositions):
        counts = Counter()
        for blue, red in compositions:
            blue, red = validate_composition(blue, red)
            counts.update(blue + red)
        vocabulary = tuple(sorted(counts))
        return cls(vocabulary, tuple(counts[c] for c in vocabulary))

    @property
    def feature_order(self):
        return tuple(f'champion_{c}' for c in self.vocabulary)

    def transform(self, blue, red):
        blue, red = validate_composition(blue, red)
        signs = dict.fromkeys(blue, 1)
        signs.update(dict.fromkeys(red, -1))
        counts = dict(zip(self.vocabulary, self.counts, strict=True))
        warnings = []
        for side, ids in (('blue', blue), ('red', red)):
            for champion in ids:
                if champion not in counts:
                    warnings.append(f'unseen_champion:{side}:{champion}:no_learned_effect')
                elif counts[champion] < SPARSE_THRESHOLD:
                    warnings.append(f'sparse_champion:{side}:{champion}:train_matches={counts[champion]}')
        return [signs.get(c, 0) for c in self.vocabulary], warnings

    def to_dict(self):
        return dict(version=ENCODER_VERSION, vocabulary=list(self.vocabulary),
                    training_match_counts=list(self.counts), feature_order=list(self.feature_order),
                    unseen_policy='zero_contribution_and_warning',
                    sparse_warning_threshold=SPARSE_THRESHOLD)

    @classmethod
    def from_dict(cls, data):
        if (data['version'] != ENCODER_VERSION
                or data['unseen_policy'] != 'zero_contribution_and_warning'
                or data['sparse_warning_threshold'] != SPARSE_THRESHOLD):
            raise ValueError('Unsupported encoder version or policy')
        result = cls(tuple(data['vocabulary']), tuple(data['training_match_counts']))
        if data['feature_order'] != list(result.feature_order):
            raise ValueError('Champion feature order mismatch')
        return result

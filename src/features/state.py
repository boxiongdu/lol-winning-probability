"""Shared simulator and training contract. Team counters are cumulative."""
import math

TEAM_FIELDS = ('total_gold', 'kills', 'towers_destroyed', 'dragons_secured', 'barons_secured')
FEATURES = ('elapsed_seconds', 'gold_diff', 'kill_diff', 'tower_diff', 'dragon_diff', 'baron_diff')


def features(state):
    seconds = state['elapsed_seconds']
    if type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('elapsed_seconds must be finite and nonnegative')
    for team in ('blue', 'red'):
        for field in TEAM_FIELDS:
            value = state[team][field]
            if type(value) is not int or value < 0:
                raise ValueError(f'{team}.{field} must be a nonnegative integer')
    return [seconds, *(state['blue'][key] - state['red'][key] for key in TEAM_FIELDS)]

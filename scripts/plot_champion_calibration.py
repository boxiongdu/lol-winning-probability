"""Render saved test calibration bins; no model fitting or new evaluation.

Optional dependency: matplotlib==3.10.8.
Run from repo root: python scripts/plot_champion_calibration.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
report = json.loads((root / 'models/champion_v1/metrics.json').read_text())
fig, axes = plt.subplots(2, 2, figsize=(11, 8), gridspec_kw={'height_ratios': [2, 1]}, constrained_layout=True)
palette = {'champion': '#087e8b', 'win_rate_baseline': '#cc6b32', 'statistics_only_legacy_C1': '#cc6b32', 'statistics_only': '#735db2'}
labels = {'champion': 'Champion features', 'win_rate_baseline': 'Training win-rate baseline',
          'statistics_only_legacy_C1': 'Original statistics recipe', 'statistics_only': 'Tuned statistics only'}
for col, (stage, keys) in enumerate([
    ('opening', ['champion', 'win_rate_baseline']),
    ('in_game', ['champion', 'statistics_only_legacy_C1', 'statistics_only']),
]):
    ax, hist = axes[0, col], axes[1, col]
    ax.plot([0,1], [0,1], '--', color='#9ca3af', linewidth=1, label='Perfect calibration')
    for key in keys:
        calibration = report['metrics']['test'][stage][key]['calibration']
        bins = [b for b in calibration['equal_width_bins'] if b['count']]
        ax.plot([b['mean_probability'] for b in bins], [b['blue_win_rate'] for b in bins],
                marker='o', linewidth=1.8, color=palette[key], label=labels[key])
        all_bins = calibration['equal_width_bins']
        hist.step([.05+i*.1 for i in range(10)], [b['match_mass']/366 for b in all_bins],
                  where='mid', color=palette[key], linewidth=1.8)
    ax.set(xlim=(0,1), ylim=(0,1), xlabel='Predicted blue-win probability', ylabel='Observed blue-win frequency',
           title='Opening · 366 matches' if stage=='opening' else 'In-game · 10,005 frames / 366 matches')
    ax.legend(loc='upper left', fontsize=8)
    hist.set(xlim=(0,1), ylim=(0,1), xlabel='Probability bin', ylabel='Fraction of match mass')
    for panel in (ax,hist):
        panel.spines[['top','right']].set_visible(False)
        panel.grid(alpha=.15)
fig.suptitle('Held-out calibration | Champion-aware v1\nEqual total weight per match; ten fixed-width bins', fontsize=15)
output = root / 'reports/champion_v1_calibration.png'
fig.savefig(output, dpi=180)
print(output)

# Initial shared data contract

Proposal to confirm before implementation; no database migrations exist yet.

## Historical states
One row per (match_id, timestamp_ms). Include game start time and patch for
chronological splitting and dataset provenance. The final blue-team result is a
label only, never an input. Use events through the selected timestamp and exclude
terminal snapshots. Join participant/team identifiers explicitly.

## Game state
Maintain elapsed_seconds and each team's cumulative total_gold, kills,
towers_destroyed, dragons_secured and barons_secured. Preserve objective identity
and availability separately for simulator validation. Counts must be nonnegative.
Display positions are scenario metadata, not initial model features.

## Initial ordered model inputs
1. elapsed_seconds
2. gold_diff
3. kill_diff
4. tower_diff
5. dragon_diff
6. baron_diff

Differences are blue minus red. Prediction returns a number in [0, 1] for blue
victory; red probability is its complement. Training and runtime must share the
same feature builder. Any additional feature requires a simulator update.

## Action configuration
Each action has an ID, eligibility rules, a success chance in [0, 1], success
effects, failure effects and elapsed time for each outcome. Update both teams.
These chances are explicit simulation assumptions, not causal API estimates.

## Decision log
Session ID, round, scenario version, before state, action ID, success chance,
random draw, outcome, after state, probabilities before/after, random seed,
model version, feature version and simulator version.

## To finalize
SQL types and constraints, remake filters, patch/queue scope, artifact format,
objective reward rules and map-coordinate convention.

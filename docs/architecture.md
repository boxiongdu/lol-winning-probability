# Architecture

## Batch pipeline
Riot discovery -> raw database storage -> normalization/validation -> timestamped
features -> chronological match splits -> training/evaluation -> model publication.
EDA branches from validated states. Starting scenarios come from held-out matches.
Airflow orchestrates this batch pipeline, not the interactive decision loop.

## Interactive runtime
Load a state -> present eligible actions -> sample success/failure -> apply outcome
and duration -> validate -> construct features -> predict -> persist -> display.
Continue for three rounds, then show a recap. After branching away from historical
events, positions and outcomes are simulated and must be labeled accordingly.

## Boundaries
- A exposes database records and task functions for ingestion/normalization.
- B owns one shared feature definition for training and inference.
- C changes underlying state, never applies a manual percentage bonus.
- D renders the state and calls the published model; no retraining per click.

## Persistence and reproducibility
Plan tables for raw responses, matches, participants, frames, events, features,
predictions, pipeline runs and game decisions. Store response hashes and fetch times.
Publish only after validation succeeds; preserve the last successful artifacts.
Version the dataset, model, feature schema, simulator rules and assets.

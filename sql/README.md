# PostgreSQL migrations

Run `python -m src.ingestion init-db` from the repository root. initialize() applies
numbered SQL files in order, records SHA256 checksums in schema_migrations and
serializes migrations with a transaction advisory lock. Already applied files are
skipped; changed checksums are rejected. Add a new numbered file for future changes.

- 001_ingestion.sql: unchanged Week 8 schema and team-frame view.
- 002_week9_storage.sql: positive champion constraint (NOT VALID for legacy rows),
  match_compositions view, game_sessions, and game_transitions.

Legacy Week 8 databases without a ledger are supported: idempotent 001 runs once
before 002. No data reset is required. Back up before upgrading. Use a dedicated
project database/schema. See docs/member_a_week9.md and docs/data_dictionary.md.
- 003_sampling.sql: frozen Challenger sampling_runs and sampling_matches membership.

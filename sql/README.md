# PostgreSQL schema

001_ingestion.sql creates the Week 8 schema and the nonterminal team-frame view.
Run `python -m src.ingestion init-db` from the repository root. Reapplying is safe.
Use a dedicated database, not an existing database containing unrelated tables.
Future schema changes should be new ordered migrations, not edits to a deployed
CREATE TABLE IF NOT EXISTS definition. See docs/data_dictionary.md.

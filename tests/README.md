# Tests

Install requirements-dev.txt, then run `python -m pytest -q` at the repository root.
Unit tests cover HTTP retry/pagination and payload validation. PostgreSQL tests cover
idempotency, resume, offline rebuild, rollback, quarantine and event preservation.
Set TEST_DATABASE_URL for database integration tests; otherwise they skip explicitly.
Each database test creates/drops a unique schema. GitHub Actions supplies PostgreSQL.
No Riot credentials or live API requests are used in these tests.

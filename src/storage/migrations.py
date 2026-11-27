"""Ordered, checksummed PostgreSQL migrations, including legacy Week 8 databases."""
import hashlib
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "sql"


class MigrationError(RuntimeError):
    """An already applied migration differs from its recorded contents."""


def migrate(conn, directory=MIGRATIONS):
    """Apply pending numbered SQL files atomically, without resetting existing data."""
    with conn.transaction():
        # Serialize startup migrations within the active schema, including test schemas.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(current_schema()), hashtext('rift-schema'))")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        for path in sorted(Path(directory).glob("[0-9][0-9][0-9]_*.sql")):
            source = path.read_bytes()
            digest = hashlib.sha256(source).hexdigest()
            row = conn.execute("SELECT sha256 FROM schema_migrations WHERE filename=%s", (path.name,)).fetchone()
            if row:
                if row["sha256"] != digest:
                    raise MigrationError(f"Applied migration changed: {path.name}; add a new migration instead")
                continue
            conn.execute(source.decode("utf-8"))
            conn.execute("INSERT INTO schema_migrations(filename,sha256) VALUES (%s,%s)", (path.name, digest))

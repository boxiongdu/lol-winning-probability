"""Synthetic inputs plus isolated PostgreSQL schemas for integration tests."""
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from psycopg import sql
from src.storage.database import Database


@pytest.fixture
def payloads():
    root = Path(__file__).resolve().parents[1] / "data" / "sample"
    return tuple(json.loads((root / f"DEMO_1.{endpoint}.json").read_text())
                 for endpoint in ("match", "timeline"))


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    with Database(url) as database:
        schema = "test_" + uuid4().hex
        database.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        database.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        try:
            database.initialize()
            yield database
        finally:
            database.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

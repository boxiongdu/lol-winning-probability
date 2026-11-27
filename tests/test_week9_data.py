"""Champion validation, coverage, patch filtering and non-destructive migrations."""
from copy import deepcopy
from pathlib import Path
import pytest
from src.ingestion.audit import champion_audit
from src.ingestion.normalize import normalize, ValidationError
from src.ingestion.pipeline import process
from src.storage.migrations import migrate, MigrationError


def put_match(db, payloads, match_id="NA1_123", patch="16.22.1"):
    details, timeline = deepcopy(payloads)
    for p in (details, timeline): p["metadata"]["matchId"] = match_id
    details["info"]["gameVersion"] = patch
    db.enqueue("americas", match_id)
    db.save_raw("americas", match_id, "match", details)
    db.save_raw("americas", match_id, "timeline", timeline)


def test_duplicate_or_missing_champion_rejected(payloads):
    details, timeline = payloads
    details["info"]["participants"][1]["championId"] = details["info"]["participants"][0]["championId"]
    with pytest.raises(ValidationError, match="unique champion"):
        normalize(details, timeline, "DEMO_1", "americas")
    details["info"]["participants"][1].pop("championId")
    with pytest.raises(ValidationError): normalize(details, timeline, "DEMO_1", "americas")


def test_audit_counts_compositions_patch_and_fixture_exclusion(db, payloads):
    put_match(db, payloads)
    put_match(db, payloads, "NA1_124", "16.23.1")
    put_match(db, payloads, "DEMO_1")
    assert process(db, "americas")["accepted"] == 3
    report = champion_audit(db, patch="16.22")
    assert report["scoped_matches"] == report["eligible_matches"] == 1
    assert report["observed_champions"] == 10
    assert report["patch_counts"] == {"16.22.1": 1}
    assert report["champion_appearances"][0] == dict(champion_id=1, matches=1, blue=1, red=0)
    assert len(report["warnings"]) == 2
    assert champion_audit(db, start_time=0, end_time=1)["eligible_matches"] == 0
    row = db.conn.execute("SELECT * FROM match_compositions WHERE match_id='NA1_123'").fetchone()
    assert row["blue_champion_ids"] == [1,2,3,4,5] and row["composition_valid"] is True


def test_audit_detects_existing_invalid_composition_without_mutation(db, payloads):
    put_match(db, payloads)
    process(db, "americas")
    db.conn.execute("UPDATE participants SET champion_id=1 WHERE participant_id=6")
    report = champion_audit(db)
    assert report["eligible_matches"] == 0 and report["invalid_matches"][0]["match_id"] == "NA1_123"
    assert db.counts("americas")["matches"] == 1


def test_patch_scope_rebuild_reuses_raw(db, payloads):
    put_match(db, payloads)
    assert process(db, "americas", patch="16.23")["excluded"] == 1
    assert process(db, "americas", patch="16.22")["accepted"] == 1
    assert db.counts("americas")["raw_responses"] == 2


def test_migrations_are_repeatable_and_preserve_data(db, payloads):
    put_match(db, payloads)
    process(db, "americas")
    before = db.counts("americas")
    db.initialize()
    assert db.counts("americas") == before
    assert db.conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 2


def test_modified_migration_rejected_atomically(db, tmp_path):
    source = Path(__file__).resolve().parents[1] / "sql"
    for path in source.glob("[0-9]*.sql"):
        (tmp_path/path.name).write_bytes(path.read_bytes())
    (tmp_path/"001_ingestion.sql").write_text("-- changed\n")
    with pytest.raises(MigrationError): migrate(db.conn, tmp_path)
    assert db.conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 2


def test_upgrade_week8_schema_preserves_raw_and_normalized(db, payloads):
    # Build a second schema using exactly the legacy 001 script (no migration ledger).
    from uuid import uuid4
    from psycopg import sql
    schema = "legacy_" + uuid4().hex
    original = db.conn.execute("SELECT current_schema() AS name").fetchone()["name"]
    db.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        db.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        path = Path(__file__).resolve().parents[1] / "sql" / "001_ingestion.sql"
        db.conn.execute(path.read_text())
        put_match(db, payloads)
        process(db, "americas")
        before = db.counts("americas")
        db.initialize()
        assert db.counts("americas") == before
        assert db.conn.execute("SELECT count(*) AS n FROM game_sessions").fetchone()["n"] == 0
    finally:
        db.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(original)))
        db.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

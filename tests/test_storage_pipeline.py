from pathlib import Path
from unittest.mock import Mock
import pytest
import psycopg
from src.ingestion.client import RiotError
from src.ingestion.normalize import normalize
from src.ingestion.pipeline import discover, load_fixture, process


def seed_raw(db, payloads):
    db.enqueue("americas", "DEMO_1")
    for endpoint, payload in zip(("match", "timeline"), payloads):
        db.save_raw("americas", "DEMO_1", endpoint, payload)


def test_duplicate_runs_and_offline_rebuild(db, payloads):
    seed_raw(db, payloads)
    assert process(db, "americas")["accepted"] == 1
    original = db.counts("americas")
    seed_raw(db, payloads)
    assert process(db, "americas")["accepted"] == 1
    assert db.counts("americas") == original
    db.conn.execute("DELETE FROM matches")
    assert process(db, "americas")["accepted"] == 1
    assert db.counts("americas") == original
    assert original["participant_frames"] == 210
    assert db.conn.execute("SELECT count(*) AS n FROM team_frame_states").fetchone()["n"] == 40


def test_resume_uses_detail_saved_before_failed_timeline(db, payloads):
    db.enqueue("americas", "DEMO_1")
    c = Mock()
    c.match.return_value = payloads[0]
    c.timeline.side_effect = RiotError("auth", 403)
    with pytest.raises(RiotError):
        process(db, "americas", c)
    assert db.get_raw("americas", "DEMO_1", "match") == payloads[0]
    assert db.counts("americas")["matches"] == 0
    c.timeline.side_effect = None
    c.timeline.return_value = payloads[1]
    assert process(db, "americas", c)["accepted"] == 1
    assert c.match.call_count == 1


def test_normalized_replacement_is_atomic(db, payloads):
    seed_raw(db, payloads)
    process(db, "americas")
    normalized = normalize(*payloads, "DEMO_1", "americas")
    bad_frames = normalized[2] + [normalized[2][0]]
    with pytest.raises(psycopg.IntegrityError):
        db.save_normalized((normalized[0], normalized[1], bad_frames, normalized[3]))
    assert db.counts("americas")["participant_frames"] == 210


def test_invalid_payload_quarantined(db, payloads):
    payloads[1]["info"]["frames"][0]["participantFrames"].pop("1")
    seed_raw(db, payloads)
    result = process(db, "americas")
    assert result["failed"] == 1
    assert db.counts("americas")["matches"] == 0
    assert db.counts("americas")["raw_responses"] == 2
    assert db.counts("americas")["statuses"] == {"failed": 1}


def test_policy_exclusion(db, payloads):
    seed_raw(db, payloads)
    assert process(db, "americas", queue=440)["excluded"] == 1
    assert db.counts("americas")["matches"] == 0
    assert process(db, "americas", queue=420)["accepted"] == 1


def test_discovery_overlapping_players(db):
    c = Mock(region="americas")
    c.match_ids.return_value = ["DEMO_1", "DEMO_2"]
    assert discover(db, c, [{"puuid": "a"}, {"puuid": "b"}]) == 2
    assert db.counts("americas")["collection_matches"] == 2


def test_fixture_import_and_missing_raw(db, payloads):
    root = Path(__file__).resolve().parents[1] / "data" / "sample"
    assert load_fixture(db, "americas", root) == 1
    assert process(db, "americas")["accepted"] == 1
    db.enqueue("americas", "MISSING")
    assert process(db, "americas")["incomplete"] == 1


def test_unknown_event_fields_and_multiple_same_timestamp_events(db, payloads):
    events = payloads[1]["info"]["frames"][10]["events"]
    events[0]["futureField"] = {"example": 1}
    events[1]["timestamp"] = events[0]["timestamp"]
    seed_raw(db, payloads)
    process(db, "americas")
    row = db.conn.execute("SELECT payload FROM events WHERE event_type='CHAMPION_KILL'").fetchone()
    assert row["payload"]["futureField"] == {"example": 1}
    assert db.counts("americas")["events"] == 4

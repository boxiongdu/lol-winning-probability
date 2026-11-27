"""Champion coverage and historical quality reports. No model features or fitting."""
import json
from uuid import UUID
from src.errors import InputError
from datetime import datetime, timezone


def champion_audit(db, *, region="americas", queue=420, map_id=11, min_duration=300,
                   patch=None, start_time=None, end_time=None, sample_id=None):
    """Return a consistent read-only snapshot; exclude synthetic DEMO_1 explicitly.

    Champion frequencies describe observed eligible matches, not an exhaustive
    champion catalog. Collection statuses cover the whole regional queue because
    rejected payloads need not have normalized metadata to filter by patch/date.
    """
    conditions = ["m.region=%s", "m.queue_id=%s", "m.map_id=%s", "m.duration_seconds >= %s",
                  "NOT m.early_surrender", "m.match_id <> 'DEMO_1'"]
    params = [region, queue, map_id, min_duration]
    if sample_id is not None:
        try:
            sample_id = str(UUID(sample_id))
        except (ValueError, TypeError, AttributeError):
            raise InputError("sample-id must be a valid sampling_runs UUID") from None
        if db.conn.execute("SELECT 1 FROM sampling_runs WHERE sample_id=%s", (sample_id,)).fetchone() is None:
            raise InputError("Sample not found in this database")
        conditions.append("EXISTS (SELECT 1 FROM sampling_matches sm WHERE sm.sample_id=%s AND sm.match_id=m.match_id)")
        params.append(sample_id)
    if patch is not None:
        conditions.append("(m.patch=%s OR m.patch LIKE %s)")
        params += [patch, patch + ".%"]
    if start_time is not None:
        conditions.append("m.game_start_ms >= %s")
        params.append(start_time * 1000)
    if end_time is not None:
        conditions.append("m.game_start_ms < %s")
        params.append(end_time * 1000)
    where = " AND ".join(conditions)
    with db.conn.transaction():
        db.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = list(db.conn.execute(f"""SELECT c.* FROM match_compositions c
            JOIN matches m USING(match_id) WHERE {where} ORDER BY m.game_start_ms,m.match_id""", params))
        invalid = [{"match_id": r["match_id"], "reason": "expected_ten_unique_positive_champions_and_five_per_team"}
                   for r in rows if not r["composition_valid"]]
        valid = [r for r in rows if r["composition_valid"]]
        coverage, patches, queues = {}, {}, {}
        for row in valid:
            patches[row["patch"]] = patches.get(row["patch"], 0) + 1
            queues[str(row["queue_id"])] = queues.get(str(row["queue_id"]), 0) + 1
            for side in ("blue", "red"):
                for champion in row[side + "_champion_ids"]:
                    item = coverage.setdefault(champion, dict(champion_id=champion, matches=0, blue=0, red=0))
                    item["matches"] += 1  # valid draft cannot repeat a champion in one match
                    item[side] += 1
        statuses = [dict(r) for r in db.conn.execute("""SELECT status,last_error AS reason,count(*) AS matches
            FROM collection_matches WHERE region=%s GROUP BY status,last_error ORDER BY status,last_error""", (region,))]
        demo_present = db.conn.execute("SELECT count(*) AS n FROM matches WHERE region=%s AND match_id='DEMO_1'", (region,)).fetchone()["n"] > 0
    counts = sorted(coverage.values(), key=lambda r: r["champion_id"])
    warnings = []
    if len(valid) < 100:
        warnings.append("Fewer than 100 eligible real matches; coverage may be sparse")
    if invalid:
        warnings.append("Invalid legacy compositions excluded from eligible counts; investigate and rebuild from raw data")
    if demo_present:
        warnings.append("Synthetic DEMO_1 exists but is excluded from this report")
    return dict(report_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
                scope=dict(region=region, queue=queue, map_id=map_id, min_duration=min_duration,
                           patch=patch, start_time=start_time, end_time=end_time, sample_id=sample_id, excluded_match_ids=["DEMO_1"]),
                scoped_matches=len(rows), eligible_matches=len(valid), invalid_matches=invalid,
                patch_counts=patches, queue_counts=queues,
                game_start_ms=dict(first=valid[0]["game_start_ms"] if valid else None,
                                   last=valid[-1]["game_start_ms"] if valid else None),
                observed_champions=len(counts), champion_appearances=counts,
                champions_with_fewer_than_5_matches=[r["champion_id"] for r in counts if r["matches"] < 5],
                regional_collection_statuses=statuses, warnings=warnings)


def write_report(db, **scope):
    """CLI-friendly formatter; callers choose a private local output path."""
    return json.dumps(champion_audit(db, **scope), indent=2)

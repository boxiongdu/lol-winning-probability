"""Callable ingestion tasks suitable for Airflow wrappers and the command line."""
import json
from src.errors import InputError
from .client import RiotError
from .normalize import ValidationError, normalize, exclusion


def read_seeds(path):
    """Read local seed JSON with either a PUUID or game_name/tag_line per player."""
    try:
        seeds = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"Seed JSON is invalid at line {exc.lineno}, column {exc.colno}. Use an array of player objects.") from None
    except UnicodeDecodeError:
        raise InputError("Seed file must be UTF-8 JSON.") from None
    if not isinstance(seeds, list) or not seeds:
        raise InputError("Seed file must contain a nonempty JSON array of player objects (wrap the objects in square brackets).")
    for seed in seeds:
        if not isinstance(seed, dict) or not (
            isinstance(seed.get("puuid"), str) and seed["puuid"].strip()
            or all(isinstance(seed.get(k), str) and seed[k].strip() for k in ("game_name", "tag_line"))
        ):
            raise InputError("Each seed needs a nonempty puuid, or both game_name and tag_line.")
    return seeds


def discover(db, client, seeds, queue=420, per_player=200, start_time=None, end_time=None):
    """Persist IDs as pages arrive; overlap across seed players is deduplicated in SQL."""
    seen = set()
    for seed in seeds:
        puuid = seed.get("puuid") or client.resolve(seed["game_name"], seed["tag_line"])
        for match_id in client.match_ids(puuid, queue, per_player, start_time, end_time):
            db.enqueue(client.region, match_id)
            seen.add(match_id)
    return len(seen)


def process(db, region, client=None, target=None, queue=420, map_id=11, min_duration=300,
            start_time=None, end_time=None):
    """Load cached responses or fetch missing ones, then validate and normalize.

    With no client this rebuilds all available raw pairs without any network calls.
    Target counts accepted matches visited in this queue, including cached matches.
    Run only one collector per database/region to avoid redundant API requests.
    """
    metrics = dict(accepted=0, excluded=0, failed=0, incomplete=0, fetched=0, cached=0)
    for match_id in db.candidates(region):
        if target is not None and metrics["accepted"] >= target:
            break
        try:
            payloads = []
            for endpoint in ("match", "timeline"):
                payload = db.get_raw(region, match_id, endpoint)
                if payload is None and client is not None:
                    payload = getattr(client, endpoint)(match_id)
                    db.save_raw(region, match_id, endpoint, payload)
                    metrics["fetched"] += 1
                elif payload is not None:
                    metrics["cached"] += 1
                payloads.append(payload)
            if any(p is None for p in payloads):
                metrics["incomplete"] += 1
                db.mark(region, match_id, "pending", "missing_raw_response")
                continue
            normalized = normalize(*payloads, match_id, region)
            reason = exclusion(normalized[0], queue, map_id, min_duration)
            start_seconds = normalized[0]["game_start_ms"] / 1000
            if start_time is not None and start_seconds < start_time:
                reason = "outside_time_window"
            if end_time is not None and start_seconds >= end_time:
                reason = "outside_time_window"
            if reason:
                db.remove_normalized(match_id)
                db.mark(region, match_id, "excluded", reason)
                metrics["excluded"] += 1
                continue
            db.save_normalized(normalized)
            metrics["accepted"] += 1
        except RiotError as exc:
            db.mark(region, match_id, "failed", str(exc))
            if exc.status in (400, 401, 403, 429) or (exc.status and exc.status >= 500) or exc.status is None:
                raise  # Stop futile requests; cached earlier responses survive.
            metrics["failed"] += 1
        except ValidationError as exc:
            db.remove_normalized(match_id)
            db.mark(region, match_id, "failed", str(exc))
            metrics["failed"] += 1
    return metrics


def load_fixture(db, region, directory):
    """Import local *.match.json / *.timeline.json pairs, preserving raw provenance."""
    count = 0
    for path in sorted(directory.glob("*.match.json")):
        match_id = path.name.removesuffix(".match.json")
        timeline_path = directory / f"{match_id}.timeline.json"
        if not timeline_path.is_file():
            raise InputError("A fixture match is missing its corresponding .timeline.json file.")
        db.enqueue(region, match_id)
        for endpoint, source in (("match", path), ("timeline", timeline_path)):
            db.save_raw(region, match_id, endpoint, json.loads(source.read_text()))
        count += 1
    if not count:
        raise InputError("No fixture pairs found. Check --directory for .match.json and .timeline.json files.")
    return count

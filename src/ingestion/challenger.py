"""Reproducible per-player Challenger sampling. No model or game logic."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from src.errors import InputError
from .client import PLATFORM_REGIONS, RiotError
from .normalize import ValidationError, exclusion, integer
from .pipeline import process


QUEUE = "RANKED_SOLO_5x5"


def stable_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(stable_json(value).encode()).hexdigest()


def save_manifest(path, manifest):
    """Atomic private checkpoints; never store API headers, keys or DSNs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as fh:
            temporary = Path(fh.name)  # NamedTemporaryFile uses owner-only permissions.
            json.dump(manifest, fh, sort_keys=True, indent=2, allow_nan=False)
            fh.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def roster_players(roster):
    """Require current PUUID-bearing entries; do not silently omit unresolved players."""
    if not isinstance(roster, dict) or roster.get("tier") != "CHALLENGER" or roster.get("queue") != QUEUE:
        raise InputError("Expected a Challenger ranked-solo roster response")
    entries = roster.get("entries")
    if not isinstance(entries, list) or not entries:
        raise InputError("Challenger roster is empty or invalid")
    if any(not isinstance(e, dict) or not isinstance(e.get("puuid"), str) or not e["puuid"].strip() for e in entries):
        raise InputError("Challenger roster contains entries without PUUIDs; inspect current League-V4 response schema")
    puuids = [e["puuid"] for e in entries]
    if len(set(puuids)) != len(puuids):
        raise InputError("Challenger roster contains duplicate PUUIDs")
    return sorted(puuids)


def shuffled_pool(ids, seed, puuid):
    """Stable per-player stream independent of roster/API ordering or earlier players."""
    values = sorted(set(ids))
    stream_seed = int(hashlib.sha256(f"{seed}:{puuid}".encode()).hexdigest(), 16)
    random.Random(stream_seed).shuffle(values)
    return values


def detail_reason(payload, match_id, puuid, config):
    """Screen eligibility before sampling; full timeline validation happens afterward."""
    if not match_id.startswith(config["platform"].upper() + "_"):
        return "platform_mismatch"
    try:
        if payload["metadata"]["matchId"] != match_id:
            return "match_id_mismatch"
        info = payload["info"]
        players = info["participants"]
        if sorted(p["participantId"] for p in players) != list(range(1, 11)):
            return "invalid_participants"
        if sum(p["teamId"] == 100 for p in players) != 5 or sum(p["teamId"] == 200 for p in players) != 5:
            return "invalid_teams"
        if len({integer(p["championId"], "championId", 1) for p in players}) != 10:
            return "invalid_champions"
        player_ids = {p["puuid"] for p in players}
        if len(player_ids) != 10 or set(payload["metadata"]["participants"]) != player_ids:
            return "invalid_participants"
        if puuid not in player_ids:
            return "seed_not_in_match"
        if any(type(p.get("gameEndedInEarlySurrender", False)) is not bool for p in players):
            return "invalid_details"
        winners = {t["teamId"]: t["win"] for t in info["teams"]}
        if set(winners) != {100, 200} or any(type(v) is not bool for v in winners.values()) or sum(winners.values()) != 1:
            return "invalid_details"
        patch = info["gameVersion"]
        if not isinstance(patch, str) or not patch:
            return "invalid_details"
        record = dict(queue_id=integer(info["queueId"], "queueId"),
                      map_id=integer(info["mapId"], "mapId"), patch=patch,
                      duration_seconds=integer(info["gameDuration"], "gameDuration", 1),
                      early_surrender=any(p.get("gameEndedInEarlySurrender", False) for p in players))
        reason = exclusion(record, 420, 11, config["min_duration"], config["patch"])
        start = integer(info["gameStartTimestamp"], "gameStartTimestamp", 1) / 1000
        if not config["start_time"] <= start < config["end_time"]:
            return "outside_time_window"
        return reason
    except (KeyError, TypeError, AttributeError, ValidationError):
        return "invalid_details"


def selection_payload(manifest):
    """The frozen selection identity excludes mutable download status/metrics."""
    return {k: manifest[k] for k in ("config", "roster_snapshot", "players", "selected_match_ids")}


def _load_manifest(path, config):
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        UUID(manifest["sample_id"])
        if manifest["version"] != 1 or manifest["config"] != config:
            raise InputError("Manifest configuration differs; use identical arguments to resume or choose a new --manifest path")
        puuids = roster_players(manifest["roster_snapshot"])
        players = manifest["players"]
        if not isinstance(players, dict) or any(p not in puuids for p in players):
            raise ValueError
        for puuid, player in players.items():
            ids = player["candidate_ids"]
            selected = player["selected_match_ids"]
            if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
                raise ValueError
            if len(set(ids)) != len(ids) or ids != shuffled_pool(ids, config["sampling_seed"], puuid):
                raise ValueError
            if type(player["cursor"]) is not int or not 0 <= player["cursor"] <= len(ids):
                raise ValueError
            if len(selected) > config["matches_per_player"] or len(set(selected)) != len(selected):
                raise ValueError
            if not set(selected) <= set(ids[:player["cursor"]]):
                raise ValueError
            if not isinstance(player["rejections"], dict):
                raise ValueError
        if manifest["phase"] != "sampling":
            if len(players) != len(puuids):
                raise ValueError
            expected = sorted({m for p in players.values() for m in p["selected_match_ids"]})
            if manifest["selected_match_ids"] != expected or manifest["selection_sha256"] != fingerprint(selection_payload(manifest)):
                raise ValueError
        return manifest
    except InputError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise InputError("Invalid sampling manifest; restore its saved checkpoint rather than editing selections") from None


def build_selection(db, client, path, config, progress=None):
    """Select first k eligible matches in each independently shuffled full history.

    This gives a uniform subset of detail-eligible matches within the retrieved
    fixed window, without downloading every candidate's details. Checkpoint after
    each inspected candidate; an API outage stops rather than silently resampling.
    """
    path = Path(path)
    if path.exists():
        manifest = _load_manifest(path, config)
    else:
        roster = client.challenger_roster(config["platform"])
        roster_players(roster)
        manifest = dict(version=1, sample_id=str(uuid4()),
                        created_at=datetime.now(timezone.utc).isoformat(), config=config,
                        roster_snapshot=roster, players={}, phase="sampling")
        save_manifest(path, manifest)
    if manifest["phase"] != "sampling":
        return manifest
    roster = roster_players(manifest["roster_snapshot"])
    for player_number, puuid in enumerate(roster, 1):
        player = manifest["players"].get(puuid)
        if player is None:
            pool = client.history_pool(puuid, start_time=config["start_time"],
                                       end_time=config["end_time"], limit=config["history_limit"])
            player = dict(candidate_ids=shuffled_pool(pool, config["sampling_seed"], puuid),
                          cursor=0, selected_match_ids=[], rejections={})
            manifest["players"][puuid] = player
            save_manifest(path, manifest)
        while player["cursor"] < len(player["candidate_ids"]) and len(player["selected_match_ids"]) < config["matches_per_player"]:
            match_id = player["candidate_ids"][player["cursor"]]
            reason = None
            if not match_id.startswith(config["platform"].upper() + "_"):
                reason = "platform_mismatch"
            else:
                payload = db.get_raw(config["region"], match_id, "match")
                if payload is None:
                    try:
                        payload = client.match(match_id)
                    except RiotError as exc:
                        if exc.status != 404:
                            raise
                        reason = "details_unavailable_404"
                    else:
                        db.save_raw(config["region"], match_id, "match", payload)
                if reason is None:
                    reason = detail_reason(payload, match_id, puuid, config)
            if reason is None:
                player["selected_match_ids"].append(match_id)
            else:
                player["rejections"][match_id] = reason
            player["cursor"] += 1
            save_manifest(path, manifest)
        if progress:
            progress(f"Sampling player {player_number}/{len(roster)}: {len(player['selected_match_ids'])}/{config['matches_per_player']} selected")
    manifest["selected_match_ids"] = sorted({m for p in manifest["players"].values() for m in p["selected_match_ids"]})
    manifest["selection_sha256"] = fingerprint(selection_payload(manifest))
    manifest["phase"] = "selected"
    save_manifest(path, manifest)
    return manifest


@contextmanager
def sample_lock(db, path):
    """Session advisory lock releases automatically if a collector crashes/disconnects."""
    key = str(Path(path).resolve())
    locked = db.conn.execute("SELECT pg_try_advisory_lock(hashtext('rift-sample'),hashtext(%s)) AS ok", (key,)).fetchone()["ok"]
    if not locked:
        raise InputError("Another collector is using this manifest; wait for it to finish")
    try:
        yield
    finally:
        db.conn.execute("SELECT pg_advisory_unlock(hashtext('rift-sample'),hashtext(%s))", (key,))


def register_selection(db, manifest):
    """Pin membership before downloads; never confuse it with older queued matches."""
    sid = UUID(manifest["sample_id"])
    sources = Counter(m for p in manifest["players"].values() for m in p["selected_match_ids"])
    with db.conn.transaction():
        db.conn.execute("""INSERT INTO sampling_runs(sample_id,selection_sha256,manifest,status)
            VALUES (%s,%s,%s,'selected') ON CONFLICT DO NOTHING""",
            (sid, manifest["selection_sha256"], Jsonb(manifest)))
        row = db.conn.execute("SELECT selection_sha256 FROM sampling_runs WHERE sample_id=%s FOR UPDATE", (sid,)).fetchone()
        if row["selection_sha256"] != manifest["selection_sha256"]:
            raise InputError("Database sample differs from manifest; do not replace a frozen selection")
        for match_id, count in sources.items():
            db.conn.execute("""INSERT INTO sampling_matches(sample_id,match_id,source_players)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""", (sid, match_id, count))
            db.enqueue(manifest["config"]["region"], match_id)


def collect_challenger(db, client, *, manifest_path, platform="na1", start_time, end_time,
                       matches_per_player=10, sampling_seed=42, history_limit=1000,
                       patch=None, min_duration=300, progress=None):
    """Return public-safe summary; private identities stay in manifest/database."""
    if platform not in PLATFORM_REGIONS or client.region != PLATFORM_REGIONS[platform]:
        raise InputError("Platform and Match-V5 region disagree")
    if start_time < 0 or end_time <= start_time or matches_per_player < 1 or history_limit < matches_per_player or sampling_seed < 0:
        raise InputError("Invalid date window, match count, history limit or sampling seed")
    config = dict(platform=platform, region=client.region, queue=420, map_id=11,
                  start_time=start_time, end_time=end_time, matches_per_player=matches_per_player,
                  sampling_seed=sampling_seed, history_limit=history_limit, patch=patch, min_duration=min_duration)
    with sample_lock(db, manifest_path):
        manifest = build_selection(db, client, manifest_path, config, progress)
        register_selection(db, manifest)
        if progress:
            progress(f"Downloading/validating {len(manifest['selected_match_ids'])} frozen unique matches; cached responses are reused")
        sid = UUID(manifest["sample_id"])
        run_id = db.start_run("collect-challenger", client.region)
        metrics = {"scope": config, "sample_id": str(sid)}
        try:
            metrics.update(process(db, client.region, client, queue=420, map_id=11,
                                   min_duration=min_duration, start_time=start_time, end_time=end_time,
                                   patch=patch, match_ids=manifest["selected_match_ids"]))
            players = manifest["players"]
            selections = sum(len(p["selected_match_ids"]) for p in players.values())
            shortfalls = sum(len(p["selected_match_ids"]) < matches_per_player for p in players.values())
            status = "partial" if shortfalls or metrics["accepted"] < len(manifest["selected_match_ids"]) else "complete"
            metrics.update(roster_players=len(players), player_match_selections=selections,
                           unique_selected_matches=len(manifest["selected_match_ids"]),
                           overlap_removed=selections-len(manifest["selected_match_ids"]),
                           players_with_shortfalls=shortfalls,
                           detail_rejections=dict(Counter(reason for player in players.values() for reason in player["rejections"].values())),
                           api_requests=client.requests, retries=client.retry_count)
            manifest["phase"] = status
            manifest["download_metrics"] = metrics
            db.conn.execute("""UPDATE sampling_runs SET status=%s,metrics=%s,manifest=%s,updated_at=now()
                WHERE sample_id=%s""", (status, Jsonb(metrics), Jsonb(manifest), sid))
            db.finish_run(run_id, "success" if status == "complete" else "partial", metrics)
            save_manifest(manifest_path, manifest)
            return {"sample_id": str(sid), "selection_sha256": manifest["selection_sha256"], "status": status, "metrics": metrics}
        except Exception as exc:
            db.conn.execute("UPDATE sampling_runs SET status='failed',updated_at=now() WHERE sample_id=%s", (sid,))
            db.finish_run(run_id, "failed", metrics, type(exc).__name__)
            raise

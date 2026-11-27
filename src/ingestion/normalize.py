"""Validate Riot payloads and normalize snapshots without inventing missing positions."""


class ValidationError(ValueError):
    """A payload violates the agreed structural contract."""


def integer(value, field, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"Invalid integer field: {field}")
    return value


def position(value):
    """Missing/incomplete coordinates remain null rather than becoming (0, 0)."""
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise ValidationError("Invalid position")
    coords = [value.get(k) for k in ("x", "y")]
    if any(v is not None and (isinstance(v, bool) or not isinstance(v, int)) for v in coords):
        raise ValidationError("Invalid position coordinate")
    return tuple(coords)


def normalize(match, timeline, expected_id, region):
    """Return validated records; unknown event fields remain in the event payload."""
    try:
        for payload in (match, timeline):
            if payload["metadata"]["matchId"] != expected_id:
                raise ValidationError("Payload match ID mismatch")
        info, ti = match["info"], timeline["info"]
        players = info["participants"]
        ids = [integer(p["participantId"], "participantId", 1) for p in players]
        if sorted(ids) != list(range(1, 11)):
            raise ValidationError("Expected ten unique participant IDs")
        teams = {p["participantId"]: p["teamId"] for p in players}
        if list(teams.values()).count(100) != 5 or list(teams.values()).count(200) != 5:
            raise ValidationError("Expected five players per team")
        puuids = {p["puuid"] for p in players}
        if len(puuids) != 10:
            raise ValidationError("Expected ten unique PUUIDs")
        for payload in (match, timeline):
            if set(payload["metadata"]["participants"]) != puuids:
                raise ValidationError("Participant metadata mismatch")
        timeline_players = {p["participantId"]: p["puuid"] for p in ti["participants"]}
        if timeline_players != {p["participantId"]: p["puuid"] for p in players}:
            raise ValidationError("Timeline participant mapping mismatch")
        winners = {t["teamId"]: t["win"] for t in info["teams"]}
        if set(winners) != {100, 200} or any(type(v) is not bool for v in winners.values()) or sum(winners.values()) != 1:
            raise ValidationError("Expected exactly one winning team")
        if any(type(p.get("gameEndedInEarlySurrender", False)) is not bool for p in players):
            raise ValidationError("Invalid early surrender flag")
        record = {
            "match_id": expected_id, "region": region,
            "game_start_ms": integer(info["gameStartTimestamp"], "gameStartTimestamp", 1),
            "duration_seconds": integer(info["gameDuration"], "gameDuration", 1),
            "patch": info["gameVersion"],
            "queue_id": integer(info["queueId"], "queueId"),
            "map_id": integer(info["mapId"], "mapId"),
            "blue_win": winners[100],
            "early_surrender": any(p.get("gameEndedInEarlySurrender", False) for p in players),
            "frame_interval_ms": integer(ti["frameInterval"], "frameInterval", 1),
        }
        if not isinstance(record["patch"], str) or not record["patch"]:
            raise ValidationError("Missing game version")
        participants = [(expected_id, p["participantId"], p["teamId"],
                         integer(p["championId"], "championId", 1)) for p in players]
        if len({p[3] for p in participants}) != 10:
            raise ValidationError("Expected ten unique champion picks for ranked Summoner's Rift")
        frames, events, last = [], [], -1
        if len(ti["frames"]) < 2:
            raise ValidationError("Expected at least two timeline frames")
        for fi, frame in enumerate(ti["frames"]):
            ts = integer(frame["timestamp"], "frame timestamp")
            if ts <= last:
                raise ValidationError("Frame timestamps must strictly increase")
            last = ts
            pf = frame["participantFrames"]
            if set(pf) != {str(i) for i in ids}:
                raise ValidationError("Incomplete participant frame")
            for key, values in pf.items():
                pid = integer(values["participantId"], "frame participantId", 1)
                if str(pid) != key:
                    raise ValidationError("Frame participant ID mismatch")
                x, y = position(values.get("position"))
                # Riot may omit zero-valued optional numerical fields.
                values_int = [integer(values.get(k, 0), k) for k in
                              ("totalGold", "xp", "level", "minionsKilled", "jungleMinionsKilled")]
                frames.append((expected_id, ts, pid, x, y, *values_int))
            for ei, event in enumerate(frame.get("events", [])):
                et = integer(event["timestamp"], "event timestamp")
                kind = event["type"]
                if not isinstance(kind, str) or not kind:
                    raise ValidationError("Missing event type")
                actor = event.get("killerId", event.get("participantId"))
                target = event.get("victimId")
                for value in (actor, target):
                    if value is not None and (type(value) is not int or value not in range(11)):
                        raise ValidationError("Invalid event participant")
                credited = None
                if kind in ("CHAMPION_KILL", "ELITE_MONSTER_KILL", "BUILDING_KILL"):
                    credited = teams.get(actor)
                    if kind == "ELITE_MONSTER_KILL" and event.get("killerTeamId") in (100, 200):
                        credited = event["killerTeamId"]
                    # teamId is the destroyed building's owner, not the killer's team.
                    if kind == "BUILDING_KILL" and event.get("teamId") in (100, 200):
                        credited = 300 - event["teamId"]
                x, y = position(event.get("position"))
                events.append((expected_id, fi, ei, et, kind, actor or None,
                               target or None, credited, x, y, event))
        return record, participants, frames, events
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValidationError(f"Missing or invalid payload structure ({type(exc).__name__})") from None


def exclusion(record, queue=420, map_id=11, min_duration=300, patch=None):
    """Return a documented policy reason, distinct from malformed data."""
    if patch is not None and not (record["patch"] == patch or record["patch"].startswith(patch + ".")):
        return "patch_mismatch"
    if record["queue_id"] != queue:
        return "queue_mismatch"
    if record["map_id"] != map_id:
        return "map_mismatch"
    if record["early_surrender"] or record["duration_seconds"] < min_duration:
        return "remake_or_short_game"
    return None

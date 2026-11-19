# Ingestion data dictionary — schema v1

| Relation | Grain / key | Main columns |
| --- | --- | --- |
| raw_responses | region, match_id, endpoint | JSONB payload, canonical payload_sha256, fetched_at |
| collection_matches | region, match_id | status, attempts, last_error, updated_at |
| ingestion_runs | run_id UUID | command, region, start/finish, status, metrics JSONB, error class |
| matches | match_id | region, game_start_ms, duration_seconds, patch (full gameVersion), queue_id, map_id, blue_win, early_surrender, frame_interval_ms |
| participants | match_id, participant_id | team_id, champion_id |
| participant_frames | match_id, timestamp_ms, participant_id | nullable x/y, total_gold, xp, level, minions_killed, jungle_minions_killed |
| events | match_id, frame_index, event_index | timestamp_ms, event_type, actor_id, target_id, credited_team_id, nullable x/y, original event JSONB |
| team_frame_states (view) | match_id, timestamp_ms, team_id | total_gold, xp, average_level, cs |

Team 100 is blue; 200 is red. Timeline timestamps are milliseconds since game
start; game_start_ms is Unix milliseconds; gameDuration is interpreted as seconds
for modern Match-V5. Frame indices preserve source ordering. Event identity is
its source frame/event index because multiple events can share a timestamp.
Actor/target 0 becomes null (no participant). Position missingness stays null.
Optional missing numeric frame fields become zero per Riot's empty-value convention;
missing structural fields, invalid types and negative counts fail validation.

Matches require exactly ten participant IDs 1–10, five per team, matching PUUID
mappings across details/timeline, exactly one winning team, and at least two frames
with strictly increasing timestamps. All frames require all ten participants.
Failed validation retains raw data and marks the queue item failed. Policy exclusions
(queue/map mismatch, early surrender or short duration, date bounds) are separate.

The view excludes the final recorded frame, duration boundary and GAME_END onward.
B must additionally ensure objective events are aggregated only through each row's
actual timestamp. Final winner and duration are label/filter metadata only.

No player PUUID or Riot ID is copied into normalized participant tables. Raw
responses still contain these identifiers. Store/restrict raw data accordingly.
SHA256 is computed over UTF-8 JSON with sorted keys and compact separators; it
identifies canonical payload content, not the original HTTP response bytes.
Raw cache preserves the first successful payload; no replacement occurs silently.

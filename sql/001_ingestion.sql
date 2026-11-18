-- Idempotent initial schema; runtime changes must use a new ordered migration.
CREATE TABLE IF NOT EXISTS raw_responses (
    region TEXT NOT NULL,
    match_id TEXT NOT NULL,
    endpoint TEXT NOT NULL CHECK (endpoint IN ('match', 'timeline')),
    payload JSONB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (region, match_id, endpoint)
);
CREATE TABLE IF NOT EXISTS collection_matches (
    region TEXT NOT NULL,
    match_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'complete', 'excluded', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (region, match_id)
);
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id UUID PRIMARY KEY,
    command TEXT NOT NULL,
    region TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    metrics JSONB NOT NULL DEFAULT '{}',
    error TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    game_start_ms BIGINT NOT NULL CHECK (game_start_ms > 0),
    duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
    patch TEXT NOT NULL,
    queue_id INTEGER NOT NULL,
    map_id INTEGER NOT NULL,
    blue_win BOOLEAN NOT NULL,
    early_surrender BOOLEAN NOT NULL,
    frame_interval_ms INTEGER NOT NULL CHECK (frame_interval_ms > 0),
    normalized_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS participants (
    match_id TEXT NOT NULL REFERENCES matches ON DELETE CASCADE,
    participant_id INTEGER NOT NULL CHECK (participant_id BETWEEN 1 AND 10),
    team_id INTEGER NOT NULL CHECK (team_id IN (100, 200)),
    champion_id INTEGER NOT NULL,
    PRIMARY KEY (match_id, participant_id)
);
CREATE TABLE IF NOT EXISTS participant_frames (
    match_id TEXT NOT NULL,
    timestamp_ms BIGINT NOT NULL CHECK (timestamp_ms >= 0),
    participant_id INTEGER NOT NULL,
    x INTEGER,
    y INTEGER,
    total_gold INTEGER NOT NULL CHECK (total_gold >= 0),
    xp INTEGER NOT NULL CHECK (xp >= 0),
    level INTEGER NOT NULL CHECK (level >= 0),
    minions_killed INTEGER NOT NULL CHECK (minions_killed >= 0),
    jungle_minions_killed INTEGER NOT NULL CHECK (jungle_minions_killed >= 0),
    PRIMARY KEY (match_id, timestamp_ms, participant_id),
    FOREIGN KEY (match_id, participant_id) REFERENCES participants ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS events (
    match_id TEXT NOT NULL REFERENCES matches ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    event_index INTEGER NOT NULL,
    timestamp_ms BIGINT NOT NULL CHECK (timestamp_ms >= 0),
    event_type TEXT NOT NULL,
    actor_id INTEGER,
    target_id INTEGER,
    credited_team_id INTEGER CHECK (credited_team_id IN (100, 200)),
    x INTEGER,
    y INTEGER,
    payload JSONB NOT NULL,
    PRIMARY KEY (match_id, frame_index, event_index)
);
CREATE INDEX IF NOT EXISTS events_match_time ON events (match_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS matches_start ON matches (game_start_ms);
-- Team snapshots are a handoff to B, not a completed feature/model pipeline.
-- Exclude the last timeline frame and any snapshot at/after GAME_END or duration.
CREATE OR REPLACE VIEW team_frame_states AS
SELECT f.match_id, f.timestamp_ms, p.team_id,
       sum(f.total_gold)::bigint AS total_gold,
       sum(f.xp)::bigint AS xp,
       avg(f.level) AS average_level,
       sum(f.minions_killed + f.jungle_minions_killed)::bigint AS cs
FROM participant_frames f
JOIN participants p USING (match_id, participant_id)
JOIN matches m USING (match_id)
WHERE f.timestamp_ms < m.duration_seconds::bigint * 1000
  AND f.timestamp_ms < (SELECT max(f2.timestamp_ms) FROM participant_frames f2
                       WHERE f2.match_id = f.match_id)
  AND NOT EXISTS (SELECT 1 FROM events e WHERE e.match_id = f.match_id
                  AND e.event_type = 'GAME_END' AND e.timestamp_ms <= f.timestamp_ms)
GROUP BY f.match_id, f.timestamp_ms, p.team_id;

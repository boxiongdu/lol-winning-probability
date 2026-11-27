-- New writes require positive IDs; legacy bad rows remain available to audit.
ALTER TABLE participants ADD CONSTRAINT participants_positive_champion
    CHECK (champion_id > 0) NOT VALID;

-- No model encoding here: raw team compositions for Member B's feature builder.
CREATE VIEW match_compositions AS
SELECT m.match_id, m.region, m.game_start_ms, m.patch, m.queue_id, m.map_id, m.blue_win,
       array_agg(p.champion_id ORDER BY p.participant_id)
           FILTER (WHERE p.team_id=100) AS blue_champion_ids,
       array_agg(p.champion_id ORDER BY p.participant_id)
           FILTER (WHERE p.team_id=200) AS red_champion_ids,
       (count(p.participant_id)=10 AND count(DISTINCT p.champion_id)=10
        AND count(*) FILTER (WHERE p.team_id=100)=5
        AND count(*) FILTER (WHERE p.team_id=200)=5
        AND bool_and(p.champion_id>0)) AS composition_valid
FROM matches m LEFT JOIN participants p USING(match_id)
GROUP BY m.match_id;

CREATE TABLE game_sessions (
    session_id UUID PRIMARY KEY,
    creation_hash TEXT NOT NULL,
    initial_state JSONB NOT NULL,
    current_state JSONB NOT NULL,
    versions JSONB NOT NULL,
    random_seed BIGINT NOT NULL CHECK (random_seed >= 0),
    phase TEXT NOT NULL DEFAULT 'draft' CHECK (phase IN ('draft','in_progress','finished')),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    event_count INTEGER NOT NULL DEFAULT 0 CHECK (event_count BETWEEN 0 AND 3),
    winner TEXT CHECK (winner IN ('blue','red')),
    latest_prediction JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((phase='finished') = (winner IS NOT NULL))
);
CREATE TABLE game_transitions (
    session_id UUID NOT NULL REFERENCES game_sessions(session_id),
    transition_id UUID NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    request_hash TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('start','progression','decision','finish')),
    event_index INTEGER CHECK (event_index BETWEEN 1 AND 3),
    event_id TEXT,
    action_id TEXT,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    prediction_before JSONB,
    prediction_after JSONB NOT NULL,
    resolution JSONB NOT NULL,
    phase_after TEXT NOT NULL CHECK (phase_after IN ('in_progress','finished')),
    event_count_after INTEGER NOT NULL CHECK (event_count_after BETWEEN 0 AND 3),
    winner TEXT CHECK (winner IN ('blue','red')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, transition_id),
    UNIQUE (session_id, sequence),
    CHECK ((kind='finish') = (winner IS NOT NULL)),
    CHECK ((kind='decision') = (event_index IS NOT NULL)),
    CHECK (kind <> 'decision' OR (event_id IS NOT NULL AND action_id IS NOT NULL))
);
CREATE UNIQUE INDEX game_decision_event ON game_transitions(session_id,event_index)
    WHERE kind='decision';
CREATE UNIQUE INDEX game_decision_identity ON game_transitions(session_id,event_id)
    WHERE kind='decision';

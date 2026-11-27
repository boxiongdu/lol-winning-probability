-- A frozen sample is distinct from the regional ingestion queue.
CREATE TABLE sampling_runs (
    sample_id UUID PRIMARY KEY,
    selection_sha256 TEXT NOT NULL,
    manifest JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('selected','complete','partial','failed')),
    metrics JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE sampling_matches (
    sample_id UUID NOT NULL REFERENCES sampling_runs(sample_id),
    match_id TEXT NOT NULL,
    source_players INTEGER NOT NULL CHECK (source_players > 0),
    PRIMARY KEY (sample_id,match_id)
);
-- Intentionally no FK to matches: membership survives missing/invalid timelines.
CREATE INDEX sampling_match_lookup ON sampling_matches(match_id);

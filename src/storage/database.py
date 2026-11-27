"""PostgreSQL persistence with independently committed raw response checkpoints."""
import hashlib
import json
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .migrations import migrate


class Database:
    def __init__(self, url):
        self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.conn.close()

    def initialize(self):
        """Apply pending versioned migrations, preserving existing records."""
        migrate(self.conn)

    def start_run(self, command, region):
        run_id = uuid4()
        self.conn.execute("INSERT INTO ingestion_runs(run_id, command, region) VALUES (%s,%s,%s)",
                          (run_id, command, region))
        return run_id

    def finish_run(self, run_id, status, metrics, error=None):
        self.conn.execute("""UPDATE ingestion_runs SET finished_at=now(), status=%s,
                          metrics=%s, error=%s WHERE run_id=%s""",
                          (status, Jsonb(metrics), error, run_id))

    def enqueue(self, region, match_id):
        self.conn.execute("""INSERT INTO collection_matches(region, match_id) VALUES (%s,%s)
                          ON CONFLICT DO NOTHING""", (region, match_id))

    def candidates(self, region):
        """Retry unfinished work first; still recheck cached complete records against policy."""
        return [r["match_id"] for r in self.conn.execute(
            """SELECT match_id FROM collection_matches WHERE region=%s
            ORDER BY (status='complete'), match_id""", (region,))]

    def get_raw(self, region, match_id, endpoint):
        row = self.conn.execute("SELECT payload FROM raw_responses WHERE region=%s AND match_id=%s AND endpoint=%s",
                                (region, match_id, endpoint)).fetchone()
        return row["payload"] if row else None

    def save_raw(self, region, match_id, endpoint, payload):
        """Cache one successful response before fetching the other endpoint."""
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        self.conn.execute("""INSERT INTO raw_responses(region,match_id,endpoint,payload,payload_sha256)
                          VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                          (region, match_id, endpoint, Jsonb(payload), digest))

    def mark(self, region, match_id, status, error=None):
        self.conn.execute("""UPDATE collection_matches SET status=%s, last_error=%s,
                          attempts=attempts+1, updated_at=now() WHERE region=%s AND match_id=%s""",
                          (status, error, region, match_id))

    def save_normalized(self, normalized):
        """Replace a match atomically; failure rolls back to its previous valid version."""
        match, participants, frames, events = normalized
        with self.conn.transaction():
            self.conn.execute("DELETE FROM matches WHERE match_id=%s", (match["match_id"],))
            keys = ("match_id", "region", "game_start_ms", "duration_seconds", "patch", "queue_id",
                    "map_id", "blue_win", "early_surrender", "frame_interval_ms")
            self.conn.execute("""INSERT INTO matches(match_id,region,game_start_ms,duration_seconds,
                patch,queue_id,map_id,blue_win,early_surrender,frame_interval_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", tuple(match[k] for k in keys))
            with self.conn.cursor() as cur:
                cur.executemany("INSERT INTO participants VALUES (%s,%s,%s,%s)", participants)
                cur.executemany("INSERT INTO participant_frames VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", frames)
                if events:
                    cur.executemany("INSERT INTO events VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                    [(*e[:-1], Jsonb(e[-1])) for e in events])
            self.mark(match["region"], match["match_id"], "complete")

    def remove_normalized(self, match_id):
        self.conn.execute("DELETE FROM matches WHERE match_id=%s", (match_id,))

    def counts(self, region):
        """Return dataset counts for CLI verification and future DAG task metrics."""
        counts = {}
        for table in ("raw_responses", "matches", "collection_matches"):
            counts[table] = self.conn.execute(
                f"SELECT count(*) AS n FROM {table} WHERE region=%s", (region,)).fetchone()["n"]
        for table in ("participants", "participant_frames", "events"):
            counts[table] = self.conn.execute(
                f"SELECT count(*) AS n FROM {table} t JOIN matches m USING(match_id) WHERE m.region=%s",
                (region,)).fetchone()["n"]
        counts["statuses"] = {r["status"]: r["n"] for r in self.conn.execute(
            "SELECT status,count(*) AS n FROM collection_matches WHERE region=%s GROUP BY status", (region,))}
        return counts

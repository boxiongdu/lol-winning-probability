"""Persistence contract for C/D. Stores supplied states; never simulates or predicts.

Use an autocommit Database connection. Every mutating method is one transaction;
return only after commit. All app writes must go through this API.
"""
import hashlib
import json
import math
from uuid import UUID
from psycopg.types.json import Jsonb

TEAM_FIELDS = ("total_gold", "kills", "towers_destroyed", "dragons_secured", "barons_secured")
VERSION_FIELDS = ("dataset", "opening_model", "in_game_model", "features", "scenario", "simulator", "champion_catalog")


class StorageInputError(ValueError):
    """The caller supplied invalid state or transition metadata."""


class SessionConflict(RuntimeError):
    """A stale revision, invalid phase, or conflicting idempotency key was submitted."""


class SessionNotFound(LookupError):
    """The requested session does not exist."""


def canonical(value):
    """Validate JSON and snapshot mutable caller inputs before SQL execution."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise StorageInputError("Payload must be finite JSON with string keys") from None


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def uuid_value(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise StorageInputError("Session and transition IDs must be UUIDs") from None


def integer(value, label, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise StorageInputError(f"{label} must be a nonnegative integer in range")
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise StorageInputError(f"{label} must be a nonempty string")
    return value


def number(value, label, probability=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (probability and value > 1):
        raise StorageInputError(f"{label} must be finite and in range")
    return value


def validate_state(state):
    """Validate storage shape and picks, leaving objective eligibility to C."""
    if not isinstance(state, dict):
        raise StorageInputError("State must be an object")
    number(state.get("elapsed_seconds"), "elapsed_seconds")
    picks = []
    for side in ("blue", "red"):
        ids = state.get(side + "_champion_ids")
        if not isinstance(ids, list) or len(ids) != 5 or any(type(x) is not int or x <= 0 for x in ids):
            raise StorageInputError("Each team must have five positive integer champion IDs")
        picks.extend(ids)
        stats = state.get(side)
        if not isinstance(stats, dict):
            raise StorageInputError("Team statistics must be objects")
        for field in TEAM_FIELDS:
            integer(stats.get(field), field)
    if len(set(picks)) != 10:
        raise StorageInputError("Champion IDs must be unique across both teams")
    if not isinstance(state.get("objective_availability"), dict):
        raise StorageInputError("objective_availability must be an object")
    canonical(state)


def validate_prediction(prediction, versions):
    if not isinstance(prediction, dict):
        raise StorageInputError("Prediction must be an object")
    number(prediction.get("p_blue"), "p_blue", probability=True)
    stage = prediction.get("stage")
    if stage not in ("opening", "in_game"):
        raise StorageInputError("Prediction stage must be opening or in_game")
    if prediction.get("model_version") != versions[stage + "_model"]:
        raise StorageInputError("Prediction must use the session's pinned model version")
    if not isinstance(prediction.get("coverage_warnings"), list) or any(
        not isinstance(x, str) for x in prediction["coverage_warnings"]
    ):
        raise StorageInputError("coverage_warnings must be a list of strings")


def serializable(row):
    if row is None:
        return None
    return {k: str(v) if isinstance(v, UUID) else v.isoformat() if hasattr(v, "isoformat") else v
            for k, v in row.items()}


class SessionStore:
    """An additive API; it does not change Week 8's existing JSONL app logger."""

    def __init__(self, database):
        self.conn = database.conn
        if not self.conn.autocommit:
            raise ValueError("SessionStore requires Database's autocommit connection")

    def create_session(self, session_id, *, initial_state, random_seed, versions):
        """Idempotently create the 00:00 draft snapshot after picks are validated."""
        sid = uuid_value(session_id)
        validate_state(initial_state)
        if initial_state["elapsed_seconds"] != 0:
            raise StorageInputError("Initial state must start at 00:00")
        if any(initial_state["blue"][k] != initial_state["red"][k] for k in TEAM_FIELDS):
            raise StorageInputError("Initial team statistics must be equal")
        integer(random_seed, "random_seed", 2**63 - 1)
        if not isinstance(versions, dict):
            raise StorageInputError("versions must be an object")
        for key in VERSION_FIELDS:
            text(versions.get(key), key)
        payload = json.loads(canonical(dict(initial_state=initial_state, random_seed=random_seed, versions=versions)))
        fingerprint = digest(payload)
        with self.conn.transaction():
            self.conn.execute("""INSERT INTO game_sessions
                (session_id,creation_hash,initial_state,current_state,random_seed,versions)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (sid, fingerprint, Jsonb(payload["initial_state"]), Jsonb(payload["initial_state"]),
                 random_seed, Jsonb(payload["versions"])))
            row = self.conn.execute("SELECT * FROM game_sessions WHERE session_id=%s FOR UPDATE", (sid,)).fetchone()
            if row["creation_hash"] != fingerprint:
                raise SessionConflict("Session ID already exists with different creation data")
            result = serializable(row)
        return result

    def load_session(self, session_id):
        """Return the latest committed state and immutable artifact references."""
        row = self.conn.execute("SELECT * FROM game_sessions WHERE session_id=%s", (uuid_value(session_id),)).fetchone()
        if row is None:
            raise SessionNotFound("Session not found")
        return serializable(row)

    def transitions(self, session_id):
        """Return the complete persisted transition history in replay order."""
        self.load_session(session_id)
        return [serializable(row) for row in self.conn.execute(
            "SELECT * FROM game_transitions WHERE session_id=%s ORDER BY sequence", (uuid_value(session_id),))]

    def append_transition(self, session_id, transition_id, *, expected_revision, kind,
                          after_state, prediction, event_index=None, event_id=None,
                          action_id=None, resolution=None, winner=None):
        """Atomically append a supplied transition and replace the session snapshot.

        An exact retry returns the original transition even after later transitions.
        Revision/phase/event constraints protect ordering, not strategy correctness.
        C supplies all effects, draws and probabilities; this method computes none.
        """
        sid, tid = uuid_value(session_id), uuid_value(transition_id)
        integer(expected_revision, "expected_revision")
        if kind not in ("start", "progression", "decision", "finish"):
            raise StorageInputError("Unknown transition kind")
        validate_state(after_state)
        request = json.loads(canonical(dict(expected_revision=expected_revision, kind=kind,
            after_state=after_state, prediction=prediction, event_index=event_index,
            event_id=event_id, action_id=action_id, resolution=resolution, winner=winner)))
        fingerprint = digest(request)
        with self.conn.transaction():
            session = self.conn.execute("SELECT * FROM game_sessions WHERE session_id=%s FOR UPDATE", (sid,)).fetchone()
            if session is None:
                raise SessionNotFound("Session not found")
            prior = self.conn.execute("SELECT * FROM game_transitions WHERE session_id=%s AND transition_id=%s", (sid, tid)).fetchone()
            if prior:
                if prior["request_hash"] != fingerprint:
                    raise SessionConflict("Transition ID was reused with different request data")
                result = serializable(prior)
            else:
                result = self._append(session, sid, tid, request, fingerprint)
        return result

    def _append(self, session, sid, tid, r, fingerprint):
        if session["revision"] != r["expected_revision"]:
            raise SessionConflict("Stale revision; reload the committed session")
        if session["phase"] == "finished":
            raise SessionConflict("Session is already finished")
        before, after = session["current_state"], r["after_state"]
        for key in ("blue_champion_ids", "red_champion_ids"):
            if before[key] != after[key]:
                raise SessionConflict("Champion picks cannot change within a session")
        elapsed = after["elapsed_seconds"] - before["elapsed_seconds"]
        kind, count = r["kind"], session["event_count"]
        validate_prediction(r["prediction"], session["versions"])
        if kind == "start":
            if session["phase"] != "draft" or after != before or r["prediction"]["stage"] != "opening":
                raise SessionConflict("Start must preserve the 00:00 draft state and use the opening model")
        elif session["phase"] != "in_progress" or elapsed <= 0:
            raise SessionConflict("Transitions require an active session and advancing time")
        if kind in ("progression", "decision") and r["prediction"]["stage"] != "in_game":
            raise StorageInputError("Non-opening transitions require the in-game model")
        if kind == "decision":
            integer(r["event_index"], "event_index", 3)
            if r["event_index"] != count + 1 or count == 3:
                raise SessionConflict("Decisions must resolve events 1, 2, 3 exactly once in order")
            text(r["event_id"], "event_id")
            text(r["action_id"], "action_id")
            if self.conn.execute("SELECT 1 FROM game_transitions WHERE session_id=%s AND event_id=%s", (sid, r["event_id"])).fetchone():
                raise SessionConflict("Event already resolved")
            self._resolution(r["resolution"], decision=True)
            count += 1
        else:
            if any(r[k] is not None for k in ("event_index", "event_id", "action_id")):
                raise StorageInputError("Event/action fields belong only to decisions")
            if kind == "finish":
                if count != 3 or r["winner"] not in ("blue", "red"):
                    raise SessionConflict("Finish requires three decisions and a supplied winner")
                self._resolution(r["resolution"], decision=False)
                previous = session["latest_prediction"]
                if r["resolution"].get("terminal_p_blue") != previous["p_blue"] or r["prediction"] != previous:
                    raise StorageInputError("Finish must retain the final pre-resolution prediction")
            elif r["resolution"] is not None:
                raise StorageInputError("Resolution metadata belongs only to decisions and finish")
        if kind != "finish" and r["winner"] is not None:
            raise StorageInputError("Winner belongs only to finish")
        phase = "finished" if kind == "finish" else "in_progress"
        seq = session["revision"] + 1
        self.conn.execute("""INSERT INTO game_transitions
            (session_id,transition_id,sequence,request_hash,kind,event_index,event_id,action_id,
             before_state,after_state,prediction_before,prediction_after,resolution,phase_after,event_count_after,winner)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sid, tid, seq, fingerprint, kind, r["event_index"], r["event_id"], r["action_id"],
             Jsonb(before), Jsonb(after), Jsonb(session["latest_prediction"]), Jsonb(r["prediction"]),
             Jsonb(r["resolution"] or {}), phase, count, r["winner"]))
        self.conn.execute("""UPDATE game_sessions SET current_state=%s, latest_prediction=%s,
            phase=%s, revision=%s, event_count=%s, winner=%s, updated_at=now() WHERE session_id=%s""",
            (Jsonb(after), Jsonb(r["prediction"]), phase, seq, count, r["winner"], sid))
        return serializable(self.conn.execute("SELECT * FROM game_transitions WHERE session_id=%s AND transition_id=%s", (sid, tid)).fetchone())

    @staticmethod
    def _resolution(value, decision):
        if not isinstance(value, dict):
            raise StorageInputError("Resolution metadata must be an object")
        integer(value.get("random_seed"), "random_seed", 2**63 - 1)
        draw = number(value.get("random_draw"), "random_draw", probability=True)
        if draw == 1:
            raise StorageInputError("random_draw must be less than 1")
        if decision:
            number(value.get("success_chance"), "success_chance", probability=True)
            if value.get("outcome") not in ("success", "failure"):
                raise StorageInputError("Decision outcome must be success or failure")
        else:
            number(value.get("terminal_p_blue"), "terminal_p_blue", probability=True)

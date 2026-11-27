"""Member A persistence contracts; supplied effects are fixtures, not a simulator."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import os
import pytest
from psycopg import sql
import psycopg
from src.storage.database import Database
from src.storage.sessions import SessionStore, SessionConflict, SessionNotFound, StorageInputError, validate_state


def state():
    stats = dict(total_gold=2500, kills=0, towers_destroyed=0, dragons_secured=0, barons_secured=0)
    return dict(elapsed_seconds=0, blue_champion_ids=[1, 2, 3, 4, 5],
                red_champion_ids=[6, 7, 8, 9, 10], blue=stats, red=deepcopy(stats),
                objective_availability={"dragon": False})


def versions():
    return dict(dataset="dataset-hash", opening_model="opening-hash", in_game_model="state-hash",
                features="features-v2", scenario="scenario-hash", simulator="sim-v2", champion_catalog="catalog-hash")


def prediction(stage="in_game", p=0.55):
    return dict(p_blue=p, stage=stage, model_version=versions()[stage + "_model"], coverage_warnings=[])


def create(db):
    store, sid = SessionStore(db), uuid4()
    store.create_session(sid, initial_state=state(), random_seed=42, versions=versions())
    return store, sid


def start(store, sid):
    return store.append_transition(sid, uuid4(), expected_revision=0, kind="start",
                                  after_state=state(), prediction=prediction("opening", .5))


def decision_request(current, i):
    after = deepcopy(current["current_state"])
    after["elapsed_seconds"] += 60
    after["blue"]["total_gold"] += 100
    return dict(expected_revision=current["revision"], kind="decision", after_state=after,
                prediction=prediction(p=.55), event_index=i, event_id=f"event-{i}", action_id="example-action",
                resolution=dict(random_seed=42+i, random_draw=.2, success_chance=.6, outcome="success"))


def test_create_load_and_idempotency(db):
    store, sid = create(db)
    again = store.create_session(sid, initial_state=state(), random_seed=42, versions=versions())
    assert again["phase"] == "draft" and again["revision"] == 0
    assert again["initial_state"] == state() and again["versions"] == versions()
    with pytest.raises(SessionConflict):
        store.create_session(sid, initial_state=state(), random_seed=43, versions=versions())
    assert store.load_session(sid)["random_seed"] == 42


def test_full_history_and_terminal_result(db):
    store, sid = create(db)
    start(store, sid)
    s = store.load_session(sid)
    after = deepcopy(s["current_state"])
    after["elapsed_seconds"] = 300
    store.append_transition(sid, uuid4(), expected_revision=1, kind="progression", after_state=after, prediction=prediction())
    for i in range(1, 4):
        store.append_transition(sid, uuid4(), **decision_request(store.load_session(sid), i))
    s = store.load_session(sid)
    after = deepcopy(s["current_state"])
    after["elapsed_seconds"] += 90
    store.append_transition(sid, uuid4(), expected_revision=s["revision"], kind="finish", after_state=after,
                            prediction=s["latest_prediction"], winner="blue",
                            resolution=dict(random_seed=99, random_draw=.4, terminal_p_blue=.55))
    finished = store.load_session(sid)
    history = store.transitions(sid)
    assert finished["phase"] == "finished" and finished["winner"] == "blue"
    assert finished["event_count"] == 3 and finished["revision"] == 6
    assert [r["kind"] for r in history] == ["start", "progression", "decision", "decision", "decision", "finish"]
    for prev, nxt in zip(history, history[1:]):
        assert prev["after_state"] == nxt["before_state"]
    assert history[-1]["prediction_after"]["p_blue"] == .55
    assert finished["current_state"]["blue_champion_ids"] == state()["blue_champion_ids"]
    with pytest.raises(SessionConflict):
        store.append_transition(sid, uuid4(), **decision_request(finished, 3))


def test_retry_returns_original_transition_even_after_later_writes(db):
    store, sid = create(db)
    start(store, sid)
    request = decision_request(store.load_session(sid), 1)
    tid = uuid4()
    original = store.append_transition(sid, tid, **request)
    store.append_transition(sid, uuid4(), **decision_request(store.load_session(sid), 2))
    assert store.append_transition(sid, tid, **request) == original
    assert len(store.transitions(sid)) == 3 and store.load_session(sid)["revision"] == 3
    request["prediction"]["p_blue"] = .6
    with pytest.raises(SessionConflict, match="reused"):
        store.append_transition(sid, tid, **request)


@pytest.mark.parametrize("mutation", ["stale", "skip_event", "picks", "time", "model", "probability", "resolution"])
def test_bad_transition_keeps_previous_state(db, mutation):
    store, sid = create(db)
    start(store, sid)
    before = store.load_session(sid)
    req = decision_request(before, 1)
    if mutation == "stale": req["expected_revision"] = 0
    if mutation == "skip_event": req["event_index"] = 2
    if mutation == "picks": req["after_state"]["blue_champion_ids"][0] = 11
    if mutation == "time": req["after_state"]["elapsed_seconds"] = 0
    if mutation == "model": req["prediction"]["model_version"] = "other-model"
    if mutation == "probability": req["prediction"]["p_blue"] = float("nan")
    if mutation == "resolution": req["resolution"].pop("random_draw")
    with pytest.raises((SessionConflict, StorageInputError)):
        store.append_transition(sid, uuid4(), **req)
    assert store.load_session(sid) == before and len(store.transitions(sid)) == 1


def test_repeat_event_and_early_finish_rejected(db):
    store, sid = create(db)
    start(store, sid)
    store.append_transition(sid, uuid4(), **decision_request(store.load_session(sid), 1))
    req = decision_request(store.load_session(sid), 2)
    req["event_id"] = "event-1"
    with pytest.raises(SessionConflict, match="already resolved"):
        store.append_transition(sid, uuid4(), **req)
    with pytest.raises(SessionConflict, match="three decisions"):
        store.append_transition(sid, uuid4(), expected_revision=2, kind="finish", after_state=req["after_state"],
                                prediction=prediction(), winner="red", resolution={})


def test_database_failure_rolls_back_transition_and_snapshot(db):
    store, sid = create(db)
    start(store, sid)
    before = store.load_session(sid)
    db.conn.execute("""CREATE FUNCTION reject_session_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'test rollback'; END $$""")
    db.conn.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON game_sessions FOR EACH ROW EXECUTE FUNCTION reject_session_write()")
    with pytest.raises(psycopg.Error, match="test rollback"):
        store.append_transition(sid, uuid4(), **decision_request(before, 1))
    assert store.load_session(sid) == before and len(store.transitions(sid)) == 1


@pytest.mark.parametrize("same_id", [False, True])
def test_concurrent_clicks_apply_once(db, same_id):
    store, sid = create(db)
    start(store, sid)
    request = decision_request(store.load_session(sid), 1)
    schema = db.conn.execute("SELECT current_schema() AS name").fetchone()["name"]
    tids = [uuid4(), uuid4()]
    if same_id: tids[1] = tids[0]
    barrier = Barrier(2)
    def submit(tid):
        with Database(os.environ["TEST_DATABASE_URL"]) as other:
            other.conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            barrier.wait(timeout=10)
            try:
                return SessionStore(other).append_transition(sid, tid, **request)
            except SessionConflict:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, tids))
    assert sum(isinstance(x, dict) for x in results) == (2 if same_id else 1)
    assert store.load_session(sid)["revision"] == 2 and len(store.transitions(sid)) == 2


def test_start_requires_opening_prediction_and_draft_phase(db):
    store, sid = create(db)
    with pytest.raises(SessionConflict):
        store.append_transition(sid, uuid4(), expected_revision=0, kind="start", after_state=state(), prediction=prediction())
    start(store, sid)
    with pytest.raises(SessionConflict):
        store.append_transition(sid, uuid4(), expected_revision=1, kind="start", after_state=state(), prediction=prediction("opening"))


def test_missing_session(db):
    with pytest.raises(SessionNotFound):
        SessionStore(db).load_session(uuid4())


@pytest.mark.parametrize("mutation", ["duplicate", "missing_pick", "negative", "bool", "nonfinite", "objectives"])
def test_invalid_state_shape(mutation):
    s = state()
    if mutation == "duplicate": s["red_champion_ids"][0] = 1
    if mutation == "missing_pick": s["blue_champion_ids"].pop()
    if mutation == "negative": s["red"]["kills"] = -1
    if mutation == "bool": s["blue_champion_ids"][0] = True
    if mutation == "nonfinite": s["elapsed_seconds"] = float("inf")
    if mutation == "objectives": s.pop("objective_availability")
    with pytest.raises(StorageInputError): validate_state(s)


def test_initial_state_must_be_equal_and_zero(db):
    store = SessionStore(db)
    s = state()
    s["elapsed_seconds"] = 720
    with pytest.raises(StorageInputError, match="00:00"):
        store.create_session(uuid4(), initial_state=s, random_seed=1, versions=versions())
    s["elapsed_seconds"] = 0
    s["blue"]["total_gold"] = 3000
    with pytest.raises(StorageInputError, match="equal"):
        store.create_session(uuid4(), initial_state=s, random_seed=1, versions=versions())

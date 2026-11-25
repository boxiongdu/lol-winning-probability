"""Rift Decisions batch pipeline (Member D wires A's and B's task functions).

init_db -> ingest -> check_training_data -> train_candidate -> publish_model

Airflow records each task's status, duration and errors; every task also returns
its record counts / metrics as XCom. Publication happens only after the
candidate passes validation, and the previous model is archived first. The
interactive game is not orchestrated here: the app reads the published model.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

try:  # Airflow 3
    from airflow.sdk import Param, dag, get_current_context, task
except ImportError:  # Airflow 2.x fallback
    from airflow.decorators import dag, task
    from airflow.models.param import Param
    from airflow.operators.python import get_current_context
from airflow.exceptions import AirflowSkipException

import rift_tasks


@dag(
    dag_id="rift_pipeline",
    description="Riot ingestion -> PostgreSQL -> features/model -> validated publish",
    schedule=None,  # triggered manually for the course project
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,  # A's collector must be the only writer per database/region
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    params={
        "ingest_mode": Param("auto", enum=["auto", "collect", "rebuild", "fixture"],
                             description="auto = collect if RIOT_API_KEY and seeds exist, else rebuild"),
        "target_matches": Param(100, type="integer", minimum=1),
        "min_matches": Param(10, type="integer", minimum=4,
                             description="Skip training below this many eligible matches"),
        "require_beats_baseline": Param(True, type="boolean"),
    },
    tags=["rift", "week8"],
)
def rift_pipeline():
    @task
    def init_db() -> dict:
        return rift_tasks.init_db()

    @task(retries=0)  # A's CLI already retries HTTP and resumes; avoid duplicate API bursts
    def ingest(_db: dict) -> dict:
        params = get_current_context()["params"]
        return rift_tasks.ingest(params["ingest_mode"], target=params["target_matches"])

    @task
    def check_training_data(_ingested: dict) -> dict:
        params = get_current_context()["params"]
        try:
            return rift_tasks.check_training_data(params["min_matches"])
        except rift_tasks.NotEnoughData as exc:
            raise AirflowSkipException(str(exc)) from exc

    @task
    def train_candidate(_checked: dict) -> dict:
        return rift_tasks.train_candidate(get_current_context()["run_id"])

    @task
    def publish_model(trained: dict) -> dict:
        params = get_current_context()["params"]
        return rift_tasks.publish_model(trained["candidate_path"], params["require_beats_baseline"])

    publish_model(train_candidate(check_training_data(ingest(init_db()))))


rift_pipeline()

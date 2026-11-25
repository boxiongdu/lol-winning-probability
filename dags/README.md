# Airflow DAGs

`rift_pipeline.py` defines `rift_pipeline`:
init_db → ingest → check_training_data → train_candidate → publish_model.
Task bodies live in `rift_tasks.py` (listed in `.airflowignore`) and call A's
ingestion CLI and B's `train_model`. Airflow tracks status, duration and errors;
tasks return counts and metrics as XCom. Gameplay stays outside Airflow.
See [docs/member_d_week8.md](../docs/member_d_week8.md).

"""Plain-Python task bodies for the Rift pipeline DAG (Member D, Week 8).

They wrap Member A's ingestion CLI and Member B's training function, so they run
and test without Airflow. Each returns a JSON-serializable dict that Airflow
stores as the task's XCom; Airflow itself records status, duration and errors.

Listed in .airflowignore so the DAG processor does not parse this file as a DAG.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.getenv("RIFT_ROOT", Path(__file__).resolve().parents[1]))
MODELS = ROOT / "models"
PUBLISHED_MODEL = Path(os.getenv("MODEL_PATH", MODELS / "week8.json"))
DEFAULT_SEEDS = ROOT / "configs" / "seeds.local.json"
SAMPLE_DIR = ROOT / "data" / "sample"
DEMO_MATCHES = ("DEMO_1",)  # synthetic fixture; B's SQL excludes it from training

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    """A pipeline gate failed; the message is safe to show (no secrets)."""


class NotEnoughData(PipelineError):
    """Too few eligible matches to train; the published model is kept."""


def _database_url(database_url: str | None = None) -> str:
    from src.config import database_url as configured  # loads .env, never logs the value
    return database_url or configured()


def _run_ingestion_cli(argv: list[str]) -> tuple[int, dict]:
    """Run A's CLI in-process so its run bookkeeping (ingestion_runs) is reused unchanged."""
    from src.ingestion.__main__ import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    if err.getvalue().strip():
        log.warning("ingestion stderr: %s", err.getvalue().strip())
    text = out.getvalue().strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {"stdout": text[-2000:]}
    if code == 1:
        raise PipelineError(f"Ingestion command {argv[0]} failed: {err.getvalue().strip()[-500:]}")
    return code, payload


def init_db() -> dict:
    """Apply A's idempotent schema migration; returns current dataset counts."""
    _code, counts = _run_ingestion_cli(["init-db"])
    return {"database": counts}


def ingest(mode: str = "auto", seeds: str | None = None, target: int | None = None) -> dict:
    """Collect from the Riot API, or rebuild from cached raw responses.

    mode:
      auto    collect when RIOT_API_KEY and a seeds file exist, otherwise rebuild
      collect Riot API discovery + download (needs key and seeds)
      rebuild normalize cached raw responses only, no network
      fixture load the synthetic DEMO_1 sample, then rebuild (plumbing check only)
    """
    from src.config import load_environment
    load_environment()  # repository .env may hold RIOT_API_KEY; exported values still win
    seeds_path = Path(seeds) if seeds else DEFAULT_SEEDS
    if mode == "auto":
        mode = "collect" if os.getenv("RIOT_API_KEY") and seeds_path.is_file() else "rebuild"
    if mode == "collect":
        if not seeds_path.is_file():
            raise PipelineError(f"Seeds file not found: {seeds_path.relative_to(ROOT) if seeds_path.is_relative_to(ROOT) else seeds_path}")
        argv = ["collect", "--seeds", str(seeds_path)]
        if target:
            argv += ["--target", str(int(target))]
    elif mode == "rebuild":
        argv = ["rebuild"]
    elif mode == "fixture":
        argv = ["load-fixture", "--directory", str(SAMPLE_DIR)]
    else:
        raise PipelineError(f"Unknown ingestion mode: {mode}")
    code, payload = _run_ingestion_cli(argv)
    status = payload.get("status", "success" if code == 0 else "partial")
    if code == 2:
        log.warning("Ingestion finished with partial coverage: %s", payload.get("metrics"))
    return {"mode": mode, "exit_code": code, "status": status,
            "run_id": payload.get("run_id"), "metrics": payload.get("metrics", {}),
            "database": payload.get("database", {})}


def check_training_data(min_matches: int = 10, database_url: str | None = None) -> dict:
    """Count eligible (non-demo) matches and nonterminal snapshots before training."""
    import psycopg

    with psycopg.connect(_database_url(database_url)) as conn:
        matches = conn.execute(
            "SELECT count(*) FROM matches WHERE match_id <> ALL(%s)", (list(DEMO_MATCHES),)).fetchone()[0]
        snapshots = conn.execute(
            "SELECT count(DISTINCT (match_id, timestamp_ms)) FROM team_frame_states "
            "WHERE match_id <> ALL(%s)", (list(DEMO_MATCHES),)).fetchone()[0]
    result = {"eligible_matches": matches, "snapshots": snapshots, "min_matches": min_matches}
    if matches < min_matches:
        raise NotEnoughData(f"Only {matches} eligible matches (need {min_matches}); "
                            "keeping the currently published model.")
    return result


def train_candidate(run_id: str, database_url: str | None = None) -> dict:
    """Run B's training into a candidate file; nothing is published here."""
    from src.modeling.train import train_model

    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in run_id)[:120]
    candidate = MODELS / "candidates" / f"{safe}.json"
    summary = train_model(_database_url(database_url), candidate)
    summary["candidate_path"] = str(candidate)
    return summary


def validate_model(path: str | Path, require_beats_baseline: bool = True) -> dict:
    """Checks a model artifact must pass before the app may load it."""
    from src.features.state import FEATURES
    from src.modeling.predict import predict_blue_win
    from app.session import SAMPLE_STATE

    model = json.loads(Path(path).read_text())
    problems = []
    if tuple(model.get("features", ())) != FEATURES:
        problems.append("feature order differs from src.features.state.FEATURES")
    splits = model.get("splits", {})
    seen: dict[str, str] = {}
    for name, ids in splits.items():
        for match_id in ids:
            if match_id in seen:
                problems.append(f"match {match_id} in both {seen[match_id]} and {name}")
            seen[match_id] = name
    if not splits:
        problems.append("artifact has no split manifest")
    try:
        p = predict_blue_win(SAMPLE_STATE, path)
        if not 0 <= p <= 1:
            problems.append(f"sample prediction outside [0, 1]: {p}")
    except Exception as exc:  # noqa: BLE001 - any failure blocks publication
        problems.append(f"sample prediction failed: {type(exc).__name__}")
        p = None
    val = model.get("metrics", {}).get("validation", {})
    if require_beats_baseline and val:
        if val["logistic"]["log_loss"] > val["baseline"]["log_loss"]:
            problems.append("validation log loss is worse than the constant baseline")
    if problems:
        raise PipelineError("Model failed validation: " + "; ".join(problems[:5]))
    return {"sample_p_blue": p, "split_matches": {k: len(v) for k, v in splits.items()},
            "metrics": model.get("metrics", {})}


def publish_model(candidate_path: str | Path, require_beats_baseline: bool = True,
                  published: Path = PUBLISHED_MODEL) -> dict:
    """Validate, archive the current model, then atomically replace it."""
    candidate_path = Path(candidate_path)
    checks = validate_model(candidate_path, require_beats_baseline)
    published = Path(published)
    archived = None
    if published.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = published.parent / "archive" / f"{published.stem}-{stamp}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(published, archive)
        archived = str(archive)
    tmp = published.with_suffix(".json.tmp")
    shutil.copy2(candidate_path, tmp)
    tmp.replace(published)  # atomic on the same filesystem; the app never sees half a file
    from app.session import model_info
    return {"published_path": str(published), "model_version": model_info(published)["model_version"],
            "archived_previous": archived, **checks}

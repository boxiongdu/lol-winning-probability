"""Run with python -m src.ingestion; never print keys, DSNs, or raw responses."""
import argparse
import json
import os
from pathlib import Path
import sys
import psycopg
from src.config import database_url, load_environment, env_number
from src.errors import ConfigurationError, InputError
from src.storage.database import Database
from .client import REGIONS, RiotClient, RiotError
from .pipeline import discover, load_fixture, process, read_seeds


def parser():
    load_environment()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["init-db", "collect", "rebuild", "load-fixture", "status"])
    p.add_argument("--region", choices=sorted(REGIONS), default=os.getenv("RIOT_REGION", "americas"))
    p.add_argument("--seeds", type=Path)
    p.add_argument("--directory", type=Path)
    p.add_argument("--target", type=int, default=env_number("TARGET_MATCH_COUNT", 100))
    p.add_argument("--per-player", type=int, default=200)
    p.add_argument("--queue", type=int, default=env_number("RIOT_QUEUE_ID", 420))
    p.add_argument("--map", type=int, default=11, dest="map_id")
    p.add_argument("--min-duration", type=int, default=300)
    p.add_argument("--start-time", type=int, help="Inclusive Unix seconds")
    p.add_argument("--end-time", type=int, help="Exclusive Unix seconds")
    return p


def main(argv=None):
    try:
        p = parser()
        args = p.parse_args(argv)
    except ConfigurationError as exc:
        print(f"ConfigurationError: {exc}", file=sys.stderr)
        return 1
    if args.region not in REGIONS:
        p.error("RIOT_REGION must be americas, europe, asia, or sea (not na1)")
    if min(args.target, args.per_player, args.min_duration) < 1:
        p.error("target, per-player and min-duration must be positive")
    if args.start_time is not None and args.end_time is not None and args.start_time >= args.end_time:
        p.error("start-time must be less than end-time")
    client = None
    try:
        if args.command == "collect":
            if args.seeds is None:
                p.error("collect requires --seeds")
            seeds = read_seeds(args.seeds)
            client = RiotClient(os.getenv("RIOT_API_KEY"), args.region,
                                env_number("RIOT_REQUEST_INTERVAL", 1.3, float))
        if args.command == "load-fixture" and args.directory is None:
            p.error("load-fixture requires --directory")
        with Database(database_url()) as db:
            db.initialize()
            if args.command in ("init-db", "status"):
                print(json.dumps(db.counts(args.region), indent=2))
                return 0
            run_id = db.start_run(args.command, args.region)
            metrics = {}
            try:
                if args.command == "collect":
                    metrics["discovered"] = discover(db, client, seeds, args.queue, args.per_player,
                                                     args.start_time, args.end_time)
                elif args.command == "load-fixture":
                    metrics["fixture_pairs"] = load_fixture(db, args.region, args.directory)
                metrics.update(process(db, args.region, client,
                                       args.target if client else None, args.queue,
                                       args.map_id, args.min_duration, args.start_time, args.end_time))
                if client:
                    metrics.update(api_requests=client.requests, retries=client.retry_count)
                short = client is not None and metrics["accepted"] < args.target
                bad = metrics["failed"] > 0 or metrics["incomplete"] > 0
                status = "partial" if short or bad else "success"
                db.finish_run(run_id, status, metrics)
                print(json.dumps({"run_id": str(run_id), "status": status, "metrics": metrics,
                                  "database": db.counts(args.region)}, indent=2))
                return 2 if short or bad else 0
            except Exception as exc:
                if client:
                    metrics.update(api_requests=client.requests, retries=client.retry_count)
                # Generic errors cannot leak connection strings or response contents.
                metrics["database"] = db.counts(args.region)
                db.finish_run(run_id, "failed", metrics, type(exc).__name__)
                raise
    except (RiotError, ConfigurationError, InputError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"InputError: Invalid JSON at line {exc.lineno}, column {exc.colno}.", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("InputError: Input file not found. Check --seeds or --directory relative to your current folder.", file=sys.stderr)
        return 1
    except psycopg.OperationalError:
        print("DatabaseError: Cannot connect to PostgreSQL. Start the database (docker compose up -d --wait db) and check DATABASE_URL credentials and port.", file=sys.stderr)
        return 1
    except (ValueError, OSError, psycopg.Error) as exc:
        print(f"{type(exc).__name__}: Unable to process the input or database configuration. Check the runbook; raw error details are omitted to protect credentials.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

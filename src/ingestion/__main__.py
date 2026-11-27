"""Run with python -m src.ingestion; never print keys, DSNs, or raw responses."""
import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import sys
import psycopg
from src.config import database_url, load_environment, env_number
from src.errors import ConfigurationError, InputError
from src.storage.database import Database
from src.storage.migrations import MigrationError
from .audit import write_report
from .client import REGIONS, PLATFORM_REGIONS, RiotClient, RiotError
from .challenger import collect_challenger
from .pipeline import discover, load_fixture, process, read_seeds


def parser():
    load_environment()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["init-db", "collect", "rebuild", "load-fixture", "status", "audit", "collect-challenger"])
    p.add_argument("--region", choices=sorted(REGIONS), default=os.getenv("RIOT_REGION", "americas"))
    p.add_argument("--seeds", type=Path)
    p.add_argument("--platform", type=str.lower, choices=sorted(PLATFORM_REGIONS), default="na1")
    p.add_argument("--matches-per-player", type=int, default=10)
    p.add_argument("--sampling-seed", type=int, default=42)
    p.add_argument("--history-limit", type=int, default=1000, help="Refuse to sample if a player exceeds this history-window size")
    p.add_argument("--manifest", type=Path, default=Path("data/raw/challenger_sample.json"))
    p.add_argument("--start-date", help="UTC YYYY-MM-DD, alternative to --start-time")
    p.add_argument("--end-date", help="Exclusive UTC YYYY-MM-DD, alternative to --end-time")
    p.add_argument("--sample-id", help="Restrict audit to a frozen sampling_runs UUID")
    p.add_argument("--directory", type=Path)
    p.add_argument("--target", type=int, default=env_number("TARGET_MATCH_COUNT", 100))
    p.add_argument("--per-player", type=int, default=200)
    p.add_argument("--queue", type=int, default=env_number("RIOT_QUEUE_ID", 420))
    p.add_argument("--map", type=int, default=11, dest="map_id")
    p.add_argument("--min-duration", type=int, default=300)
    p.add_argument("--patch", help="Exact gameVersion or numeric patch prefix, e.g. 16.22")
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
    if args.start_date is not None or args.end_date is not None:
        if args.start_time is not None or args.end_time is not None or not (args.start_date and args.end_date):
            p.error("Provide both date flags or both Unix time flags, without mixing them")
        try:
            args.start_time = int(datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            args.end_time = int(datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            p.error("Dates must use UTC YYYY-MM-DD format")
    if args.command == "collect-challenger":
        args.region = PLATFORM_REGIONS[args.platform]
        if args.start_time is None or args.end_time is None:
            p.error("collect-challenger requires a fixed start/end date or Unix time window")
        if args.start_time < 0 or args.matches_per_player < 1 or args.history_limit < args.matches_per_player or args.sampling_seed < 0:
            p.error("Invalid sampling counts, seed or time window")
        if args.queue != 420 or args.map_id != 11:
            p.error("collect-challenger supports ranked solo/duo (queue 420, map 11) only")
    if args.region not in REGIONS:
        p.error("RIOT_REGION must be americas, europe, asia, or sea (not na1)")
    if min(args.target, args.per_player, args.min_duration) < 1:
        p.error("target, per-player and min-duration must be positive")
    if args.start_time is not None and args.end_time is not None and args.start_time >= args.end_time:
        p.error("start-time must be less than end-time")
    if args.patch is not None:
        import re
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", args.patch):
            p.error("patch must be a numeric dotted version such as 16.22")
    client = None
    try:
        if args.command == "collect":
            if args.seeds is None:
                p.error("collect requires --seeds")
            seeds = read_seeds(args.seeds)
            client = RiotClient(os.getenv("RIOT_API_KEY"), args.region,
                                env_number("RIOT_REQUEST_INTERVAL", 1.3, float))
        if args.command == "collect-challenger":
            client = RiotClient(os.getenv("RIOT_API_KEY"), args.region,
                                env_number("RIOT_REQUEST_INTERVAL", 1.3, float))
        if args.command == "load-fixture" and args.directory is None:
            p.error("load-fixture requires --directory")
        with Database(database_url()) as db:
            db.initialize()
            if args.command == "collect-challenger":
                result = collect_challenger(db, client, manifest_path=args.manifest,
                    platform=args.platform, start_time=args.start_time, end_time=args.end_time,
                    matches_per_player=args.matches_per_player, sampling_seed=args.sampling_seed,
                    history_limit=args.history_limit, patch=args.patch, min_duration=args.min_duration,
                    progress=lambda message: print(message, file=sys.stderr, flush=True))
                print(json.dumps(result, indent=2))
                return 0 if result["status"] == "complete" else 2
            if args.command == "audit":
                print(write_report(db, region=args.region, queue=args.queue, map_id=args.map_id,
                      min_duration=args.min_duration, patch=args.patch,
                      start_time=args.start_time, end_time=args.end_time, sample_id=args.sample_id))
                return 0
            if args.command in ("init-db", "status"):
                print(json.dumps(db.counts(args.region), indent=2))
                return 0
            run_id = db.start_run(args.command, args.region)
            metrics = {"scope": dict(region=args.region, queue=args.queue, map_id=args.map_id,
                       min_duration=args.min_duration, patch=args.patch,
                       start_time=args.start_time, end_time=args.end_time)}
            try:
                if args.command == "collect":
                    metrics["discovered"] = discover(db, client, seeds, args.queue, args.per_player,
                                                     args.start_time, args.end_time)
                elif args.command == "load-fixture":
                    metrics["fixture_pairs"] = load_fixture(db, args.region, args.directory)
                metrics.update(process(db, args.region, client,
                                       args.target if client else None, args.queue,
                                       args.map_id, args.min_duration, args.start_time, args.end_time, args.patch))
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
    except (RiotError, ConfigurationError, InputError, MigrationError) as exc:
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

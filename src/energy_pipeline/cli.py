"""Command line entry point: `pipeline`."""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path

from energy_pipeline import __version__
from energy_pipeline.config import Settings, get_settings
from energy_pipeline.extract import to_rows
from energy_pipeline.extract.ote import PRAGUE
from energy_pipeline.http import make_client
from energy_pipeline.load.dbt_results import parse_artifact
from energy_pipeline.load.warehouse import DBT_RESULTS, Warehouse
from energy_pipeline.pipeline import (
    SOURCES,
    Task,
    backfill_tasks,
    daily_tasks,
    pending,
    run_tasks,
)

BACKFILL_MIN_INTERVAL = 0.25  # seconds between requests to one host: polite to public APIs


def prague_today() -> date:
    return datetime.now(PRAGUE).date()


def record_dbt_results(artifacts: list[Path], settings: Settings) -> int:
    with Warehouse.from_settings(settings) as wh:
        for path in artifacts:
            results = parse_artifact(json.loads(path.read_text("utf-8")))
            run_id = results[0].invocation_id if results else "empty"
            rows = wh.upsert(DBT_RESULTS, to_rows(results), run_id)
            logging.info("%s: %d dbt results recorded", path, rows)
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("config", help="show the resolved configuration")
    subcommands.add_parser("init", help="create the raw and ops schemas if they do not exist")

    sources = argparse.ArgumentParser(add_help=False)
    sources.add_argument(
        "--source",
        choices=SOURCES,
        action="append",
        help="source to load; repeat for several (default: all)",
    )

    run = subcommands.add_parser("run", parents=[sources], help="load one business date")
    run.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="business date YYYY-MM-DD (default: today in Europe/Prague)",
    )
    subcommands.add_parser(
        "daily", help="the scheduled run: tomorrow's prices, FX, last week's weather"
    )
    backfill = subcommands.add_parser("backfill", parents=[sources], help="load a date range")
    backfill.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    backfill.add_argument(
        "--to", dest="end", type=date.fromisoformat, default=None, help="default: today"
    )
    backfill.add_argument(
        "--resume", action="store_true", help="skip periods that already loaded successfully"
    )
    record = subcommands.add_parser(
        "record-dbt-results", help="store dbt run_results.json / sources.json in ops.dbt_results"
    )
    record.add_argument("artifacts", type=Path, nargs="+", help="dbt artifact JSON files")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    if args.command == "config":
        print(f"env:      {settings.env}")
        print(f"database: {settings.database}")
        return 0
    if args.command == "init":
        with Warehouse.from_settings(settings):  # creating the schema is part of connecting
            print(f"schema ready in {settings.database}")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is noise
    if args.command == "record-dbt-results":
        return record_dbt_results(args.artifacts, settings)

    today = prague_today()
    min_interval = 0.0
    if args.command == "daily":
        tasks = daily_tasks(today)
    elif args.command == "backfill":
        tasks = backfill_tasks(args.start, args.end or today, args.source or SOURCES)
        min_interval = BACKFILL_MIN_INTERVAL
    else:
        day = args.date or today
        tasks = [Task(source, day) for source in args.source or SOURCES]

    with make_client(min_interval=min_interval) as client, Warehouse.from_settings(settings) as wh:
        if getattr(args, "resume", False):
            tasks = pending(tasks, wh)
        results = run_tasks(tasks, client, wh, today)
    return 1 if any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

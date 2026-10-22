"""Command line entry point: `pipeline`."""

import argparse
import logging
from datetime import date, datetime

from energy_pipeline import __version__
from energy_pipeline.config import get_settings
from energy_pipeline.extract.ote import PRAGUE
from energy_pipeline.http import make_client
from energy_pipeline.load.warehouse import Warehouse
from energy_pipeline.pipeline import SOURCES, Task, daily_tasks, run_tasks


def prague_today() -> date:
    return datetime.now(PRAGUE).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("config", help="show the resolved configuration")

    run = subcommands.add_parser("run", help="load one business date")
    run.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="business date YYYY-MM-DD (default: today in Europe/Prague)",
    )
    run.add_argument(
        "--source",
        choices=SOURCES,
        action="append",
        help="source to load; repeat for several (default: all)",
    )
    subcommands.add_parser(
        "daily", help="the scheduled run: tomorrow's prices, FX, last week's weather"
    )

    args = parser.parse_args(argv)
    settings = get_settings()
    if args.command == "config":
        print(f"env:      {settings.env}")
        print(f"database: {settings.database}")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is noise
    today = prague_today()
    if args.command == "daily":
        tasks = daily_tasks(today)
    else:
        day = args.date or today
        tasks = [Task(source, day) for source in args.source or SOURCES]

    with make_client() as client, Warehouse(settings.database) as warehouse:
        results = run_tasks(tasks, client, warehouse, today)
    return 1 if any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

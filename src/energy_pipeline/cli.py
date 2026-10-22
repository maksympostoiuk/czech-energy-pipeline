"""Command line entry point: `pipeline`."""

import argparse

from energy_pipeline import __version__
from energy_pipeline.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("config", help="show the resolved configuration")

    args = parser.parse_args(argv)
    if args.command == "config":
        settings = get_settings()
        print(f"env:      {settings.env}")
        print(f"database: {settings.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

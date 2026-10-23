"""Reference data that lives in both Python and dbt must not drift apart."""

import csv
from datetime import date
from pathlib import Path

from energy_pipeline.extract import chmi, ote

SEEDS = Path(__file__).parent.parent / "dbt" / "seeds"


def read_seed(name: str) -> list[dict[str, str]]:
    with (SEEDS / f"{name}.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_known_incomplete_days_match_extractor() -> None:
    seed_days = {
        date.fromisoformat(r["delivery_date"]) for r in read_seed("ote_known_incomplete_days")
    }

    assert seed_days == set(ote.KNOWN_INCOMPLETE_DAYS)


def test_selected_stations_match_extractor() -> None:
    seed_stations = [r["station_id"] for r in read_seed("chmi_stations_selected")]

    assert sorted(seed_stations) == sorted(chmi.STATIONS)

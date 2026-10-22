"""Pin the source facts from docs/sources.md to the stored real responses.

If a re-fetched fixture breaks one of these, the source changed and the parsers need review.
"""

import csv
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def ote_series(name: str) -> dict[str, int]:
    lines = load(f"ote/{name}")["data"]["dataLine"]
    return {line["title"]: len(line["point"]) for line in lines}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("dam_2025-09-30_hourly.json", {"Volume (MWh)": 24, "Price (EUR/MWh)": 24}),
        ("dam_2024-10-27_hourly_dst_25h.json", {"Volume (MWh)": 25, "Price (EUR/MWh)": 25}),
        (
            "dam_2026-10-21_15min.json",
            {
                "Volume (MWh)": 96,
                "15min price (EUR/MWh)": 96,
                "60min price reference (EUR/MWh)": 96,
            },
        ),
        (
            "dam_2025-10-26_15min_dst_100.json",
            {
                "Volume (MWh)": 100,
                "15min price (EUR/MWh)": 100,
                "60min price reference (EUR/MWh)": 100,
            },
        ),
        (
            "dam_2026-03-29_15min_dst_92.json",
            {
                "Volume (MWh)": 92,
                "15min price (EUR/MWh)": 92,
                "60min price reference (EUR/MWh)": 92,
            },
        ),
        ("dam_2026-10-30_not_published.json", {}),
        ("dam_2025-07-04_partial_12h.json", {"Volume (MWh)": 12, "Price (EUR/MWh)": 12}),
    ],
)
def test_ote_series_and_period_counts(name: str, expected: dict[str, int]) -> None:
    assert ote_series(name) == expected


@pytest.mark.parametrize(
    ("name", "valid_for"),
    [
        ("daily_2026-10-16_friday.json", "2026-10-16"),
        ("daily_2026-10-17_saturday.json", "2026-10-16"),
        ("daily_2026-09-28_holiday.json", "2026-09-25"),
    ],
)
def test_cnb_returns_last_valid_fixing(name: str, valid_for: str) -> None:
    rates = load(f"cnb/{name}")["rates"]
    eur = next(rate for rate in rates if rate["currencyCode"] == "EUR")

    assert eur["validFor"] == valid_for
    assert eur["amount"] == 1


def test_cnb_month_skips_non_business_days() -> None:
    days = [rate["validFor"] for rate in load("cnb/eur_month_2026-09.json")["rates"]]

    assert "2026-09-26" not in days  # Saturday
    assert "2026-09-28" not in days  # St. Wenceslas Day
    assert len(days) == 21


def test_chmi_json_shape_and_missing_values() -> None:
    table = load("chmi/10m-0-20000-0-11520-20261021.json")["data"]["data"]
    rows = [dict(zip(table["header"].split(","), row, strict=True)) for row in table["values"]]
    elements = {row["ELEMENT"] for row in rows}

    assert table["header"] == "STATION,ELEMENT,DT,VAL,FLAG,QUALITY"
    assert {"T", "F", "H", "RGLB10", "SSV10M", "SRA10M"} <= elements
    assert sum(row["ELEMENT"] == "T" for row in rows) == 144
    # Missing values are empty strings marked with quality code 4, not nulls.
    missing = [row for row in rows if row["VAL"] == ""]
    assert missing
    assert all(row["QUALITY"] == 4.0 for row in missing)


def test_chmi_historical_csv_differs_from_json() -> None:
    with (FIXTURES / "chmi/10m-0-20000-0-11520-T-202501.head.csv").open(encoding="utf-8") as f:
        reader = csv.reader(f)
        header, first = next(reader), next(reader)

    assert header == ["STATION", "ELEMENT", "DT", "VALUE", "FLAG", "QUALITY", ""]
    assert first[2] == "2025-01-01T00:00Z"  # no seconds, unlike the JSON files

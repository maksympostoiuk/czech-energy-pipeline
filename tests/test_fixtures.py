"""Pin the source facts from docs/sources.md to the stored real responses.

If a re-fetched fixture breaks one of these, the source changed and the parsers need review.
"""

import csv
import json
from datetime import datetime, timedelta
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


OPEN_METEO_VARS = [
    "temperature_2m",
    "shortwave_radiation",
    "wind_speed_10m",
    "wind_speed_100m",
    "cloud_cover",
]


def open_meteo_hours(name: str, suffix: str = "") -> dict[str, list]:
    """valid hour -> values of the five variables (with an optional _previous_dayN suffix)."""
    hourly = load(f"openmeteo/{name}")["hourly"]
    return {
        time: [hourly[f"{var}{suffix}"][i] for var in OPEN_METEO_VARS]
        for i, time in enumerate(hourly["time"])
    }


def test_open_meteo_shape_utc_and_units() -> None:
    locations = load("openmeteo/previous_icon_eu_2026-10-24_2loc.json")
    single = load("openmeteo/previous_icon_eu_2026-06-15.json")

    # Several coordinates give a list in request order; one coordinate gives a plain object.
    assert isinstance(locations, list)
    assert len(locations) == 2
    assert isinstance(single, dict)
    # Grid cell coordinates, not the requested ones.
    assert (single["latitude"], single["longitude"]) == (50.0, 14.4375)
    for location in [*locations, single]:
        assert location["utc_offset_seconds"] == 0
        assert location["hourly_units"]["wind_speed_10m_previous_day1"] == "m/s"
        assert location["hourly_units"]["shortwave_radiation_previous_day2"] == "W/m²"
    # timezone=UTC: naive ISO hours, and 48 of them over the 25-hour local DST day.
    times = locations[0]["hourly"]["time"]
    assert times[0] == "2026-10-24T00:00"
    assert len(times) == 48


def test_previous_day_n_comes_from_the_run_started_n_days_earlier() -> None:
    """The leak-safety fact: previous_dayN at valid hour T is the 6-hourly ICON-EU run
    initialised at floor_6h(T - N days), so its run time is known from T alone."""
    runs = {
        datetime.fromisoformat(path.stem.removeprefix("single_icon_eu_").removesuffix("Z")): (
            open_meteo_hours(path.name)
        )
        for path in (FIXTURES / "openmeteo").glob("single_icon_eu_2026-06-1*.json")
    }
    assert len(runs) == 8  # 00/06/12/18Z on 2026-06-13 and 2026-06-14
    for n in (1, 2):
        for time, values in open_meteo_hours(
            "previous_icon_eu_2026-06-15.json", f"_previous_day{n}"
        ).items():
            earlier = datetime.fromisoformat(time) - timedelta(days=n)
            run = earlier.replace(hour=earlier.hour // 6 * 6)
            assert runs[run][time] == values, (time, n, run)


def test_previous_day_falls_back_to_an_older_run_when_one_is_missing() -> None:
    """The 2026-10-18 06Z run is not in the archive: the hours it should serve come from
    the 00Z run, never from a later one."""
    previous = open_meteo_hours("previous_icon_eu_2026-10-20.json", "_previous_day2")
    run_00z = open_meteo_hours("single_icon_eu_2026-10-18T00Z.json")
    run_06z = open_meteo_hours("single_icon_eu_2026-10-18T06Z.json")

    for hour in range(6, 12):
        time = f"2026-10-20T{hour:02d}:00"
        assert previous[time] == run_00z[time]
        assert previous[time] != run_06z[time]


def test_previous_runs_archive_has_holes() -> None:
    hourly = load("openmeteo/previous_icon_eu_2026-04-09_gap.json")["hourly"]

    assert all(v is None for v in hourly["shortwave_radiation_previous_day2"])
    assert all(v is not None for v in hourly["shortwave_radiation_previous_day1"])
    assert all(v is not None for v in hourly["temperature_2m_previous_day2"])


def test_open_meteo_error_and_model_metadata() -> None:
    error = load("openmeteo/error_invalid_model.json")
    meta = load("openmeteo/meta_dwd_icon_eu.json")

    assert error["error"] is True
    assert "no_such_model" in error["reason"]
    # Unix seconds; ICON-EU runs every 3 h and is served about 3 h after initialisation.
    assert meta["update_interval_seconds"] == 3 * 3600
    assert meta["last_run_availability_time"] > meta["last_run_initialisation_time"]

"""Score Open-Meteo day-ahead forecasts against ČHMÚ observations at the 8 project stations.

Usage (needs network and a loaded local warehouse):
    uv run python scripts/compare_forecast_models.py [--start 2024-03-14] [--end 2026-10-20]

Only forecasts that exist at 12:00 Prague on day D are scored for delivery day D+1: the
value predicted one day earlier when its run (initialised at floor_6h(T - 1 day)) is no later
than 00Z on D, otherwise the value predicted two days earlier. See docs/sources.md section 5.
The score is for the national mean of the stations, the signal the price model uses.
"""

import argparse
import json
import random
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from energy_pipeline.extract.chmi import STATIONS  # noqa: E402

MODELS = ["dwd_icon_eu", "ecmwf_ifs025", "gfs_seamless"]
VARIABLES = {  # Open-Meteo variable -> column of int_weather_hourly_station
    "temperature_2m": "temperature_c",
    "wind_speed_10m": "wind_speed_ms",
    "shortwave_radiation": "global_radiation_wm2",
}
PAUSE_SECONDS = 65  # one long request weighs hundreds of calls; the limit is 600 per minute


def get(url: str) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "czech-energy-pipeline"})
    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.load(response)
    return payload if isinstance(payload, list) else [payload]


def coordinates(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    stations = con.sql(
        "select station_id, latitude, longitude from marts.dim_station order by station_id"
    ).df()
    assert set(stations["station_id"]) == set(STATIONS), "dim_station differs from STATIONS"
    return stations


def frame(payload: list[dict], stations: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for station_id, location in zip(stations["station_id"], payload, strict=True):
        part = pd.DataFrame(location["hourly"])
        part["station_id"] = station_id
        parts.append(part)
    result = pd.concat(parts)
    result["valid_at"] = pd.to_datetime(result.pop("time")).dt.tz_localize("UTC")
    return result


def known_at_noon(forecast: pd.DataFrame, lead_days: int | None = None) -> pd.DataFrame:
    """Pick previous_day1 where its run is no later than 00Z of D, else previous_day2.
    lead_days=1 or 2 takes that series everywhere instead, to show what the rule costs."""
    delivery = forecast["valid_at"].dt.tz_convert("Europe/Prague").dt.tz_localize(None)
    latest_run = (delivery.dt.normalize() - pd.Timedelta(days=1)).dt.tz_localize("UTC")
    day1_run = (forecast["valid_at"] - pd.Timedelta(days=1)).dt.floor("6h")
    use_day1 = day1_run <= latest_run
    if lead_days is not None:
        use_day1[:] = lead_days == 1
    result = forecast[["station_id", "valid_at"]].copy()
    for variable in VARIABLES:
        result[variable] = forecast[f"{variable}_previous_day1"].where(
            use_day1, forecast[f"{variable}_previous_day2"]
        )
    return result


def score(forecast: pd.DataFrame, observed: pd.DataFrame, source: str) -> list[dict]:
    rows = []
    for variable, column in VARIABLES.items():
        hours = forecast[["station_id", "valid_at", variable]].copy()
        # Radiation at T is the mean over the hour before T; observations are labelled by start.
        shift = pd.Timedelta(hours=1 if variable == "shortwave_radiation" else 0)
        hours["hour_start_utc"] = hours["valid_at"] - shift
        matched = hours.merge(observed, on=["station_id", "hour_start_utc"])
        matched = matched.dropna(subset=[variable, column])
        national = matched.groupby("hour_start_utc")[[variable, column]].mean()
        error = national[variable] - national[column]
        rows.append(
            {
                "source": source,
                "variable": variable,
                "hours": len(national),
                "mae": round(error.abs().mean(), 2),
                "bias": round(error.mean(), 2),
                "corr": round(national[variable].corr(national[column]), 3),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-03-14")  # first ECMWF HRES single run
    parser.add_argument("--end", default="2026-10-20")
    parser.add_argument("--sample-days", type=int, default=40)
    args = parser.parse_args()

    con = duckdb.connect(str(ROOT / "data" / "warehouse.duckdb"), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    stations = coordinates(con)
    observed = con.sql(
        f"""
        select station_id, hour_start_utc, {", ".join(VARIABLES.values())}
        from intermediate.int_weather_hourly_station
        where hour_start_utc >= '{args.start}'
        """
    ).df()
    observed["hour_start_utc"] = pd.to_datetime(observed["hour_start_utc"], utc=True)
    where = (
        f"latitude={','.join(map(str, stations['latitude']))}"
        f"&longitude={','.join(map(str, stations['longitude']))}"
        "&timezone=UTC&wind_speed_unit=ms"
    )

    rows, forecasts = [], {}
    for model in MODELS:
        hourly = ",".join(f"{v}_previous_day{n}" for v in VARIABLES for n in (1, 2))
        url = (
            "https://previous-runs-api.open-meteo.com/v1/forecast?"
            f"{where}&models={model}&start_date={args.start}&end_date={args.end}&hourly={hourly}"
        )
        raw = frame(get(url), stations)
        forecasts[model] = known_at_noon(raw)
        rows += score(forecasts[model], observed, f"{model}, previous runs")
        for lead_days in (1, 2):
            only = known_at_noon(raw, lead_days)
            rows += score(only, observed, f"{model}, previous_day{lead_days} only")
        print(f"{model}: {len(forecasts[model])} rows", file=sys.stderr)
        time.sleep(PAUSE_SECONDS)

    # Is the 00Z run of D (the freshest one available at noon, at every hour) much better?
    # Single Runs keeps ECMWF HRES from 2024-03-14, so compare on a sample of its days.
    random.seed(7)
    first, last = date.fromisoformat(args.start), date.fromisoformat(args.end)
    days = random.sample(range((last - first).days), args.sample_days)
    runs = []
    for offset in sorted(days):
        run = first + timedelta(days=offset)
        url = (
            "https://single-runs-api.open-meteo.com/v1/forecast?"
            f"{where}&models=ecmwf_ifs&run={run}T00:00&forecast_hours=54"
            f"&hourly={','.join(VARIABLES)}"
        )
        hours = frame(get(url), stations)
        delivery = hours["valid_at"].dt.tz_convert("Europe/Prague").dt.date
        runs.append(hours[delivery == run + timedelta(days=1)])
        time.sleep(4)
    sample = pd.concat(runs)
    keys = sample[["station_id", "valid_at"]]
    rows += score(sample, observed, "ecmwf_ifs 00Z of D, single runs (sample)")
    for model, forecast in forecasts.items():
        rows += score(forecast.merge(keys), observed, f"{model}, previous runs (sample)")

    table = pd.DataFrame(rows).sort_values(["variable", "source"])
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()

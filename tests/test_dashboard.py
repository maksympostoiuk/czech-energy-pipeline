"""Render every dashboard page headlessly and fail on any exception.

Runs against DASHBOARD_DUCKDB_PATH (CI: the empty warehouse from `dbt build --empty`,
which checks the pages handle "no data") or else the local data/warehouse.duckdb.
"""

import os
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from energy_pipeline.load.warehouse import PRICE_FORECAST, Warehouse

ROOT = Path(__file__).parent.parent
APP = ROOT / "dashboard" / "app.py"
DATABASE = os.environ.get("DASHBOARD_DUCKDB_PATH") or str(ROOT / "data" / "warehouse.duckdb")
PAGES = [
    "views/day_ahead.py",
    "views/forecast.py",
    "views/history.py",
    "views/weather.py",
    "views/quality.py",
]

pytestmark = pytest.mark.skipif(
    not Path(DATABASE).exists(), reason="no warehouse file to render the dashboard from"
)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("DASHBOARD_DUCKDB_PATH", DATABASE)
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    return AppTest.from_file(str(APP), default_timeout=60)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(app: AppTest, page: str) -> None:
    app.run()
    app.switch_page(page).run()

    assert not app.exception, [e.value for e in app.exception]
    assert app.title, "every page starts with a title"


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 10, 25), 25), (date(2026, 3, 29), 23)])
def test_forecast_hours_are_elapsed_hours_on_dst_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, day: date, hours: int
) -> None:
    """A wrong session time zone once made the autumn day read 0, 1, 3, 3, ..."""
    path = tmp_path / "warehouse.duckdb"
    with Warehouse(str(path)) as wh:
        wh.con.execute("create schema marts")
        wh.con.execute(
            "create table marts.fct_dam_prices_hourly (hour_start_utc timestamptz, "
            "price_eur_mwh double)"
        )
        start = datetime.combine(day, time(), ZoneInfo("Europe/Prague")).astimezone(UTC)
        records = [
            {
                "forecast_date": day - timedelta(days=1),
                "delivery_date": day,
                "hour_start_utc": start + timedelta(hours=n),
                "cutoff_at_utc": start - timedelta(hours=12),
                "model": "lightgbm",
                "model_version": "test",
                "code_version": "test",
                "run_type": "live",
                "predicted_price_eur_mwh": 100.0 + n,
            }
            for n in range(hours)
        ]
        wh.upsert(PRICE_FORECAST, records, "test")
    monkeypatch.setenv("DASHBOARD_DUCKDB_PATH", str(path))
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    monkeypatch.syspath_prepend(str(ROOT / "dashboard"))
    import data

    st.cache_resource.clear()
    st.cache_data.clear()
    try:
        result = data.forecast_hours(day)
    finally:
        st.cache_resource.clear()
        st.cache_data.clear()

    assert list(result["hour"]) == list(range(hours))
    assert list(result["forecast"]) == [100.0 + n for n in range(hours)]

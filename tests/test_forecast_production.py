from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from energy_pipeline.forecast import production
from energy_pipeline.forecast.features import TARGET
from energy_pipeline.load.warehouse import Warehouse
from test_forecast import FIRST_DAY, synthetic_frame

DAYS = 40
FORECAST_DATE = FIRST_DAY + timedelta(days=DAYS - 2)  # forecasts the last day of the frame
DELIVERY_DATE = FORECAST_DATE + timedelta(days=1)
FAST = {"n_estimators": 30}


@pytest.fixture
def frame() -> pd.DataFrame:
    """Feature-mart-shaped rows; the last delivery day has no published prices yet."""
    data = synthetic_frame(DAYS)
    data["cutoff_at_utc"] = [
        pd.Timestamp(day - timedelta(days=1), tz="UTC") + pd.Timedelta(hours=11)
        for day in data["delivery_date"]
    ]
    data["known_prices_through"] = data["forecast_date"]
    data["weather_latest_run_utc"] = data["cutoff_at_utc"] - pd.Timedelta(hours=11)
    data.loc[data["delivery_date"] == DELIVERY_DATE, TARGET] = None
    return data


@pytest.fixture(autouse=True)
def fast_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from energy_pipeline.forecast.models import LightGBMModel, week_ago, yesterday

    monkeypatch.setattr(
        production,
        "PRODUCTION_MODELS",
        (
            lambda: LightGBMModel(
                "weather_forecast", quantiles=True, calibration_days=7, params=FAST
            ),
            yesterday,
            week_ago,
        ),
    )


@pytest.fixture
def warehouse() -> Iterator[Warehouse]:
    with Warehouse(":memory:") as wh:
        yield wh


def stored(warehouse: Warehouse) -> pd.DataFrame:
    return warehouse.con.execute("select * from ml.price_forecast order by model").df()


def test_predict_stores_every_model_once(warehouse: Warehouse, frame: pd.DataFrame) -> None:
    on_time = datetime.combine(FORECAST_DATE, datetime.min.time(), UTC) + timedelta(hours=6)

    written = production.predict(warehouse, frame, FORECAST_DATE, now=on_time)
    again = production.predict(warehouse, frame, FORECAST_DATE, now=on_time + timedelta(hours=2))
    rows = stored(warehouse)

    assert (written, again) == (3 * 24, 0)  # the backup run finds the forecast and stops
    assert set(rows["model"]) == {"lightgbm", "benchmark_yesterday", "benchmark_week_ago"}
    assert (rows["delivery_date"] == pd.Timestamp(DELIVERY_DATE)).all()
    assert not rows["is_late"].any()
    assert (rows["trained_through"] == pd.Timestamp(FORECAST_DATE)).all()
    assert (rows["run_type"] == "live").all()
    assert (rows["model_version"] == production.MODEL_VERSION).all()
    lightgbm = rows[rows["model"] == "lightgbm"]
    assert lightgbm["p10_eur_mwh"].notna().all()
    assert (lightgbm["p10_eur_mwh"] <= lightgbm["p90_eur_mwh"]).all()
    assert rows.loc[rows["model"] != "lightgbm", "p10_eur_mwh"].isna().all()


def test_a_late_forecast_is_flagged_and_force_replaces(
    warehouse: Warehouse, frame: pd.DataFrame
) -> None:
    late = datetime.combine(FORECAST_DATE, datetime.min.time(), UTC) + timedelta(hours=15)

    production.predict(warehouse, frame, FORECAST_DATE, now=late - timedelta(hours=10))
    production.predict(warehouse, frame, FORECAST_DATE, now=late, force=True)
    rows = stored(warehouse)

    assert len(rows) == 3 * 24
    assert rows["is_late"].all()


@pytest.mark.parametrize(
    ("breakage", "message"),
    [
        (lambda f: f.drop(f[f["delivery_date"] == DELIVERY_DATE].index[:3]), "feature rows"),
        (lambda f: f[f["delivery_date"] != DELIVERY_DATE], "no feature rows"),
        (
            lambda f: f.assign(known_prices_through=f["known_prices_through"] - timedelta(days=1)),
            "prices of",
        ),
        (
            lambda f: f.assign(
                de_wind_100m_ms=f["de_wind_100m_ms"].where(f["delivery_date"] != DELIVERY_DATE)
            ),
            "de_wind_100m_ms",
        ),
    ],
)
def test_incomplete_inputs_fail_loudly(
    warehouse: Warehouse, frame: pd.DataFrame, breakage, message: str
) -> None:
    with pytest.raises(production.ForecastNotPossible, match=message):
        production.predict(warehouse, breakage(frame), FORECAST_DATE)
    assert stored(warehouse).empty


def test_backfill_history_stores_backtest_rows(warehouse: Warehouse, frame: pd.DataFrame) -> None:
    start = FIRST_DAY + timedelta(days=35)

    production.backfill_history(warehouse, frame, start)
    rows = stored(warehouse)

    days = sorted(rows["delivery_date"].dt.date.unique())
    assert days == [start + timedelta(days=n) for n in range(4)]  # the last day has no prices
    assert (rows["run_type"] == "backtest").all()
    assert rows["issued_at"].isna().all()
    assert (rows["trained_through"] == rows["delivery_date"] - pd.Timedelta(days=1)).all()
    assert len(rows) == 3 * 4 * 24


def test_code_version_comes_from_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef")
    assert production.code_version() == "0123456"
    monkeypatch.delenv("GITHUB_SHA")
    assert production.code_version() == "local"

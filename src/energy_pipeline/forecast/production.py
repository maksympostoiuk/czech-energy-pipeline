"""The daily production forecast, stored in ml.price_forecast.

Every morning `forecast predict` trains the chosen model on every delivery day whose prices
are known on D and forecasts the hours of D+1 from ml.ml_features_hourly, whose rows hold
what was known at the cutoff (12:00 Prague time on D) however late the job runs. Training
takes seconds, so the model is refitted every day, exactly as in the backtest.

A run that finds a forecast for D+1 already stored stops: the backup schedule never
replaces an on-time forecast with a later one. `is_late` records a forecast issued after
the cutoff, which would have been too late for the auction although it uses no later data.
"""

import logging
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from energy_pipeline.forecast.backtest import Period, delivery_days, training_rows, walk_forward
from energy_pipeline.forecast.features import TARGET
from energy_pipeline.forecast.models import LightGBMModel, Model, week_ago, yesterday
from energy_pipeline.load.warehouse import PRICE_FORECAST, Warehouse

log = logging.getLogger(__name__)

# Bump when the model or its settings change; code_version records the exact commit.
MODEL_VERSION = "lightgbm-l1-500-cqr28-v1"
MAIN_MODEL = "lightgbm"
PRODUCTION_MODELS: tuple[Callable[[], Model], ...] = (
    lambda: LightGBMModel("weather_forecast", quantiles=True),
    yesterday,
    week_ago,
)
# Without these the model would silently fall back to calendar and prices only.
REQUIRED_WEATHER = [
    "cz_temperature_c",
    "cz_radiation_wm2",
    "cz_wind_10m_ms",
    "de_solar_radiation_wm2",
    "de_wind_100m_ms",
]
FEATURE_COLUMNS = [
    "hour_start_utc",
    "forecast_date",
    "delivery_date",
    "cutoff_at_utc",
    "weather_latest_run_utc",
]


class ForecastNotPossible(RuntimeError):
    """The inputs for D+1 are incomplete; no forecast is better than a silently worse one."""


def code_version() -> str:
    return os.environ.get("GITHUB_SHA", "local")[:7]


def inputs_for(frame: pd.DataFrame, delivery_date: date) -> pd.DataFrame:
    """The feature rows of `delivery_date`, checked to be complete."""
    forecast_date = delivery_date - timedelta(days=1)
    rows = frame[frame["delivery_date"] == delivery_date]
    if rows.empty:
        raise ForecastNotPossible(f"no feature rows for {delivery_date}: are D's prices loaded?")
    if len(rows) != rows["hours_in_day"].iloc[0]:
        raise ForecastNotPossible(f"{len(rows)} feature rows for {delivery_date}")
    if (rows["known_prices_through"] != forecast_date).any():
        raise ForecastNotPossible(f"prices of {forecast_date} are not in the features")
    missing = [column for column in REQUIRED_WEATHER if rows[column].isna().any()]
    if missing:
        raise ForecastNotPossible(f"weather forecast missing for {delivery_date}: {missing}")
    return rows


def _optional(value) -> float | None:
    return None if pd.isna(value) else float(value)


def _records(
    hours: pd.DataFrame, model: str, run_type: str, issued_at: datetime | None
) -> list[dict]:
    """Rows of ml.price_forecast from hours with FEATURE_COLUMNS, the prediction columns,
    trained_through and training_hours."""
    records = []
    for hour in hours.itertuples(index=False):
        cutoff = hour.cutoff_at_utc.to_pydatetime()
        records.append(
            {
                "forecast_date": hour.forecast_date,
                "delivery_date": hour.delivery_date,
                "hour_start_utc": hour.hour_start_utc.to_pydatetime(),
                "cutoff_at_utc": cutoff,
                "model": model,
                "model_version": MODEL_VERSION,
                "code_version": code_version(),
                "run_type": run_type,
                "predicted_price_eur_mwh": float(hour.prediction),
                "p10_eur_mwh": _optional(getattr(hour, "p10", None)),
                "p50_eur_mwh": _optional(getattr(hour, "p50", None)),
                "p90_eur_mwh": _optional(getattr(hour, "p90", None)),
                "issued_at": issued_at,
                "is_late": None if issued_at is None else issued_at > cutoff,
                "trained_through": hour.trained_through,
                "training_hours": int(hour.training_hours),
                "weather_latest_run_utc": hour.weather_latest_run_utc.to_pydatetime(),
            }
        )
    return records


def forecast(frame: pd.DataFrame, forecast_date: date, issued_at: datetime) -> list[dict]:
    """Rows of ml.price_forecast for delivery day forecast_date + 1, every production model."""
    delivery_date = forecast_date + timedelta(days=1)
    target = inputs_for(frame, delivery_date)
    train = training_rows(frame, delivery_date)
    records = []
    for make_model in PRODUCTION_MODELS:
        model = make_model()
        model.fit(train)
        hours = target[FEATURE_COLUMNS].join(model.predict(target))
        hours["trained_through"] = train["delivery_date"].max()
        hours["training_hours"] = len(train)
        records += _records(hours, model.name, "live", issued_at)
    return records


def already_forecast(warehouse: Warehouse, delivery_date: date) -> bool:
    found = warehouse.con.execute(
        "select count(*) from ml.price_forecast "
        "where delivery_date = ? and model = ? and run_type = 'live'",
        [delivery_date, MAIN_MODEL],
    ).fetchone()[0]
    return found > 0


def predict(
    warehouse: Warehouse,
    frame: pd.DataFrame,
    forecast_date: date,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> int:
    """Store the forecast for forecast_date + 1; returns the rows written (0: already there)."""
    delivery_date = forecast_date + timedelta(days=1)
    if not force and already_forecast(warehouse, delivery_date):
        log.info("forecast for %s already stored, nothing to do", delivery_date)
        return 0
    records = forecast(frame, forecast_date, now or datetime.now(UTC))
    written = warehouse.upsert(PRICE_FORECAST, records, f"forecast-{code_version()}")
    late = " (issued after the cutoff)" if records[0]["is_late"] else ""
    log.info("stored %d rows for %s%s", written, delivery_date, late)
    return written


def backfill_history(warehouse: Warehouse, frame: pd.DataFrame, start: date) -> int:
    """Walk-forward forecasts from `start` (run_type 'backtest'), so the dashboard has a track
    record before live forecasts accumulate. Same models, refitted every day."""
    days = delivery_days(frame, Period(start))
    known = frame[frame[TARGET].notna()]
    training_hours = {day: int((known["delivery_date"] < day).sum()) for day in days}
    written = 0
    for make_model in PRODUCTION_MODELS:
        predictions = walk_forward(frame, make_model, days, variant="history")
        hours = predictions[["hour_start_utc", "prediction", *_quantile_columns(predictions)]]
        hours = hours.merge(frame[FEATURE_COLUMNS], on="hour_start_utc")
        hours["trained_through"] = hours["delivery_date"] - timedelta(days=1)
        hours["training_hours"] = hours["delivery_date"].map(training_hours)
        records = _records(hours, make_model().name, "backtest", None)
        written += warehouse.upsert(PRICE_FORECAST, records, f"backfill-{code_version()}")
        log.info("%s: %d backtest rows from %s", make_model().name, len(records), start)
    return written


def _quantile_columns(predictions: pd.DataFrame) -> list[str]:
    return [c for c in ("p10", "p50", "p90") if c in predictions.columns]

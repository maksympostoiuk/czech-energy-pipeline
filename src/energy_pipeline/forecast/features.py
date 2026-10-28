"""The feature mart as a DataFrame, and the feature sets the models are compared on.

Every column of ml.ml_features_hourly is known at the forecast cutoff, 12:00 Prague time
on the day before delivery (see the model's SQL and tests/test_ml_features_leakage.py).
The observed weather comes from ml.ml_oracle_weather_hourly and is used only by the
"weather_observed" variant, the upper bound of what a perfect weather forecast could add.
"""

from typing import Literal

import duckdb
import pandas as pd

TARGET = "price_eur_mwh"
Variant = Literal["no_weather", "weather_forecast", "weather_observed"]
VARIANTS: tuple[Variant, ...] = ("no_weather", "weather_forecast", "weather_observed")

CALENDAR = [
    "local_hour",
    "hour_index",
    "hours_in_day",
    "is_peak",
    "day_of_week",
    "is_weekend",
    "is_holiday",
    "is_business_day",
    "is_next_day_business_day",
    "is_previous_day_business_day",
    "month",
    "day_of_year",
]
PRICES = [
    "price_same_hour_d",
    "price_same_hour_week_ago",
    "price_same_hour_mean_7d",
    "price_same_hour_min_7d",
    "price_same_hour_max_7d",
    "base_d",
    "peak_d",
    "offpeak_d",
    "min_price_d",
    "max_price_d",
    "std_price_d",
    "negative_hours_d",
    "base_d_minus_1",
    "base_mean_7d",
]
WEATHER = [
    "cz_temperature_c",
    "cz_radiation_wm2",
    "cz_wind_10m_ms",
    "cz_wind_100m_ms",
    "cz_cloud_cover_pct",
    "de_solar_radiation_wm2",
    "de_wind_100m_ms",
    "cz_temperature_day_mean_c",
    "cz_radiation_day_mean_wm2",
    "de_solar_radiation_day_mean_wm2",
    "de_wind_100m_day_mean_ms",
]
# Czech forecast columns the observed-weather variant replaces with ČHMÚ observations.
# German regions and the 100 m wind / cloud cover have no observations and stay forecasts.
OBSERVED = ["cz_temperature_c", "cz_radiation_wm2", "cz_wind_10m_ms"]
OBSERVED_DAY_MEANS = {
    "cz_temperature_day_mean_c": "cz_temperature_c",
    "cz_radiation_day_mean_wm2": "cz_radiation_wm2",
}
# Kept next to the features for evaluation, never fed to a model.
KEYS = ["hour_start_utc", "delivery_date", "forecast_date", "season"]


def feature_columns(variant: Variant) -> list[str]:
    return CALENDAR + PRICES + ([] if variant == "no_weather" else WEATHER)


def load(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Features of every delivery hour, with the observed Czech weather as observed_*."""
    con.execute("SET TimeZone = 'UTC'")
    frame = con.sql(
        f"""
        select
            features.*,
            {", ".join(f"observed.{c} as observed_{c}" for c in OBSERVED)}
        from ml.ml_features_hourly as features
        left join ml.ml_oracle_weather_hourly as observed using (hour_start_utc)
        order by features.hour_start_utc
        """
    ).df()
    frame["delivery_date"] = pd.to_datetime(frame["delivery_date"]).dt.date
    frame["forecast_date"] = pd.to_datetime(frame["forecast_date"]).dt.date
    return frame


def design(frame: pd.DataFrame, variant: Variant) -> pd.DataFrame:
    """The model inputs of one variant, all numeric (booleans as 0/1, missing as NaN)."""
    data = frame.copy()
    if variant == "weather_observed":
        for column in OBSERVED:
            data[column] = data[f"observed_{column}"]
        for day_mean, column in OBSERVED_DAY_MEANS.items():
            data[day_mean] = data.groupby("delivery_date")[column].transform("mean")
    return data[feature_columns(variant)].astype("float64")

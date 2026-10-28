from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from energy_pipeline.forecast import features, metrics
from energy_pipeline.forecast.backtest import Period, delivery_days, training_rows, walk_forward
from energy_pipeline.forecast.features import TARGET
from energy_pipeline.forecast.models import LightGBMModel, RidgeModel, week_ago, yesterday

FIRST_DAY = date(2026, 1, 1)


def synthetic_frame(days: int = 40, seed: int = 0) -> pd.DataFrame:
    """Feature-mart-shaped rows: 24 hours a day, a price that depends on hour and radiation."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(days):
        day = FIRST_DAY + timedelta(days=d)
        for hour in range(24):
            radiation = max(0.0, 600 * np.sin((hour - 6) / 12 * np.pi)) * rng.uniform(0.5, 1)
            rows.append(
                {
                    "hour_start_utc": pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=hour),
                    "delivery_date": day,
                    "forecast_date": day - timedelta(days=1),
                    "season": "winter",
                    "local_hour": hour,
                    "hour_index": hour,
                    "hours_in_day": 24,
                    "is_peak": 8 <= hour < 20,
                    "day_of_week": day.isoweekday(),
                    "is_weekend": day.isoweekday() >= 6,
                    "is_holiday": False,
                    "is_business_day": day.isoweekday() < 6,
                    "is_next_day_business_day": True,
                    "is_previous_day_business_day": True,
                    "month": day.month,
                    "day_of_year": day.timetuple().tm_yday,
                    **{column: rng.normal(100, 20) for column in features.PRICES},
                    **{column: rng.normal(10, 3) for column in features.WEATHER},
                    "cz_radiation_wm2": radiation,
                    TARGET: 120 + 3 * hour - 0.1 * radiation + rng.normal(0, 2),
                    **{f"observed_{c}": rng.normal(10, 3) for c in features.OBSERVED},
                }
            )
    return pd.DataFrame(rows)


@dataclass
class SpyModel:
    """Records what each fit saw, predicts zero."""

    name: str = "spy"
    fits: list = field(default_factory=list)

    def fit(self, frame: pd.DataFrame) -> None:
        self.fits.append(frame["delivery_date"].max())

    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        last_training_day = self.fits[-1]
        return pd.DataFrame(
            {"prediction": 0.0, "trained_through": last_training_day}, index=frame.index
        )


def test_training_rows_stop_at_the_forecast_date() -> None:
    frame = synthetic_frame(10)
    day = FIRST_DAY + timedelta(days=5)

    rows = training_rows(frame, day)

    assert rows["delivery_date"].max() == day - timedelta(days=1)
    with pytest.raises(ValueError, match="no valid training data"):
        training_rows(frame, FIRST_DAY)


def test_training_rows_skip_unpublished_prices() -> None:
    frame = synthetic_frame(10)
    day = FIRST_DAY + timedelta(days=5)
    frame.loc[frame["delivery_date"] == day - timedelta(days=1), TARGET] = None

    assert training_rows(frame, day)["delivery_date"].max() == day - timedelta(days=2)


@pytest.mark.parametrize("refit_every", [1, 3])
def test_walk_forward_never_trains_on_the_day_it_predicts(refit_every: int) -> None:
    frame = synthetic_frame(20)
    days = delivery_days(frame, Period(FIRST_DAY + timedelta(days=10)))

    predictions = walk_forward(
        frame, SpyModel, days, variant="test", refit_every=refit_every, n_jobs=1
    )

    assert sorted(predictions["delivery_date"].unique()) == days
    assert (predictions["trained_through"] < predictions["delivery_date"]).all()
    staleness = predictions["delivery_date"] - predictions["trained_through"]
    assert staleness.max() == timedelta(days=refit_every)


def test_delivery_days_skip_days_without_all_prices() -> None:
    frame = synthetic_frame(5)
    frame.loc[frame.index[-1], TARGET] = None  # the last hour of the last day

    days = delivery_days(frame, Period(FIRST_DAY, FIRST_DAY + timedelta(days=10)))

    assert days == [FIRST_DAY + timedelta(days=n) for n in range(4)]


def test_benchmarks_repeat_a_known_price_and_fall_back_to_the_base() -> None:
    frame = synthetic_frame(2)
    frame.loc[frame.index[2], "price_same_hour_d"] = None  # e.g. the spring DST 02:00

    predicted = yesterday().predict(frame)["prediction"]

    assert predicted.iloc[0] == frame["price_same_hour_d"].iloc[0]
    assert predicted.iloc[2] == frame["base_d"].iloc[2]
    assert (week_ago().predict(frame)["prediction"] == frame["price_same_hour_week_ago"]).all()


def test_variants_differ_only_in_weather() -> None:
    frame = synthetic_frame(3)

    no_weather = features.design(frame, "no_weather")
    forecast = features.design(frame, "weather_forecast")
    observed = features.design(frame, "weather_observed")

    assert not set(features.WEATHER) & set(no_weather.columns)
    assert list(forecast.columns) == list(observed.columns)
    assert (observed["cz_temperature_c"] == frame["observed_cz_temperature_c"]).all()
    assert (forecast["cz_temperature_c"] == frame["cz_temperature_c"]).all()
    day_mean = frame.groupby("delivery_date")["observed_cz_temperature_c"].transform("mean")
    assert np.allclose(observed["cz_temperature_day_mean_c"], day_mean)
    assert (observed["de_wind_100m_ms"] == forecast["de_wind_100m_ms"]).all()


@pytest.mark.parametrize("make_model", [RidgeModel, LightGBMModel])
def test_models_learn_the_signal(make_model) -> None:
    frame = synthetic_frame(40)
    train = frame[frame["delivery_date"] < FIRST_DAY + timedelta(days=35)]
    test = frame[frame["delivery_date"] >= FIRST_DAY + timedelta(days=35)]

    model = make_model("weather_forecast")
    model.fit(train)
    error = metrics.mae(test[TARGET], model.predict(test)["prediction"])

    assert error < metrics.mae(test[TARGET], yesterday().predict(test)["prediction"]) / 3


def test_quantiles_are_ordered_and_contributions_add_up() -> None:
    frame = synthetic_frame(30)
    model = LightGBMModel("weather_forecast", quantiles=True, params={"n_estimators": 50})
    model.fit(frame)

    predicted = model.predict(frame)
    contributions = model.contributions(frame)

    assert (predicted["p10"] <= predicted["p50"]).all()
    assert (predicted["p50"] <= predicted["p90"]).all()
    assert np.allclose(contributions.sum(axis=1), predicted["prediction"])


def test_calibration_widens_an_overconfident_interval() -> None:
    frame = synthetic_frame(40, seed=1)
    frame[TARGET] += np.random.default_rng(2).normal(0, 30, len(frame))  # noise to cover
    train = frame[frame["delivery_date"] < FIRST_DAY + timedelta(days=30)]
    test = frame[frame["delivery_date"] >= FIRST_DAY + timedelta(days=30)]
    params = {"n_estimators": 200, "min_child_samples": 5}

    def coverage(calibration_days: int) -> float:
        model = LightGBMModel(
            "weather_forecast", quantiles=True, calibration_days=calibration_days, params=params
        )
        model.fit(train)
        predicted = model.predict(test)
        assert (predicted["p10"] <= predicted["p50"]).all()
        assert (predicted["p50"] <= predicted["p90"]).all()
        return metrics.coverage(test[TARGET], predicted["p10"], predicted["p90"])

    raw, calibrated = coverage(0), coverage(7)

    assert raw < 0.7  # small leaves overfit: the raw interval is too narrow
    assert calibrated > raw
    assert calibrated == pytest.approx(0.8, abs=0.1)


def test_metrics() -> None:
    actual = pd.Series([0.0, 10.0, -10.0, 100.0])
    predicted = pd.Series([10.0, 10.0, 0.0, 80.0])

    assert metrics.mae(actual, predicted) == 10.0
    assert metrics.rmse(actual, predicted) == pytest.approx(np.sqrt((100 + 0 + 100 + 400) / 4))
    assert metrics.pinball(actual, predicted, 0.9) == pytest.approx(
        (0.1 * 10 + 0 + 0.1 * 10 + 0.9 * 20) / 4
    )
    assert metrics.coverage(actual, actual - 1, pd.Series([0.0, 5.0, 0.0, 200.0])) == 0.75

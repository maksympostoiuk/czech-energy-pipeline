import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energy_pipeline.forecast import report

DASHBOARD_CHARTS = Path(__file__).parent.parent / "dashboard" / "charts.py"
FIRST_DAY = date(2026, 3, 1)
MODELS = [
    ("benchmark_yesterday", "benchmark", 30.0),
    ("benchmark_week_ago", "benchmark", 40.0),
    ("ridge", "weather_forecast", 25.0),
    ("lightgbm", "no_weather", 24.0),
    ("lightgbm", "weather_forecast", 20.0),
    ("lightgbm", "weather_observed", 19.0),
    ("ridge", "no_weather", 27.0),
    ("ridge", "weather_observed", 24.5),
]


@pytest.fixture
def predictions() -> pd.DataFrame:
    """Backtest-shaped output: every model misses every hour by a fixed amount."""
    rng = np.random.default_rng(0)
    hours = pd.DataFrame(
        [
            {
                "hour_start_utc": pd.Timestamp(FIRST_DAY, tz="UTC") + pd.Timedelta(hours=h),
                "delivery_date": FIRST_DAY + timedelta(days=h // 24),
                "forecast_date": FIRST_DAY + timedelta(days=h // 24 - 1),
                "season": "spring",
                "local_hour": h % 24,
                "is_peak": 8 <= h % 24 < 20,
                "price_eur_mwh": rng.normal(100, 30),
            }
            for h in range(24 * 20)
        ]
    )
    parts = []
    for model, variant, error in MODELS:
        part = hours.assign(model=model, variant=variant, prediction=hours["price_eur_mwh"] + error)
        if (model, variant) == report.MAIN:
            part = part.assign(
                p10=part["prediction"] - 50, p50=part["prediction"], p90=part["prediction"] + 50
            )
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def test_palette_matches_the_dashboard() -> None:
    source = DASHBOARD_CHARTS.read_text("utf-8")
    dashboard = dict(re.findall(r'^([A-Z_]+) = "(#[0-9a-f]{6})"', source, flags=re.MULTILINE))

    for name in ("BLUE", "ORANGE", "SURFACE", "TEXT", "TEXT_SECONDARY", "GRID"):
        assert getattr(report, name) == dashboard[name], name
    assert report.BAND in source  # a step of the dashboard's sequential ramp


def test_summary_tables(predictions: pd.DataFrame) -> None:
    summary = report.test_summary(predictions).set_index("Model")

    assert summary.loc["LightGBM, weather forecast", "MAE"] == pytest.approx(20)
    assert summary.loc["LightGBM, weather forecast", "MAE vs yesterday"] == -0.33  # rounded
    assert report.period_facts(predictions)["days"] == 20
    assert report.weather_value(predictions).loc["lightgbm", "weather_observed"] == 19
    assert report.by_group(predictions, "local_hour").shape == (24, 2)
    assert np.allclose(report.daily_mae(predictions)["LightGBM"], 20)
    assert report.interval_summary(predictions)["coverage_p10_p90"].iloc[0] == pytest.approx(1)


def test_every_chart_is_valid_vega_lite(predictions: pd.DataFrame) -> None:
    importance = pd.DataFrame(
        {"feature": ["a", "b"], "mean_abs_shap_eur_mwh": [2.0, 1.0], "gain_share": [0.6, 0.4]}
    )
    charts = [
        report.mae_by_model_chart(predictions),
        report.rolling_mae_chart(predictions),
        report.mae_by_hour_chart(predictions),
        report.forecast_vs_actual_chart(predictions, FIRST_DAY + timedelta(days=10)),
        report.importance_chart(importance),
    ]

    for chart in charts:
        json.dumps(chart.to_dict())  # fails on values Vega-Lite cannot take, e.g. dates

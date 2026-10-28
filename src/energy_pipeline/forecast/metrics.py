"""Error metrics. No MAPE: prices are often zero or negative (floor -500 EUR/MWh)."""

import numpy as np
import pandas as pd

from energy_pipeline.forecast.features import TARGET


def mae(actual: pd.Series, predicted: pd.Series) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def rmse(actual: pd.Series, predicted: pd.Series) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def pinball(actual: pd.Series, quantile: pd.Series, alpha: float) -> float:
    """Quantile loss: under-forecasts cost alpha, over-forecasts 1 - alpha, per EUR/MWh."""
    diff = actual - quantile
    return float(np.mean(np.maximum(alpha * diff, (alpha - 1) * diff)))


def coverage(actual: pd.Series, lower: pd.Series, upper: pd.Series) -> float:
    """Share of actual prices inside [lower, upper]; 0.8 is ideal for a p10-p90 interval."""
    return float(np.mean((actual >= lower) & (actual <= upper)))


def summarize(predictions: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    """MAE and RMSE per model (and per `by` groups), from backtest predictions."""
    keys = ["model", "variant", *(by or [])]
    rows = []
    for key, group in predictions.groupby(keys, sort=False, dropna=False):
        rows.append(
            {
                **dict(zip(keys, key, strict=True)),
                "hours": len(group),
                "mae": mae(group[TARGET], group["prediction"]),
                "rmse": rmse(group[TARGET], group["prediction"]),
            }
        )
    return pd.DataFrame(rows)


def summarize_intervals(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    with_quantiles = predictions.dropna(subset=["p10", "p50", "p90"])
    for (model, variant), group in with_quantiles.groupby(["model", "variant"], sort=False):
        actual = group[TARGET]
        rows.append(
            {
                "model": model,
                "variant": variant,
                "coverage_p10_p90": coverage(actual, group["p10"], group["p90"]),
                "mean_width": float((group["p90"] - group["p10"]).mean()),
                "pinball_p10": pinball(actual, group["p10"], 0.1),
                "pinball_p50": pinball(actual, group["p50"], 0.5),
                "pinball_p90": pinball(actual, group["p90"], 0.9),
            }
        )
    return pd.DataFrame(rows)

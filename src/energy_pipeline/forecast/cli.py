"""Command line entry point: `forecast`.

    forecast validate     compare candidate settings on the validation period
    forecast backtest     score the chosen models on the test period (all variants)
    forecast importance   SHAP feature importance of the LightGBM model

Results are written to data/forecast/ and printed. Both read the local warehouse
(data/warehouse.duckdb) after `dbt build`.
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd

from energy_pipeline.config import get_settings
from energy_pipeline.forecast import features, metrics
from energy_pipeline.forecast.backtest import (
    TEST,
    VALIDATION,
    Period,
    delivery_days,
    training_rows,
    walk_forward,
)
from energy_pipeline.forecast.models import (
    LIGHTGBM_PARAMS,
    LightGBMModel,
    Model,
    RidgeModel,
    week_ago,
    yesterday,
)

OUTPUT = Path("data/forecast")
log = logging.getLogger(__name__)

Candidate = tuple[str, Callable[[], Model]]


def validation_candidates() -> list[Candidate]:
    """Settings compared on the validation period, all with the weather forecast."""
    variant = "weather_forecast"
    return [
        ("benchmark", yesterday),
        ("benchmark", week_ago),
        *(
            (f"ridge alpha={alpha:g} relative={relative}", _ridge(variant, alpha, relative))
            for relative in (True, False)
            for alpha in (1.0, 10.0, 100.0, 1000.0, 10000.0)
        ),
        *(
            (f"lightgbm {objective} relative={relative}", _lightgbm(variant, objective, relative))
            for relative in (True, False)
            for objective in ("l1", "l2")
        ),
        (
            "lightgbm l1 relative=False trees=1000",
            _lightgbm(variant, "l1", False, n_estimators=1000),
        ),
        *(
            (
                f"lightgbm quantiles calibration_days={days}",
                lambda days=days: LightGBMModel(variant, quantiles=True, calibration_days=days),
            )
            for days in (0, 28)
        ),
    ]


def test_candidates() -> list[Candidate]:
    """The settings chosen on the validation period, for every weather variant."""
    candidates: list[Candidate] = [("benchmark", yesterday), ("benchmark", week_ago)]
    for variant in features.VARIANTS:
        candidates.append((variant, lambda variant=variant: RidgeModel(variant)))
        candidates.append((variant, lambda variant=variant: LightGBMModel(variant, quantiles=True)))
    return candidates


def _ridge(variant, alpha, relative) -> Callable[[], Model]:
    return lambda: RidgeModel(variant, alpha=alpha, relative=relative)


def _lightgbm(variant, objective, relative, quantiles=False, **params) -> Callable[[], Model]:
    settings = {**LIGHTGBM_PARAMS, **params}
    return lambda: LightGBMModel(
        variant, objective=objective, relative=relative, quantiles=quantiles, params=settings
    )


def load_frame() -> pd.DataFrame:
    with duckdb.connect(str(get_settings().duckdb_path), read_only=True) as con:
        return features.load(con)


def run(candidates: list[Candidate], period: Period, refit_every: int) -> pd.DataFrame:
    frame = load_frame()
    days = delivery_days(frame, period)
    log.info("%d delivery days %s..%s, refit every %d", len(days), days[0], days[-1], refit_every)
    parts = []
    for label, make_model in candidates:
        started = time.perf_counter()
        part = walk_forward(frame, make_model, days, variant=label, refit_every=refit_every)
        parts.append(part)
        log.info("%-40s %-22s %6.1f s", label, part["model"].iloc[0], time.perf_counter() - started)
    return pd.concat(parts, ignore_index=True)


def report(predictions: pd.DataFrame, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(OUTPUT / f"{name}_predictions.parquet", index=False)
    overall = metrics.summarize(predictions)
    by_peak = metrics.summarize(predictions, ["is_peak"]).pivot_table(
        index=["model", "variant"], columns="is_peak", values="mae", sort=False
    )
    overall = overall.merge(
        by_peak.rename(columns={True: "mae_peak", False: "mae_offpeak"}).reset_index(),
        on=["model", "variant"],
    )
    overall.to_csv(OUTPUT / f"{name}_metrics.csv", index=False)
    for by in ("season", "local_hour"):
        metrics.summarize(predictions, [by]).to_csv(OUTPUT / f"{name}_by_{by}.csv", index=False)
    pd.set_option("display.width", 160)
    print(overall.round(2).to_string(index=False))
    intervals = metrics.summarize_intervals(predictions) if "p10" in predictions else None
    if intervals is not None and not intervals.empty:
        intervals.to_csv(OUTPUT / f"{name}_intervals.csv", index=False)
        print(intervals.round(3).to_string(index=False))


def importance(period: Period = TEST) -> pd.DataFrame:
    """Mean absolute SHAP value per feature, in EUR/MWh: the LightGBM model with the weather
    forecast, trained on everything before the period and explaining the period's hours."""
    frame = load_frame()
    days = delivery_days(frame, period)
    model = LightGBMModel("weather_forecast")
    model.fit(training_rows(frame, days[0]))
    hours = frame[frame["delivery_date"].isin(days)]
    contributions = model.contributions(hours).drop(columns="expected_value")
    gain = model.gain()
    result = (
        contributions.abs()
        .mean()
        .rename("mean_abs_shap_eur_mwh")
        .to_frame()
        .join(gain)
        .sort_values("mean_abs_shap_eur_mwh", ascending=False)
        .rename_axis("feature")
        .reset_index()
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT / "importance.csv", index=False)
    print(result.round(2).to_string(index=False))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forecast", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="compare settings on the validation period")
    validate.add_argument("--refit-every", type=int, default=7)
    backtest = commands.add_parser("backtest", help="score the chosen models on the test period")
    backtest.add_argument("--refit-every", type=int, default=1)
    commands.add_parser("importance", help="SHAP feature importance on the test period")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.command == "validate":
        report(run(validation_candidates(), VALIDATION, args.refit_every), "validation")
    elif args.command == "backtest":
        report(run(test_candidates(), TEST, args.refit_every), "test")
    else:
        importance()
    return 0


if __name__ == "__main__":
    sys.exit(main())

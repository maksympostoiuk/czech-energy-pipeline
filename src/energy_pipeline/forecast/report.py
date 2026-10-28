"""Tables and charts of the forecasting report (forecasting/report.ipynb).

The notebook only calls these functions, so every number in it is computed here from the
backtest outputs in data/forecast/. `results()` re-runs `forecast validate`, `backtest`
and `importance` when the outputs are missing or when asked to refresh.
"""

from dataclasses import dataclass

import altair as alt
import pandas as pd

from energy_pipeline.forecast import cli, metrics
from energy_pipeline.forecast.backtest import TEST, VALIDATION
from energy_pipeline.forecast.features import TARGET

# The validated palette of dashboard/charts.py (tests/test_forecast_report.py keeps them equal).
BLUE = "#2a78d6"
ORANGE = "#eb6834"
BAND = "#cde2fb"  # first step of the dashboard's sequential blue ramp
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e9e8e4"

MAIN = ("lightgbm", "weather_forecast")
BENCHMARK = ("benchmark_yesterday", "benchmark")
LABELS = {
    ("benchmark_yesterday", "benchmark"): "Benchmark: same hour yesterday",
    ("benchmark_week_ago", "benchmark"): "Benchmark: same hour a week ago",
    ("ridge", "no_weather"): "Ridge, no weather",
    ("ridge", "weather_forecast"): "Ridge, weather forecast",
    ("ridge", "weather_observed"): "Ridge, observed weather (upper bound)",
    ("lightgbm", "no_weather"): "LightGBM, no weather",
    ("lightgbm", "weather_forecast"): "LightGBM, weather forecast",
    ("lightgbm", "weather_observed"): "LightGBM, observed weather (upper bound)",
}
SHORT = {MAIN: "LightGBM", BENCHMARK: "Same hour yesterday"}


@dataclass(frozen=True)
class Results:
    validation: pd.DataFrame  # metrics per candidate setting
    validation_intervals: pd.DataFrame
    test: pd.DataFrame  # one row per test hour and model
    importance: pd.DataFrame


def results(refresh: bool = False) -> Results:
    files = {
        "validation": cli.OUTPUT / "validation_metrics.csv",
        "validation_intervals": cli.OUTPUT / "validation_intervals.csv",
        "test": cli.OUTPUT / "test_predictions.parquet",
        "importance": cli.OUTPUT / "importance.csv",
    }
    if refresh or not all(path.exists() for path in files.values()):
        cli.report(cli.run(cli.validation_candidates(), VALIDATION, refit_every=7), "validation")
        cli.report(cli.run(cli.test_candidates(), TEST, refit_every=1), "test")
        cli.importance()
    return Results(
        validation=pd.read_csv(files["validation"]),
        validation_intervals=pd.read_csv(files["validation_intervals"]),
        test=pd.read_parquet(files["test"]),
        importance=pd.read_csv(files["importance"]),
    )


def _model(predictions: pd.DataFrame, key: tuple[str, str]) -> pd.DataFrame:
    model, variant = key
    return predictions[(predictions["model"] == model) & (predictions["variant"] == variant)]


def test_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    """MAE / RMSE per model, overall and for peak and off-peak hours."""
    overall = metrics.summarize(predictions)
    split = metrics.summarize(predictions, ["is_peak"]).pivot_table(
        index=["model", "variant"], columns="is_peak", values="mae", sort=False
    )
    table = overall.merge(split.reset_index(), on=["model", "variant"])
    benchmark = table.loc[
        (table["model"] == BENCHMARK[0]) & (table["variant"] == BENCHMARK[1]), "mae"
    ].iloc[0]
    return pd.DataFrame(
        {
            "Model": [
                LABELS[(m, v)] for m, v in zip(table["model"], table["variant"], strict=True)
            ],
            "MAE": table["mae"],
            "RMSE": table["rmse"],
            "MAE peak": table[True],
            "MAE off-peak": table[False],
            "MAE vs yesterday": table["mae"] / benchmark - 1,
        }
    ).round(2)


def period_facts(predictions: pd.DataFrame) -> dict[str, object]:
    main = _model(predictions, MAIN)
    return {
        "first_day": main["delivery_date"].min(),
        "last_day": main["delivery_date"].max(),
        "days": main["delivery_date"].nunique(),
        "hours": len(main),
    }


def daily_mae(predictions: pd.DataFrame) -> pd.DataFrame:
    """MAE per delivery day of the main model and the 'yesterday' benchmark."""
    columns = {}
    for key, name in SHORT.items():
        rows = _model(predictions, key)
        columns[name] = (
            (rows[TARGET] - rows["prediction"]).abs().groupby(rows["delivery_date"]).mean()
        )
    return pd.DataFrame(columns).rename_axis("delivery_date")


def by_group(predictions: pd.DataFrame, group: str) -> pd.DataFrame:
    """MAE of the main model and the 'yesterday' benchmark per season or local hour."""
    rows = []
    for key, name in SHORT.items():
        part = metrics.summarize(_model(predictions, key), [group])
        rows.append(part.assign(series=name))
    return pd.concat(rows).pivot_table(index=group, columns="series", values="mae", sort=False)


def interval_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    return metrics.summarize_intervals(_model(predictions, MAIN)).round(3)


def weather_value(predictions: pd.DataFrame) -> pd.DataFrame:
    """MAE per model and weather input: what the forecast adds, and what perfect weather would."""
    table = metrics.summarize(predictions)
    table = table[table["model"].isin(["ridge", "lightgbm"])]
    return table.pivot_table(index="model", columns="variant", values="mae", sort=False)[
        ["no_weather", "weather_forecast", "weather_observed"]
    ].round(2)


def _styled(chart: alt.TopLevelMixin, height: int = 280, width: int = 640) -> alt.TopLevelMixin:
    return (
        chart.properties(height=height, width=width, background=SURFACE)
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelColor=TEXT_SECONDARY,
            titleColor=TEXT_SECONDARY,
            titleFontWeight="normal",
            labelFontSize=12,
            titleFontSize=12,
            gridColor=GRID,
            domainColor=GRID,
            tickColor=GRID,
        )
        .configure_legend(orient="top", labelColor=TEXT_SECONDARY, titleColor=TEXT_SECONDARY)
        .configure_title(color=TEXT, fontSize=14, anchor="start", fontWeight="normal")
    )


TWO_SERIES = alt.Scale(domain=list(SHORT.values()), range=[BLUE, ORANGE])


def mae_by_model_chart(predictions: pd.DataFrame) -> alt.TopLevelMixin:
    table = test_summary(predictions)
    bars = (
        alt.Chart(table, title="Test period: mean absolute error by model (EUR/MWh)")
        .mark_bar(color=BLUE, cornerRadiusEnd=4, height=16)
        .encode(
            x=alt.X("MAE:Q", title="MAE, EUR/MWh"),
            y=alt.Y("Model:N", sort="x", title=None, axis=alt.Axis(labelLimit=320)),
        )
    )
    labels = bars.mark_text(align="left", dx=4, color=TEXT_SECONDARY).encode(
        text=alt.Text("MAE:Q", format=".1f")
    )
    return _styled(bars + labels, height=260)


def _lines(data: pd.DataFrame, x: alt.X, y_title: str, title: str) -> alt.TopLevelMixin:
    long = data.reset_index().melt(id_vars=data.index.name, var_name="series", value_name="mae")
    lines = (
        alt.Chart(long, title=title)
        .mark_line(strokeWidth=2)
        .encode(
            x=x,
            y=alt.Y("mae:Q", title=y_title),
            color=alt.Color("series:N", scale=TWO_SERIES, title=None),
        )
    )
    last = long[long[data.index.name] == long[data.index.name].max()]
    labels = (
        alt.Chart(last)
        .mark_text(align="left", dx=6, color=TEXT_SECONDARY)
        .encode(x=x, y="mae:Q", text="series:N")
    )
    return _styled(lines + labels)


def rolling_mae_chart(predictions: pd.DataFrame, window: int = 14) -> alt.TopLevelMixin:
    daily = daily_mae(predictions).rolling(window, min_periods=window).mean().dropna()
    daily.index = pd.to_datetime(daily.index)
    return _lines(
        daily,
        alt.X("delivery_date:T", title=None),
        "MAE, EUR/MWh",
        f"Daily MAE, {window}-day rolling mean",
    )


def mae_by_hour_chart(predictions: pd.DataFrame) -> alt.TopLevelMixin:
    return _lines(
        by_group(predictions, "local_hour"),
        alt.X("local_hour:Q", title="Hour of the delivery day (Prague time)"),
        "MAE, EUR/MWh",
        "Error by hour of the day",
    )


def forecast_vs_actual_chart(predictions: pd.DataFrame, first_day, days: int = 7):
    """Actual price, the forecast and its calibrated p10-p90 interval for a stretch of days."""
    rows = _model(predictions, MAIN)
    last_day = pd.Timestamp(first_day) + pd.Timedelta(days=days - 1)
    rows = rows[
        (pd.to_datetime(rows["delivery_date"]) >= pd.Timestamp(first_day))
        & (pd.to_datetime(rows["delivery_date"]) <= last_day)
    ].assign(time=lambda d: d["hour_start_utc"].dt.tz_convert("Europe/Prague").dt.tz_localize(None))
    series = pd.concat(
        [
            rows[["time"]].assign(series="Actual price", price=rows[TARGET]),
            rows[["time"]].assign(series="Forecast (LightGBM)", price=rows["prediction"]),
        ]
    )
    color = alt.Scale(domain=["Actual price", "Forecast (LightGBM)"], range=[TEXT_SECONDARY, BLUE])
    band = (
        alt.Chart(rows[["time", "p10", "p90"]])
        .mark_area(color=BAND, opacity=0.9)
        .encode(
            x=alt.X(
                "time:T",
                title=None,
                axis=alt.Axis(format="%a %d %b", tickCount="day", labelAngle=0),
            ),
            y="p10:Q",
            y2="p90:Q",
        )
    )
    lines = (
        alt.Chart(series)
        .mark_line(strokeWidth=2)
        .encode(
            x="time:T",
            y=alt.Y("price:Q", title="EUR/MWh"),
            color=alt.Color("series:N", scale=color, title=None),
        )
    )
    title = (
        f"Forecast vs actual, {pd.Timestamp(first_day):%d %b} - {last_day:%d %b %Y} "
        "(band: calibrated p10-p90)"
    )
    return _styled((band + lines).properties(title=title), height=300, width=760)


def importance_chart(importance: pd.DataFrame, top: int = 15) -> alt.TopLevelMixin:
    data = importance.head(top)
    bars = (
        alt.Chart(data, title=f"Top {top} features: mean |SHAP| on the test hours (EUR/MWh)")
        .mark_bar(color=BLUE, cornerRadiusEnd=4, height=14)
        .encode(
            x=alt.X("mean_abs_shap_eur_mwh:Q", title="Mean |SHAP|, EUR/MWh"),
            y=alt.Y("feature:N", sort="-x", title=None),
        )
    )
    labels = bars.mark_text(align="left", dx=4, color=TEXT_SECONDARY).encode(
        text=alt.Text("mean_abs_shap_eur_mwh:Q", format=".1f")
    )
    return _styled(bars + labels, height=360)

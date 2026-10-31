from datetime import timedelta

import pandas as pd
import streamlit as st

import charts
import data

ROLLING_DAYS = 14
MODEL_NAMES = {
    "lightgbm": "LightGBM",
    "benchmark_yesterday": "Same hour yesterday",
    "benchmark_week_ago": "Same hour a week ago",
}
REPO = "https://github.com/maksympostoiuk/czech-energy-pipeline/blob/main"

st.title("Forecast vs actual")
st.caption(
    "Every morning a LightGBM model forecasts each hour of the next delivery day, using only "
    "what is known when the auction closes at 12:00 Prague time: the calendar, published "
    "prices and weather forecasts already issued. "
    f"[Method and evaluation]({REPO}/forecasting/report.ipynb)."
)

days = data.forecast_days()
if days.empty:
    st.info("No price forecasts in the warehouse yet.")
    st.stop()


def origin_note(row: pd.Series) -> str:
    if row["run_type"] == "backtest":
        return "Walk-forward forecast, computed afterwards from data known at the cutoff."
    issued = pd.to_datetime(row["issued_at"], utc=True).tz_convert("Europe/Prague")
    timing = "after the 12:00 cutoff" if row["is_late"] else "before the 12:00 cutoff"
    return f"Issued {issued:%d %b %H:%M %Z}, {timing}."


def forecast_section(row: pd.Series) -> pd.DataFrame:
    day = row["delivery_date"].date()
    hours = data.forecast_hours(day)
    st.caption(f"{day:%A %d %B %Y}. {origin_note(row)} Band: calibrated p10–p90 interval.")
    charts.show(charts.forecast_vs_actual(hours), height=340)
    with st.expander("Table view"):
        columns = {
            "local_time": "Starts",
            "forecast": "Forecast",
            "p10": "p10",
            "p90": "p90",
            "actual": "Actual",
        }
        st.dataframe(
            hours[list(columns)].rename(columns=columns).round(1), hide_index=True, width="stretch"
        )
    return hours


latest = days.iloc[0]
st.subheader("Latest forecast")
hours = forecast_section(latest)
values = st.columns(4)
values[0].metric("Forecast base, EUR/MWh", f"{hours['forecast'].mean():,.1f}", border=True)
values[1].metric("Forecast min", f"{hours['forecast'].min():,.1f}", border=True)
values[2].metric("Forecast max", f"{hours['forecast'].max():,.1f}", border=True)
if hours["actual"].notna().all():
    error = (hours["forecast"] - hours["actual"]).abs().mean()
    values[3].metric("MAE against actual", f"{error:,.1f}", border=True)
else:
    values[3].metric("Actual price", "Not published yet", border=True)

previous = days[days["delivery_date"] == latest["delivery_date"] - timedelta(days=1)]
if not previous.empty:
    row = previous.iloc[0]
    st.subheader("The day before: forecast against the actual price")
    forecast_section(row)
    scores = data.forecast_day_scores(row["delivery_date"].date()).set_index("model")
    ordered = [model for model in MODEL_NAMES if model in scores.index]
    if ordered:
        for cell, model in zip(st.columns(len(ordered)), ordered, strict=True):
            mae = scores.at[model, "mae_eur_mwh"]
            cell.metric(f"MAE: {MODEL_NAMES[model]}", f"{mae:,.1f}", border=True)

st.subheader(f"Daily MAE, {ROLLING_DAYS}-day rolling mean")
daily = data.forecast_daily_mae()
if len(daily) < ROLLING_DAYS:
    st.info(f"The rolling error needs at least {ROLLING_DAYS} scored days.")
else:
    models = [model for model in MODEL_NAMES if model in daily.columns]
    rolling = daily[["delivery_date", *models]].copy()
    rolling[models] = rolling[models].rolling(ROLLING_DAYS).mean()
    rolling = rolling.dropna(subset=models)
    chart = charts.labelled_lines(
        rolling,
        x="delivery_date",
        x_title="Delivery day",
        y_title="MAE, EUR/MWh",
        series=[(model, MODEL_NAMES[model]) for model in models],
        y_format=",.1f",
    )
    live = daily.loc[daily["run_type"] == "live", "delivery_date"]
    if not live.empty and (daily["run_type"] == "backtest").any():
        chart = chart + charts.date_marker(live.min(), "Live forecasts start")
    charts.show(chart, height=320)
    st.caption(
        "Lower is better. Days before the first live forecast are walk-forward forecasts of the "
        "same models, each trained only on data published before that day."
    )

st.subheader("Accuracy")
if daily.empty:
    st.info("No forecast has been scored yet: OTE publishes prices around 13:00.")
    st.stop()
last_day = daily["delivery_date"].max()
periods = {
    "Last 7 days": ("delivery_date > ?", (last_day - timedelta(days=7),)),
    "Last 30 days": ("delivery_date > ?", (last_day - timedelta(days=30),)),
    "Live forecasts only": ("run_type = 'live'", ()),
    "Walk-forward history": ("run_type = 'backtest'", ()),
}
table = pd.concat(
    data.forecast_metrics(where, params).assign(period=period)
    for period, (where, params) in periods.items()
)
table["model"] = table["model"].map(MODEL_NAMES).fillna(table["model"])
st.dataframe(
    table[["period", "model", "days", "mae", "rmse", "mae_peak", "mae_offpeak", "coverage"]],
    column_config={
        "period": "Period",
        "model": "Model",
        "days": "Days",
        "mae": st.column_config.NumberColumn("MAE", format="%.1f"),
        "rmse": st.column_config.NumberColumn("RMSE", format="%.1f"),
        "mae_peak": st.column_config.NumberColumn("MAE peak", format="%.1f"),
        "mae_offpeak": st.column_config.NumberColumn("MAE off-peak", format="%.1f"),
        "coverage": st.column_config.NumberColumn("In p10–p90", format="percent"),
    },
    hide_index=True,
    width="stretch",
)
st.caption(
    "EUR/MWh. Peak = 08:00–20:00 local time. No percentage errors: prices are often zero or "
    "negative. The p10–p90 interval aims to contain 80 % of the hours. Weather data by "
    "[Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0)."
)

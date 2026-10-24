from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

import charts
import data

st.title("Price history")

dates = data.delivery_date_range()
if dates is None:
    st.info("No prices in the warehouse yet.")
    st.stop()
first_day, last_day = dates

controls = st.columns([2, 1, 2])
period = controls[0].date_input(
    "Period",
    value=(max(first_day, last_day - timedelta(days=365)), last_day),
    min_value=first_day,
    max_value=last_day,
    format="DD.MM.YYYY",
)
currency = controls[1].segmented_control("Currency", ["EUR", "CZK"], default="EUR") or "EUR"
if not isinstance(period, tuple) or len(period) != 2:
    st.info("Pick the end of the period.")
    st.stop()
start, end = period
unit = f"{currency}/MWh"
rate_col = "eur_czk"

daily = data.daily_summary(start, end)
if daily.empty:
    st.warning("No data in this period.")
    st.stop()
if currency == "CZK":
    for column in ("base_price_eur_mwh", "peak_price_eur_mwh"):
        daily[column] = daily[column] * daily[rate_col]

st.subheader("Daily base and peak price")
st.caption("Peak = 08:00–20:00 local time. Hover for the values of a day.")
charts.show(
    charts.labelled_lines(
        daily,
        x="delivery_date",
        x_title="Delivery day",
        y_title=unit,
        series=[("base_price_eur_mwh", "Base"), ("peak_price_eur_mwh", "Peak")],
        y_format=",.1f",
    ),
    height=340,
)

left, right = st.columns(2)
with left:
    st.subheader("Average price by weekday and hour")
    profile = data.weekday_hour_profile(start, end)
    value = "price_eur_mwh" if currency == "EUR" else "price_czk_mwh"
    charts.show(charts.heatmap(profile, value, unit), height=260)
    st.caption("Midday solar output pushes prices down; morning and evening ramps are the dearest.")
with right:
    st.subheader("Hours with a negative price, per month")
    monthly = data.monthly_summary()
    monthly["month_start"] = pd.to_datetime(
        dict(year=monthly["year"], month=monthly["month"], day=1)
    )
    charts.show(
        charts.single_bars(
            monthly,
            x="month_start",
            y="negative_price_hours",
            x_title="Month",
            y_title="Hours",
            color=charts.RED,
            y_format=",.0f",
        ),
        height=260,
    )
    st.caption("All loaded history. Negative prices cluster in sunny spring and summer weekends.")

st.subheader("Monthly average base price, year over year")
monthly = data.monthly_summary()
value = "base_price_eur_mwh" if currency == "EUR" else "base_price_czk_mwh"
wide = monthly.pivot(index="month", columns="year", values=value).reset_index()
years = [int(y) for y in monthly["year"].drop_duplicates().sort_values()][-3:]  # palette cap: 3
wide.columns = [str(c) for c in wide.columns]
charts.show(
    charts.labelled_lines(
        wide,
        x="month",
        x_title="Month",
        y_title=unit,
        series=[(str(y), str(y)) for y in years],
        x_type="O",
        y_format=",.1f",
        x_axis=alt.Axis(labelAngle=0, labelExpr=charts.MONTH_LABELS),
    ),
    height=300,
)
with st.expander("Table view"):
    st.dataframe(
        monthly.assign(**{value: monthly[value].round(1)})[
            ["year", "month", value, "negative_price_hours", "days"]
        ],
        hide_index=True,
        width="stretch",
    )

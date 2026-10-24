import pandas as pd
import streamlit as st

import charts
import data

st.title("Day-ahead electricity prices, Czech Republic")

dates = data.delivery_date_range()
if dates is None:
    st.info("No prices in the warehouse yet. Run the pipeline first.")
    st.stop()
first_day, last_day = dates

controls = st.columns([1, 1, 3])
day = controls[0].date_input(
    "Delivery day", value=last_day, min_value=first_day, max_value=last_day, format="DD.MM.YYYY"
)
currency = controls[1].segmented_control("Currency", ["EUR", "CZK"], default="EUR")
currency = currency or "EUR"
price_col = "price_eur_mwh" if currency == "EUR" else "price_czk_mwh"
unit = f"{currency}/MWh"

prices = data.day_prices(day)
summary = data.day_summary(day)
if prices.empty or summary.empty:
    st.warning(f"No prices for {day:%d %b %Y}.")
    st.stop()

today = summary.iloc[0]
previous = summary.iloc[1] if len(summary) > 1 else None
rate = 1.0 if currency == "EUR" else float(today["eur_czk"])
previous_rate = 1.0 if currency == "EUR" or previous is None else float(previous["eur_czk"])

notes = []
if today["periods"] in (23, 92):
    notes.append("23-hour day: clocks move forward, 02:00–03:00 does not exist.")
if today["periods"] in (25, 100):
    notes.append("25-hour day: clocks move back, 02:00–03:00 happens twice.")
if today["is_known_incomplete"]:
    notes.append("OTE published only part of this day.")
mtu = "15-minute" if today["mtu_minutes"] == 15 else "hourly"
st.caption(
    f"{day:%A %d %B %Y} · {mtu} market time unit · {int(today['periods'])} periods. "
    + " ".join(notes)
)


def metric(column, label: str, key: str) -> None:
    value = today[key] * rate
    delta = None
    if previous is not None and pd.notna(previous[key]):
        delta = f"{value - previous[key] * previous_rate:+,.1f}"
    # Neutral delta color: a higher price is not "good" or "bad" in itself.
    column.metric(label, f"{value:,.1f}", delta, delta_color="off", border=True)


st.caption(f"Prices in {unit}; arrows compare with the previous delivery day.")
cols = st.columns(6)
metric(cols[0], "Base", "base_price_eur_mwh")
metric(cols[1], "Peak 08–20", "peak_price_eur_mwh")
metric(cols[2], "Off-peak", "offpeak_price_eur_mwh")
cheapest = prices.loc[prices[price_col].idxmin()]
dearest = prices.loc[prices[price_col].idxmax()]
cols[3].metric(
    "Min",
    f"{cheapest[price_col]:,.1f}",
    f"at {cheapest['local_time']}",
    delta_color="off",
    delta_arrow="off",  # the delta slot carries a time here, not a change
    border=True,
)
cols[4].metric(
    "Max",
    f"{dearest[price_col]:,.1f}",
    f"at {dearest['local_time']}",
    delta_color="off",
    delta_arrow="off",
    border=True,
)
cols[5].metric("Hours < 0", f"{today['negative_price_hours']:g}", border=True)

st.subheader(f"Price per {mtu} interval")
charts.show(charts.price_bars(prices, price_col, unit), height=360)

with st.expander("Table view"):
    st.dataframe(
        prices[["local_time", "price_eur_mwh", "price_czk_mwh", "volume_mwh"]].rename(
            columns={
                "local_time": "Starts",
                "price_eur_mwh": "EUR/MWh",
                "price_czk_mwh": "CZK/MWh",
                "volume_mwh": "Volume MWh",
            }
        ),
        hide_index=True,
        width="stretch",
    )

st.caption(
    f"EUR/CZK {today['eur_czk']:.3f} (ČNB fixing; weekends and holidays use the last one). "
    "Base, peak and off-peak reproduce OTE's official daily indices."
)

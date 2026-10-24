import streamlit as st

import charts
import data

st.title("Price vs weather")
st.caption(
    "Hourly day-ahead prices next to the mean of 8 ČHMÚ stations across Czechia. "
    "Solar and wind push prices down; cold days push them up."
)

controls = st.columns([3, 2])
seasons = controls[0].pills(
    "Season", list(data.SEASONS), selection_mode="multi", default=list(data.SEASONS)
)
if not seasons:
    st.info("Pick at least one season.")
    st.stop()
seasons = tuple(seasons)

left, right = st.columns(2)
with left:
    st.subheader("Solar: price by global radiation")
    radiation = data.price_by_weather_bin("global_radiation_wm2", 50, seasons, (9, 16))
    if radiation.empty:
        st.info("Not enough data.")
    else:
        charts.show(
            charts.binned_profile(radiation, 50, "Global radiation W/m²", "EUR/MWh"), height=300
        )
        st.caption("Daytime hours 09:00–17:00. Line = mean price, band = middle 50% of hours.")
with right:
    st.subheader("Wind: price by wind speed")
    wind = data.price_by_weather_bin("wind_speed_ms", 0.5, seasons, (0, 23))
    if wind.empty:
        st.info("Not enough data.")
    else:
        charts.show(charts.binned_profile(wind, 0.5, "Wind speed m/s", "EUR/MWh"), height=300)
        st.caption("All hours. Mean 10-minute wind speed at 10 m, averaged over the stations.")

st.subheader("Daily base price vs temperature")
dates = data.delivery_date_range()
daily = data.daily_summary(*dates) if dates else None
if daily is not None:
    daily = daily[daily["season"].isin(seasons) & daily["avg_temperature_c"].notna()]
if daily is None or daily.empty:
    st.info("Not enough data.")
else:
    charts.show(
        charts.scatter_with_trend(
            daily, "avg_temperature_c", "base_price_eur_mwh", "Mean temperature °C", "Base EUR/MWh"
        ),
        height=320,
    )
    st.caption("One dot per day; the grey line is a LOESS trend.")

st.subheader("Correlation with the hourly price, by season")
corr = data.correlations_by_season()
corr = (
    corr[corr["season"].isin(seasons)]
    .set_index("season")
    .reindex([s for s in data.SEASONS if s in seasons])
)
st.dataframe(
    corr.rename(
        columns={
            "radiation_daytime": "Global radiation (daytime)",
            "wind": "Wind speed",
            "temperature": "Temperature",
            "hours": "Hours",
        }
    ).style.format("{:+.2f}", subset=["Global radiation (daytime)", "Wind speed", "Temperature"]),
    width="stretch",
)
st.caption("Pearson correlation; −1 = price falls as the measure rises, 0 = no linear relation.")

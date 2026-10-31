"""Czech day-ahead electricity prices: Streamlit entry point.

Run locally:  uv run streamlit run dashboard/app.py
"""

import streamlit as st

from data import source_label

st.set_page_config(page_title="Czech Energy Data", page_icon="⚡", layout="wide")

pages = st.navigation(
    [
        st.Page("views/day_ahead.py", title="Day-ahead prices", icon="⚡", default=True),
        st.Page("views/forecast.py", title="Forecast vs actual", icon="🎯"),
        st.Page("views/history.py", title="History", icon="📈"),
        st.Page("views/weather.py", title="Price vs weather", icon="🌤️"),
        st.Page("views/quality.py", title="Data quality", icon="✅"),
    ]
)

with st.sidebar:
    st.markdown(
        """
        **Czech Energy Data Pipeline**

        Daily ELT of the Czech electricity market: OTE day-ahead prices, ČNB EUR/CZK
        fixings, ČHMÚ weather observations and Open-Meteo forecasts, modelled and tested
        with dbt, and a price forecast for the next delivery day.

        [Source code](https://github.com/maksympostoiuk/czech-energy-pipeline) ·
        [dbt docs](https://maksympostoiuk.github.io/czech-energy-pipeline/)
        """
    )
    st.caption(
        "Data: OTE, a.s. · Česká národní banka · ČHMÚ open data · "
        "[Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0). "
        f"Read from {source_label()}, "
        "refreshed daily after OTE publishes (~13:00 CET)."
    )

pages.run()

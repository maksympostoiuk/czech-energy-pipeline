"""Czech day-ahead electricity prices: Streamlit entry point.

Run locally:  uv run streamlit run dashboard/app.py
"""

import streamlit as st

from data import source_label

st.set_page_config(page_title="Czech Energy Data", page_icon="⚡", layout="wide")

pages = st.navigation(
    [
        st.Page("views/day_ahead.py", title="Day-ahead prices", icon="⚡", default=True),
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
        fixings and ČHMÚ weather observations, modelled and tested with dbt.

        [Source code](https://github.com/maksympostoiuk/czech-energy-pipeline) ·
        [dbt docs](https://maksympostoiuk.github.io/czech-energy-pipeline/)
        """
    )
    st.caption(
        f"Data: OTE, a.s. · Česká národní banka · ČHMÚ open data. Read from {source_label()}, "
        "refreshed daily after OTE publishes (~13:00 CET)."
    )

pages.run()

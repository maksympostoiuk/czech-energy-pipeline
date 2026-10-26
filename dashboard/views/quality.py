import pandas as pd
import streamlit as st

import data

st.title("Data quality")
st.caption(
    "Every scheduled run loads the sources, rebuilds the dbt models and runs the data "
    "quality tests; the results land in the warehouse and are shown here."
)

SOURCE_NAMES = {
    "ote": "OTE day-ahead prices",
    "cnb": "ČNB exchange rates",
    "chmi": "ČHMÚ weather",
    "openmeteo": "Open-Meteo forecasts",
}
# Same thresholds as dbt source freshness: warn after 26 h, error after 50 h.
STATUS = [(26, "✅", "Fresh"), (50, "⚠️", "Late"), (float("inf"), "❌", "Stale")]

st.subheader("Sources")
status = data.load_status()
if status.empty:
    st.info("No load runs recorded yet.")
else:
    cols = st.columns(len(status))
    for col, row in zip(cols, status.itertuples(), strict=True):
        hours = row.hours_since_success
        if pd.isna(hours):
            icon, label = "❌", "Never loaded"
        else:
            icon, label = next((i, lbl) for limit, i, lbl in STATUS if hours < limit)
        with col.container(border=True):
            st.markdown(f"**{SOURCE_NAMES.get(row.source, row.source)}**")
            st.markdown(f"{icon} {label}")
            if pd.notna(hours):
                st.caption(
                    f"Last successful load {hours:,.1f} h ago · "
                    f"data up to {row.last_period_loaded:%d %b %Y}"
                )

st.subheader("Latest dbt run")
results = data.latest_dbt_results()
if results.empty:
    st.info("No dbt results recorded yet.")
else:
    build = results[results["resource_type"] != "source"]
    generated = build["generated_at"].max() if not build.empty else results["generated_at"].max()
    tests = build[build["resource_type"] == "test"]
    cols = st.columns(4)
    cols[0].metric("Tests passed", int((tests["status"] == "pass").sum()), border=True)
    cols[1].metric("⚠️ Warnings", int(tests["is_warning"].sum()), border=True)
    cols[2].metric("❌ Failures", int(tests["is_failure"].sum()), border=True)
    cols[3].metric(
        "Models and seeds built", int((build["resource_type"] != "test").sum()), border=True
    )
    st.caption(f"Recorded {pd.Timestamp(generated):%d %b %Y %H:%M} UTC.")

    flagged = results[results["is_failure"] | results["is_warning"]]
    if flagged.empty:
        st.success("All checks pass.", icon="✅")
    else:
        st.markdown("**Checks that need attention**")
        st.dataframe(
            flagged[["resource_type", "name", "status", "failures", "message"]],
            hide_index=True,
            width="stretch",
        )
    with st.expander("All checks of the latest run"):
        st.dataframe(
            results[["resource_type", "name", "status", "failures", "execution_time_s", "message"]],
            hide_index=True,
            width="stretch",
        )

st.subheader("Coverage")
st.dataframe(
    data.coverage(),
    hide_index=True,
    width="stretch",
    column_config={
        "dataset": "Dataset",
        "first_day": st.column_config.DateColumn("First day", format="DD.MM.YYYY"),
        "last_day": st.column_config.DateColumn("Last day", format="DD.MM.YYYY"),
        "rows": st.column_config.NumberColumn("Rows", format="localized"),
    },
)

st.subheader("Recent load tasks")
tasks = data.recent_tasks()
tasks["source"] = tasks["source"].map(SOURCE_NAMES).fillna(tasks["source"])
tasks["error"] = tasks["error"].fillna("")
st.dataframe(
    tasks,
    hide_index=True,
    width="stretch",
    column_config={
        "finished_at": st.column_config.DatetimeColumn("Finished (UTC)", format="DD.MM.YYYY HH:mm"),
        "source": "Source",
        "business_date": st.column_config.DateColumn("From", format="DD.MM.YYYY"),
        "period_end": st.column_config.DateColumn("To", format="DD.MM.YYYY"),
        "status": "Status",
        "rows_loaded": st.column_config.NumberColumn("Rows", format="localized"),
        "duration_s": st.column_config.NumberColumn("Seconds", format="%.1f"),
        "error": "Error",
    },
)

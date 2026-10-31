"""Read-only access to the warehouse marts.

Production (Streamlit Community Cloud): MotherDuck, with a token of its own in st.secrets.
Locally: the DuckDB file the pipeline writes (data/warehouse.duckdb), opened read-only.
"""

import os
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

LOCAL_DATABASE = Path(__file__).resolve().parent.parent / "data" / "warehouse.duckdb"
CACHE_SECONDS = 15 * 60  # the warehouse changes once a day; this keeps MotherDuck use low


def _setting(name: str) -> str | None:
    try:
        value = st.secrets.get(name)
    except FileNotFoundError:  # no secrets.toml at all, the normal local case
        value = None
    return value or os.environ.get(name)


@st.cache_resource
def _connection() -> duckdb.DuckDBPyConnection:
    token = _setting("MOTHERDUCK_TOKEN")
    if token:
        config = {"motherduck_token": token}
        if extension_directory := _setting("PIPELINE_DUCKDB_EXTENSION_DIRECTORY"):
            config["extension_directory"] = extension_directory
        database = _setting("MOTHERDUCK_DATABASE") or "czech_energy"
        con = duckdb.connect(f"md:{database}", config=config)
    else:
        path = _setting("DASHBOARD_DUCKDB_PATH") or str(LOCAL_DATABASE)
        con = duckdb.connect(path, read_only=True)
    return con


def source_label() -> str:
    return "MotherDuck" if _setting("MOTHERDUCK_TOKEN") else "local DuckDB file"


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    # A cursor per query: Streamlit serves sessions from several threads. A cursor does not
    # inherit session settings, so each one sets the time zone itself.
    cursor = _connection().cursor()
    cursor.execute("SET TimeZone = 'UTC'")
    return cursor.execute(sql, list(params)).df()


# --- Day-ahead prices -------------------------------------------------------------------


def delivery_date_range() -> tuple[date, date] | None:
    df = query(
        "select min(delivery_date) as first, max(delivery_date) as last from marts.fct_dam_prices"
    )
    if df.empty or pd.isna(df.at[0, "last"]):
        return None
    return df.at[0, "first"].date(), df.at[0, "last"].date()


def day_prices(day: date) -> pd.DataFrame:
    """One row per market time unit. Positions are elapsed hours since local midnight, so
    23- and 25-hour DST days plot correctly; the tooltip carries the real clock time."""
    df = query(
        """
        select
            period,
            mtu_minutes,
            (period - 1) * mtu_minutes / 60.0 as start_h,
            period * mtu_minutes / 60.0 as end_h,
            interval_start_utc,
            is_peak,
            price_eur_mwh,
            price_czk_mwh,
            volume_mwh,
            eur_czk
        from marts.fct_dam_prices
        where delivery_date = ?
        order by period
        """,
        (day,),
    )
    if not df.empty:
        local = pd.to_datetime(df["interval_start_utc"], utc=True).dt.tz_convert("Europe/Prague")
        df["local_time"] = local.dt.strftime("%H:%M %Z")
    return df


def day_summary(day: date) -> pd.DataFrame:
    """The selected day and the day before, for deltas."""
    return query(
        """
        select *
        from marts.fct_daily_market_summary
        where delivery_date in (?, ? - interval 1 day)
        order by delivery_date desc
        """,
        (day, day),
    )


# --- History ----------------------------------------------------------------------------


def daily_summary(start: date, end: date) -> pd.DataFrame:
    return query(
        """
        select
            delivery_date, year, month, season, is_weekend, is_holiday,
            base_price_eur_mwh, peak_price_eur_mwh, offpeak_price_eur_mwh,
            base_price_czk_mwh, eur_czk, min_price_eur_mwh, max_price_eur_mwh,
            negative_price_hours, avg_temperature_c, avg_wind_speed_ms,
            avg_global_radiation_wm2, sunshine_hours
        from marts.fct_daily_market_summary
        where delivery_date between ? and ?
        order by delivery_date
        """,
        (start, end),
    )


def weekday_hour_profile(start: date, end: date) -> pd.DataFrame:
    return query(
        """
        select
            isodow(delivery_date) as day_of_week,
            strftime(delivery_date, '%a') as day_name,
            local_hour,
            avg(price_eur_mwh) as price_eur_mwh,
            avg(price_czk_mwh) as price_czk_mwh,
            count(*) as hours
        from marts.fct_dam_prices_hourly
        where delivery_date between ? and ?
        group by all
        order by day_of_week, local_hour
        """,
        (start, end),
    )


def monthly_summary() -> pd.DataFrame:
    return query(
        """
        select
            year,
            month,
            avg(base_price_eur_mwh) as base_price_eur_mwh,
            avg(base_price_czk_mwh) as base_price_czk_mwh,
            sum(negative_price_hours) as negative_price_hours,
            count(*) as days
        from marts.fct_daily_market_summary
        group by all
        order by year, month
        """
    )


# --- Price vs weather -------------------------------------------------------------------

SEASONS = ("winter", "spring", "summer", "autumn")


def price_by_weather_bin(
    measure: str, bin_size: float, seasons: tuple[str, ...], hours: tuple[int, int]
) -> pd.DataFrame:
    """Mean and inter-quartile range of the hourly price per bin of a weather measure."""
    if measure not in ("global_radiation_wm2", "wind_speed_ms", "temperature_c"):
        raise ValueError(measure)
    placeholders = ", ".join("?" for _ in seasons)
    return query(
        f"""
        select
            floor({measure} / ?) * ? as bin_start,
            avg(price_eur_mwh) as mean_price,
            quantile_cont(price_eur_mwh, 0.25) as p25,
            quantile_cont(price_eur_mwh, 0.75) as p75,
            count(*) as hours
        from marts.mart_price_vs_weather
        where {measure} is not null
            and season in ({placeholders})
            and local_hour between ? and ?
        group by all
        having count(*) >= 20
        order by bin_start
        """,
        (bin_size, bin_size, *seasons, *hours),
    )


def correlations_by_season() -> pd.DataFrame:
    return query(
        """
        select
            season,
            corr(price_eur_mwh, global_radiation_wm2)
                filter (where local_hour between 9 and 16) as radiation_daytime,
            corr(price_eur_mwh, wind_speed_ms) as wind,
            corr(price_eur_mwh, temperature_c) as temperature,
            count(*) as hours
        from marts.mart_price_vs_weather
        where temperature_c is not null
        group by season
        """
    )


# --- Data quality -----------------------------------------------------------------------


def load_status() -> pd.DataFrame:
    """Per source: the latest successful load and the newest business period it covered."""
    return query(
        """
        select
            source,
            max(finished_at) filter (where status = 'success') as last_success_at,
            max(period_end) filter (where status = 'success') as last_period_loaded,
            date_diff('minute', max(finished_at) filter (where status = 'success'), now()) / 60.0
                as hours_since_success
        from staging.stg_ops__pipeline_runs
        group by source
        order by source
        """
    )


def recent_tasks(limit: int = 30) -> pd.DataFrame:
    return query(
        """
        select finished_at, source, business_date, period_end, status, rows_loaded,
               round(duration_s, 1) as duration_s, error
        from staging.stg_ops__pipeline_runs
        order by finished_at desc
        limit ?
        """,
        (limit,),
    )


def latest_dbt_results() -> pd.DataFrame:
    """Every node of the latest recorded dbt build, plus the latest source freshness."""
    return query(
        """
        with latest as (
            select resource_type = 'source' as is_freshness, max(generated_at) as generated_at
            from staging.stg_ops__dbt_results
            group by all
        )
        select results.*
        from staging.stg_ops__dbt_results as results
        inner join latest
            on (results.resource_type = 'source') = latest.is_freshness
            and results.generated_at = latest.generated_at
        order by
            results.is_failure desc, results.is_warning desc,
            results.resource_type, results.name
        """
    )


def coverage() -> pd.DataFrame:
    return query(
        """
        select 'OTE day-ahead prices' as dataset,
               min(delivery_date) as first_day, max(delivery_date) as last_day, count(*) as rows
        from marts.fct_dam_prices
        union all
        select 'ČNB EUR/CZK fixings', min(valid_for), max(valid_for), count(*)
        from staging.stg_cnb__fx_rates where currency_code = 'EUR'
        union all
        select 'ČHMÚ hourly weather (8 stations)',
            min(hour_start_utc)::date, max(hour_start_utc)::date, count(*)
        from intermediate.int_weather_hourly_station
        """
    )


# --- Price forecast ---------------------------------------------------------------------

MAIN_MODEL = "lightgbm"
# A delivery day can have both a walk-forward ('backtest') and a live forecast: show the live.
PREFER_LIVE = "row_number() over (partition by {key}, model order by run_type = 'live' desc) = 1"


def forecast_days() -> pd.DataFrame:
    """Delivery days with a forecast of the main model, latest first."""
    return query(
        f"""
        select
            delivery_date,
            run_type,
            any_value(issued_at) as issued_at,
            bool_or(is_late) as is_late,
            any_value(model_version) as model_version
        from ml.price_forecast
        where model = ?
        group by delivery_date, run_type, model
        qualify {PREFER_LIVE.format(key="delivery_date")}
        order by delivery_date desc
        """,
        (MAIN_MODEL,),
    )


def forecast_hours(day: date) -> pd.DataFrame:
    """The main model's forecast for one delivery day, with the actual price once published.
    Positions are elapsed hours since local midnight, so DST days plot correctly."""
    df = query(
        f"""
        with forecast as (
            select *
            from ml.price_forecast
            where delivery_date = ? and model = ?
            qualify {PREFER_LIVE.format(key="hour_start_utc")}
        )
        select
            (
                epoch(forecast.hour_start_utc)
                - epoch(forecast.delivery_date::timestamp at time zone 'Europe/Prague')
            )::int // 3600 as hour,
            forecast.hour_start_utc,
            forecast.predicted_price_eur_mwh as forecast,
            forecast.p10_eur_mwh as p10,
            forecast.p90_eur_mwh as p90,
            prices.price_eur_mwh as actual
        from forecast
        left join marts.fct_dam_prices_hourly as prices using (hour_start_utc)
        order by forecast.hour_start_utc
        """,
        (day, MAIN_MODEL),
    )
    if not df.empty:
        local = pd.to_datetime(df["hour_start_utc"], utc=True).dt.tz_convert("Europe/Prague")
        df["local_time"] = local.dt.strftime("%H:%M %Z")
    return df


def forecast_day_scores(day: date) -> pd.DataFrame:
    """One row per model for a scored delivery day."""
    return query(
        f"""
        select model, run_type, mae_eur_mwh, rmse_eur_mwh, bias_eur_mwh, interval_coverage
        from ml.ml_forecast_daily_metrics
        where delivery_date = ?
        qualify {PREFER_LIVE.format(key="delivery_date")}
        """,
        (day,),
    )


def forecast_daily_mae() -> pd.DataFrame:
    """Daily MAE per model (columns), with the run type of the main model's forecast."""
    long = query(
        f"""
        select delivery_date, model, run_type, mae_eur_mwh
        from ml.ml_forecast_daily_metrics
        qualify {PREFER_LIVE.format(key="delivery_date")}
        order by delivery_date
        """
    )
    if long.empty:
        return long
    wide = long.pivot(index="delivery_date", columns="model", values="mae_eur_mwh")
    main = long[long["model"] == MAIN_MODEL].set_index("delivery_date")["run_type"]
    return wide.assign(run_type=main).reset_index()


def forecast_metrics(where: str, params: tuple = ()) -> pd.DataFrame:
    """Accuracy per model over the scored hours selected by `where` (a fixed SQL condition)."""
    return query(
        f"""
        with scored as (
            select *
            from ml.ml_forecast_accuracy_hourly
            qualify {PREFER_LIVE.format(key="hour_start_utc")}
        )
        select
            model,
            count(distinct delivery_date) as days,
            avg(abs_error_eur_mwh) as mae,
            sqrt(avg(error_eur_mwh * error_eur_mwh)) as rmse,
            avg(abs_error_eur_mwh) filter (where is_peak) as mae_peak,
            avg(abs_error_eur_mwh) filter (where not is_peak) as mae_offpeak,
            avg(is_in_interval::int) as coverage
        from scored
        where {where}
        group by model
        order by mae
        """,
        params,
    )

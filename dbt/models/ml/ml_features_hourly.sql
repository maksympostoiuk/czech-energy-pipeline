-- Features for the day-ahead price forecast: one row per UTC hour of delivery day D+1, as
-- they were known at the cutoff, 12:00 Prague time on the forecast date D (the auction
-- closes then). Rows reach one day past the last published prices, so tomorrow's hours
-- exist before their prices do; price_eur_mwh, the target, is null until then.
--
-- Known at the cutoff: the calendar, prices of delivery days up to D (published on D-1
-- around 13:00), and weather forecasts published before the cutoff
-- (int_openmeteo_forecast_at_cutoff). Not known: D+1 prices (published ~13:00 on D),
-- observed weather of D+1 (only in ml_oracle_weather_hourly, for the perfect-forecast
-- experiment). known_prices_through and weather_latest_run_utc record what was used, and
-- tests check them against the cutoff.

with hours as (

    select *
    from {{ ref('int_delivery_hours') }}
    where delivery_date >= '{{ var("ml_first_delivery_date") }}'
        and delivery_date <= (select max(delivery_date) from {{ ref('fct_dam_prices_hourly') }}) + 1

),

prices as (

    select hour_start_utc, delivery_date, local_hour, is_peak, price_eur_mwh
    from {{ ref('fct_dam_prices_hourly') }}

),

-- Prices per delivery day and local hour. The repeated autumn 02:00 is averaged and the
-- skipped spring 02:00 is missing, so same-hour lookups work on every kind of day.
prices_by_local_hour as (

    select delivery_date, local_hour, avg(price_eur_mwh) as price_eur_mwh
    from prices
    group by delivery_date, local_hour

),

daily as (

    select
        delivery_date,
        avg(price_eur_mwh) as base,
        avg(price_eur_mwh) filter (where is_peak) as peak,
        avg(price_eur_mwh) filter (where not is_peak) as offpeak,
        min(price_eur_mwh) as min_price,
        max(price_eur_mwh) as max_price,
        stddev_pop(price_eur_mwh) as std_price,
        count(*) filter (where price_eur_mwh < 0) as negative_hours
    from prices
    group by delivery_date

),

-- What is known on forecast date D about the last week of prices (delivery days D-6..D).
price_history as (

    select
        hours.hour_start_utc,
        max(prices_by_local_hour.delivery_date) as known_prices_through,
        avg(prices_by_local_hour.price_eur_mwh) filter (
            where prices_by_local_hour.delivery_date = hours.forecast_date
        ) as price_same_hour_d,
        avg(prices_by_local_hour.price_eur_mwh) filter (
            where prices_by_local_hour.delivery_date = hours.delivery_date - 7
        ) as price_same_hour_week_ago,
        avg(prices_by_local_hour.price_eur_mwh) as price_same_hour_mean_7d,
        min(prices_by_local_hour.price_eur_mwh) as price_same_hour_min_7d,
        max(prices_by_local_hour.price_eur_mwh) as price_same_hour_max_7d
    from hours
    inner join prices_by_local_hour
        on prices_by_local_hour.local_hour = hours.local_hour
        and prices_by_local_hour.delivery_date between hours.forecast_date - 6 and hours.forecast_date
    group by hours.hour_start_utc

),

daily_history as (

    select
        hours.hour_start_utc,
        max(daily.delivery_date) as known_days_through,
        any_value(daily.base) filter (where daily.delivery_date = hours.forecast_date) as base_d,
        any_value(daily.peak) filter (where daily.delivery_date = hours.forecast_date) as peak_d,
        any_value(daily.offpeak) filter (where daily.delivery_date = hours.forecast_date)
            as offpeak_d,
        any_value(daily.min_price) filter (where daily.delivery_date = hours.forecast_date)
            as min_price_d,
        any_value(daily.max_price) filter (where daily.delivery_date = hours.forecast_date)
            as max_price_d,
        any_value(daily.std_price) filter (where daily.delivery_date = hours.forecast_date)
            as std_price_d,
        any_value(daily.negative_hours) filter (where daily.delivery_date = hours.forecast_date)
            as negative_hours_d,
        any_value(daily.base) filter (where daily.delivery_date = hours.forecast_date - 1)
            as base_d_minus_1,
        avg(daily.base) as base_mean_7d
    from hours
    inner join daily
        on daily.delivery_date between hours.forecast_date - 6 and hours.forecast_date
    group by hours.hour_start_utc

),

points as (

    select point_id, point_group
    from {{ ref('openmeteo_points') }}

),

weather as (

    select
        forecasts.hour_start_utc,
        avg(forecasts.temperature_2m_c) filter (where points.point_group = 'cz')
            as cz_temperature_c,
        avg(forecasts.shortwave_radiation_wm2) filter (where points.point_group = 'cz')
            as cz_radiation_wm2,
        avg(forecasts.wind_speed_10m_ms) filter (where points.point_group = 'cz')
            as cz_wind_10m_ms,
        avg(forecasts.wind_speed_100m_ms) filter (where points.point_group = 'cz')
            as cz_wind_100m_ms,
        avg(forecasts.cloud_cover_pct) filter (where points.point_group = 'cz')
            as cz_cloud_cover_pct,
        avg(forecasts.shortwave_radiation_wm2) filter (where points.point_group = 'de_solar')
            as de_solar_radiation_wm2,
        avg(forecasts.wind_speed_100m_ms) filter (where points.point_group = 'de_wind')
            as de_wind_100m_ms,
        max(greatest(forecasts.lead_days, forecasts.radiation_lead_days))
            as weather_max_lead_days,
        max(forecasts.latest_run_utc) as weather_latest_run_utc
    from {{ ref('int_openmeteo_forecast_at_cutoff') }} as forecasts
    inner join points using (point_id)
    group by forecasts.hour_start_utc

),

calendar as (

    select date_day, day_of_week, is_weekend, is_holiday, is_business_day, month, season
    from {{ ref('dim_date') }}

),

features as (

    select
        hours.hour_start_utc,
        hours.delivery_date,
        hours.forecast_date,
        hours.cutoff_at_utc,
        hours.hour_start_local,

        -- calendar of the delivery day
        hours.local_hour,
        hours.hour_index,
        hours.hours_in_day,
        hours.local_hour >= {{ var('peak_start_hour') }}
            and hours.local_hour < {{ var('peak_end_hour') }} as is_peak,
        calendar.day_of_week,
        calendar.is_weekend,
        calendar.is_holiday,
        calendar.is_business_day,
        coalesce(next_day.is_business_day, true) as is_next_day_business_day,
        coalesce(previous_day.is_business_day, true) as is_previous_day_business_day,
        calendar.month,
        calendar.season,
        dayofyear(hours.delivery_date) as day_of_year,

        -- prices known on the forecast date
        price_history.price_same_hour_d,
        price_history.price_same_hour_week_ago,
        price_history.price_same_hour_mean_7d,
        price_history.price_same_hour_min_7d,
        price_history.price_same_hour_max_7d,
        daily_history.base_d,
        daily_history.peak_d,
        daily_history.offpeak_d,
        daily_history.min_price_d,
        daily_history.max_price_d,
        daily_history.std_price_d,
        daily_history.negative_hours_d,
        daily_history.base_d_minus_1,
        daily_history.base_mean_7d,

        -- weather forecasts published before the cutoff
        weather.cz_temperature_c,
        weather.cz_radiation_wm2,
        weather.cz_wind_10m_ms,
        weather.cz_wind_100m_ms,
        weather.cz_cloud_cover_pct,
        weather.de_solar_radiation_wm2,
        weather.de_wind_100m_ms,
        avg(weather.cz_temperature_c) over delivery_day as cz_temperature_day_mean_c,
        avg(weather.cz_radiation_wm2) over delivery_day as cz_radiation_day_mean_wm2,
        avg(weather.de_solar_radiation_wm2) over delivery_day as de_solar_radiation_day_mean_wm2,
        avg(weather.de_wind_100m_ms) over delivery_day as de_wind_100m_day_mean_ms,

        -- provenance, checked against the cutoff by tests
        greatest(price_history.known_prices_through, daily_history.known_days_through)
            as known_prices_through,
        weather.weather_latest_run_utc,
        weather.weather_max_lead_days,

        -- target: the actual price, null until published
        prices.price_eur_mwh
    from hours
    inner join calendar on calendar.date_day = hours.delivery_date
    left join calendar as next_day on next_day.date_day = hours.delivery_date + 1
    left join calendar as previous_day on previous_day.date_day = hours.delivery_date - 1
    left join price_history using (hour_start_utc)
    left join daily_history using (hour_start_utc)
    left join weather using (hour_start_utc)
    left join prices using (hour_start_utc)
    window delivery_day as (partition by hours.delivery_date)

)

select * from features

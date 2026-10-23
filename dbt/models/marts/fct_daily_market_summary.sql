-- One row per delivery day: the OTE-style indices (base / peak / off-peak), price spread,
-- negative prices, volume, FX and the day's national weather.

with prices as (

    select * from {{ ref('fct_dam_prices') }}

),

daily_prices as (

    select
        delivery_date,
        any_value(mtu_minutes) as mtu_minutes,
        count(*) as periods,
        -- MTU periods within a day have equal length, so plain averages are time-weighted.
        round(avg(price_eur_mwh), 2) as base_price_eur_mwh,
        round(avg(price_eur_mwh) filter (where is_peak), 2) as peak_price_eur_mwh,
        round(avg(price_eur_mwh) filter (where not is_peak), 2) as offpeak_price_eur_mwh,
        min(price_eur_mwh) as min_price_eur_mwh,
        max(price_eur_mwh) as max_price_eur_mwh,
        max(price_eur_mwh) - min(price_eur_mwh) as price_spread_eur_mwh,
        count(*) filter (where is_negative_price) as negative_price_periods,
        sum(mtu_minutes) filter (where is_negative_price) / 60.0 as negative_price_hours,
        sum(volume_mwh) as volume_mwh,
        any_value(eur_czk) as eur_czk
    from prices
    group by delivery_date

),

daily_weather as (

    select
        (hour_start_utc at time zone 'Europe/Prague')::date as local_date,
        round(avg(temperature_c), 2) as avg_temperature_c,
        round(avg(wind_speed_ms), 2) as avg_wind_speed_ms,
        round(avg(global_radiation_wm2), 1) as avg_global_radiation_wm2,
        round(sum(sunshine_min) / 60.0, 1) as sunshine_hours,
        round(sum(precipitation_mm), 1) as precipitation_mm,
        count(*) as weather_hours
    from {{ ref('int_weather_hourly_cz') }}
    group by local_date

)

select
    daily_prices.delivery_date,
    dim_date.year,
    dim_date.month,
    dim_date.day_of_week,
    dim_date.is_weekend,
    dim_date.is_holiday,
    dim_date.is_business_day,
    dim_date.season,
    daily_prices.mtu_minutes,
    daily_prices.periods,
    dim_date.hours_in_day * 60 // daily_prices.mtu_minutes as expected_periods,
    daily_prices.periods = dim_date.hours_in_day * 60 // daily_prices.mtu_minutes
        as is_complete,
    incomplete.delivery_date is not null as is_known_incomplete,
    daily_prices.base_price_eur_mwh,
    daily_prices.peak_price_eur_mwh,
    daily_prices.offpeak_price_eur_mwh,
    daily_prices.min_price_eur_mwh,
    daily_prices.max_price_eur_mwh,
    daily_prices.price_spread_eur_mwh,
    daily_prices.negative_price_periods,
    coalesce(daily_prices.negative_price_hours, 0) as negative_price_hours,
    daily_prices.volume_mwh,
    daily_prices.eur_czk,
    round(daily_prices.base_price_eur_mwh * daily_prices.eur_czk, 2) as base_price_czk_mwh,
    daily_weather.avg_temperature_c,
    daily_weather.avg_wind_speed_ms,
    daily_weather.avg_global_radiation_wm2,
    daily_weather.sunshine_hours,
    daily_weather.precipitation_mm,
    daily_weather.weather_hours
from daily_prices
inner join {{ ref('dim_date') }} as dim_date on dim_date.date_day = daily_prices.delivery_date
left join {{ ref('ote_known_incomplete_days') }} as incomplete
    on incomplete.delivery_date = daily_prices.delivery_date
left join daily_weather on daily_weather.local_date = daily_prices.delivery_date

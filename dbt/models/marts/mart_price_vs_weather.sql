-- Hourly prices next to the national weather signal, for correlation analysis and the
-- dashboard. Hours without weather yet (tomorrow's prices) keep null weather columns.

select
    prices.hour_start_utc,
    prices.hour_start_local,
    prices.delivery_date,
    prices.local_hour,
    prices.is_peak,
    dim_date.is_weekend,
    dim_date.is_holiday,
    dim_date.season,
    prices.price_eur_mwh,
    prices.price_czk_mwh,
    prices.volume_mwh,
    weather.temperature_c,
    weather.wind_speed_ms,
    weather.global_radiation_wm2,
    weather.sunshine_min,
    weather.precipitation_mm,
    weather.reporting_stations
from {{ ref('fct_dam_prices_hourly') }} as prices
inner join {{ ref('dim_date') }} as dim_date on dim_date.date_day = prices.delivery_date
left join {{ ref('int_weather_hourly_cz') }} as weather using (hour_start_utc)

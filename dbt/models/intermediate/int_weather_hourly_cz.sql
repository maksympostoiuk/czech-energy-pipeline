-- A simple national weather signal: the unweighted mean of the 8 regional stations.
-- Global radiation and wind drive solar and wind generation, temperature drives demand.

select
    hour_start_utc,
    round(avg(temperature_c), 2) as temperature_c,
    round(avg(wind_speed_ms), 2) as wind_speed_ms,
    round(avg(global_radiation_wm2), 1) as global_radiation_wm2,
    round(avg(sunshine_min), 1) as sunshine_min,
    round(avg(precipitation_mm), 2) as precipitation_mm,
    round(avg(relative_humidity_pct), 1) as relative_humidity_pct,
    count(distinct station_id) as reporting_stations
from {{ ref('int_weather_hourly_station') }}
group by hour_start_utc

-- 10-minute observations pivoted to one row per station and UTC hour.
-- Levels (temperature, wind, radiation, humidity) are averaged; amounts (sunshine,
-- precipitation) are summed. Values ČHMÚ marks poor or missing are left out.

select
    station_id,
    date_trunc('hour', observed_at_utc) as hour_start_utc,
    avg(value) filter (where measure = 'temperature_c') as temperature_c,
    avg(value) filter (where measure = 'wind_speed_ms') as wind_speed_ms,
    avg(value) filter (where measure = 'global_radiation_wm2') as global_radiation_wm2,
    sum(value) filter (where measure = 'sunshine_s') / 60.0 as sunshine_min,
    sum(value) filter (where measure = 'precipitation_mm') as precipitation_mm,
    avg(value) filter (where measure = 'relative_humidity_pct') as relative_humidity_pct,
    count(*) as usable_observations
from {{ ref('stg_chmi__observations') }}
where is_usable
group by all

-- Weather forecasts as issued: one row per point, valid hour and lead (1 or 2 days).
-- issued_at_utc bounds the run the values come from: it was initialised no later than
-- that, so a forecast is usable at a cutoff when issued_at_utc plus the model's delay is
-- before it (docs/sources.md, section 5). Radiation at valid_at_utc is the mean over the
-- preceding hour, so it describes the hour starting at radiation_hour_start_utc.

with source as (

    select * from {{ source('raw', 'openmeteo_forecasts') }}

)

select
    point_id,
    model,
    valid_at as valid_at_utc,
    lead_days,
    issued_at as issued_at_utc,
    valid_at - interval 1 hour as radiation_hour_start_utc,
    temperature_2m_c,
    shortwave_radiation_wm2,
    wind_speed_10m_ms,
    wind_speed_100m_ms,
    cloud_cover_pct,
    _ingested_at as ingested_at
from source

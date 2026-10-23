-- Long format, one row per station × element × 10 minutes, with readable element names.
-- Nothing is filtered here; is_usable marks what downstream aggregates may use.

with source as (

    select * from {{ source('raw', 'chmi_observations') }}

)

select
    station_id,
    case element
        when 'T' then 'temperature_c'
        when 'F' then 'wind_speed_ms'
        when 'RGLB10' then 'global_radiation_wm2'
        when 'SSV10M' then 'sunshine_s'
        when 'SRA10M' then 'precipitation_mm'
        when 'H' then 'relative_humidity_pct'
    end as measure,
    element as chmi_element,
    observed_at as observed_at_utc,
    value,
    quality,
    value is not null
        and quality in ({{ var('chmi_usable_quality') | join(', ') }}) as is_usable,
    source_folder,
    _ingested_at as ingested_at
from source

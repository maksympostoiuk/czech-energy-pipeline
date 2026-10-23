-- Long format, one row per station × element × 10 minutes, with readable element names.
-- Nothing is filtered here; is_usable marks what downstream aggregates may use: a value
-- that exists, has an acceptable quality code and is physically possible (seed
-- chmi_measure_ranges). ČHMÚ occasionally marks impossible values as good quality.

with source as (

    select * from {{ source('raw', 'chmi_observations') }}

),

renamed as (

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
        source_folder,
        _ingested_at as ingested_at
    from source

)

select
    renamed.station_id,
    renamed.measure,
    renamed.chmi_element,
    renamed.observed_at_utc,
    renamed.value,
    renamed.quality,
    renamed.value between ranges.min_value and ranges.max_value as is_plausible,
    renamed.value is not null
        and renamed.quality in ({{ var('chmi_usable_quality') | join(', ') }})
        and renamed.value between ranges.min_value and ranges.max_value as is_usable,
    renamed.source_folder,
    renamed.ingested_at
from renamed
left join {{ ref('chmi_measure_ranges') }} as ranges using (measure)

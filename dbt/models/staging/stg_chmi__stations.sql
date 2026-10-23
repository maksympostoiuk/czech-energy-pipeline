-- meta1 occasionally lists a station twice with slightly different coordinates
-- (e.g. Olomouc, Klášterní Hradisko); keep one row per station deterministically.

select
    station_id,
    gh_id,
    full_name,
    latitude,
    longitude,
    elevation_m,
    begin_date
from {{ source('raw', 'chmi_stations') }}
qualify row_number() over (
    partition by station_id
    order by _ingested_at desc, latitude, longitude
) = 1

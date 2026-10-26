-- Every hour from the first day the archive covers fully up to the last loaded hour has
-- both leads for every point. A gap means a failed load, or a new hole in the archive to
-- look into (the known one, 2026-04-07..11, only nulls some day-2 values: rows still exist).

{{ config(tags=['needs_history']) }}

with forecasts as (

    select * from {{ ref('stg_openmeteo__forecasts') }}

),

hours as (

    select unnest(generate_series(
        timestamptz '2024-01-21 00:00:00+00',
        (select max(valid_at_utc) from forecasts),
        interval 1 hour
    )) as valid_at_utc

),

loaded as (

    select valid_at_utc, count(*) as row_count
    from forecasts
    group by valid_at_utc

)

select
    hours.valid_at_utc,
    coalesce(loaded.row_count, 0) as row_count,
    (select count(*) from {{ ref('openmeteo_points') }}) * 2 as expected_rows
from hours
left join loaded using (valid_at_utc)
where coalesce(loaded.row_count, 0) <> (select count(*) from {{ ref('openmeteo_points') }}) * 2

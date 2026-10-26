-- One row per UTC hour of every delivery day, with the moment its price forecast is made:
-- on the day before (forecast_date), before the day-ahead auction closes at 12:00 Prague
-- time (cutoff_at_utc). 23, 24 or 25 rows per day; the repeated autumn 02:00 is two rows.

with days as (

    select
        date_day as delivery_date,
        date_day - 1 as forecast_date,
        hours_in_day,
        date_day::timestamp at time zone 'Europe/Prague' as day_start_utc,
        (date_day + 1)::timestamp at time zone 'Europe/Prague' as day_end_utc,
        ((date_day - 1)::timestamp + to_hours({{ var('forecast_cutoff_hour_local') }}))
            at time zone 'Europe/Prague' as cutoff_at_utc
    from {{ ref('dim_date') }}

),

hours as (

    select
        unnest(generate_series(day_start_utc, day_end_utc - interval 1 hour, interval 1 hour))
            as hour_start_utc,
        delivery_date,
        forecast_date,
        cutoff_at_utc,
        hours_in_day
    from days

)

select
    hour_start_utc,
    delivery_date,
    forecast_date,
    cutoff_at_utc,
    hour_start_utc at time zone 'Europe/Prague' as hour_start_local,
    hour(hour_start_utc at time zone 'Europe/Prague') as local_hour,
    -- 0-based position in the delivery day: unlike local_hour, unique on DST days.
    row_number() over (partition by delivery_date order by hour_start_utc) - 1 as hour_index,
    hours_in_day
from hours

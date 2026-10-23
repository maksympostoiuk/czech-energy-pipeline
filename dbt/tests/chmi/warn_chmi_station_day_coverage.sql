{{ config(severity='warn') }}

-- Station-days (UTC) with usable data in fewer than 22 of 24 hours. The last two days are
-- skipped: today is still in progress and yesterday may come from the partial now/ folder.

with hours_per_day as (

    select
        station_id,
        hour_start_utc::date as observed_date,
        count(*) as hours_with_data
    from {{ ref('int_weather_hourly_station') }}
    where hour_start_utc::date < current_date - 1
    group by all

),

expected as (

    select stations.station_id, days.observed_date
    from {{ ref('chmi_stations_selected') }} as stations
    cross join (select distinct observed_date from hours_per_day) as days

)

select
    expected.station_id,
    expected.observed_date,
    coalesce(hours_per_day.hours_with_data, 0) as hours_with_data
from expected
left join hours_per_day using (station_id, observed_date)
where coalesce(hours_per_day.hours_with_data, 0) < 22

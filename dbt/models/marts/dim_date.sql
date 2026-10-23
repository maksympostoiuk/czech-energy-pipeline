{{ config(materialized='table') }}

with days as (

    select range::date as date_day
    from range(date '2024-01-01', date '2028-01-01', interval 1 day)

),

holidays as (

    select * from {{ ref('czech_public_holidays') }}

)

select
    days.date_day,
    year(days.date_day) as year,
    quarter(days.date_day) as quarter,
    month(days.date_day) as month,
    strftime(days.date_day, '%B') as month_name,
    weekofyear(days.date_day) as iso_week,
    isodow(days.date_day) as day_of_week,  -- 1 = Monday
    strftime(days.date_day, '%A') as day_name,
    isodow(days.date_day) >= 6 as is_weekend,
    holidays.holiday_date is not null as is_holiday,
    holidays.name_en as holiday_name,
    isodow(days.date_day) < 6 and holidays.holiday_date is null as is_business_day,
    case
        when month(days.date_day) in (12, 1, 2) then 'winter'
        when month(days.date_day) in (3, 4, 5) then 'spring'
        when month(days.date_day) in (6, 7, 8) then 'summer'
        else 'autumn'
    end as season,
    -- 23 or 25 on the days clocks change; drives the expected number of MTU periods.
    date_diff(
        'hour',
        days.date_day::timestamp at time zone 'Europe/Prague',
        (days.date_day + 1)::timestamp at time zone 'Europe/Prague'
    ) as hours_in_day
from days
left join holidays on holidays.holiday_date = days.date_day

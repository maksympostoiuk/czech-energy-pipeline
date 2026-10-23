-- ČNB fixes exchange rates on every Czech business day. Within the loaded range a weekday
-- without a fixing must be a public holiday and a weekday holiday must have no fixing.
-- A row here means a missing ČNB load or an outdated holiday seed.

with fixing_days as (

    select distinct valid_for as date_day
    from {{ ref('stg_cnb__fx_rates') }}
    where currency_code = 'EUR'

),

loaded_weekdays as (

    select date_day, is_holiday, holiday_name
    from {{ ref('dim_date') }}
    where not is_weekend
        and date_day between (select min(date_day) from fixing_days)
            and (select max(date_day) from fixing_days)

)

select
    loaded_weekdays.date_day,
    loaded_weekdays.is_holiday,
    loaded_weekdays.holiday_name,
    fixing_days.date_day is not null as has_fixing
from loaded_weekdays
left join fixing_days using (date_day)
where loaded_weekdays.is_holiday = (fixing_days.date_day is not null)

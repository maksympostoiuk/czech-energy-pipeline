-- EUR/CZK for every calendar day. ČNB fixes rates on business days only, so weekends and
-- holidays carry the last fixing forward. The spine reaches the last delivery day OTE has
-- published (tomorrow), which therefore uses today's fixing.

with fixings as (

    select valid_for, rate_czk
    from {{ ref('stg_cnb__fx_rates') }}
    where currency_code = 'EUR'

),

calendar as (

    select range::date as date_day
    from range(
        (select min(valid_for) from fixings),
        greatest(
            (select max(valid_for) from fixings),
            (select max(delivery_date) from {{ ref('stg_ote__dam_prices') }})
        ) + interval 1 day,
        interval 1 day
    )

)

select
    calendar.date_day,
    last_value(fixings.rate_czk ignore nulls) over by_day as eur_czk,
    last_value(fixings.valid_for ignore nulls) over by_day as fixing_valid_for,
    fixings.valid_for is not null as is_fixing_day
from calendar
left join fixings on fixings.valid_for = calendar.date_day
window by_day as (order by calendar.date_day rows between unbounded preceding and current row)

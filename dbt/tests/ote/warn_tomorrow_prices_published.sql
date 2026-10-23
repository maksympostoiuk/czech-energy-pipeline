{{ config(severity='warn', tags=['freshness']) }}

-- OTE publishes tomorrow's results around 13:00 CET. From 14:00 Prague time the
-- warehouse should hold them; otherwise the daily run ran too early or OTE is late.

with now_prague as (

    select current_timestamp at time zone 'Europe/Prague' as local_now

)

select
    now_prague.local_now,
    (select max(delivery_date) from {{ ref('fct_dam_prices') }}) as last_delivery_date
from now_prague
where hour(now_prague.local_now) >= 14
    and (select max(delivery_date) from {{ ref('fct_dam_prices') }})
        < now_prague.local_now::date + 1

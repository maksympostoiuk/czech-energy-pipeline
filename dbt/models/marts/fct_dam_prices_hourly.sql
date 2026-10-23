-- Hourly view of the whole history: hourly MTU days pass through, 15-minute days are
-- averaged per hour. The grain is the UTC hour, so the repeated local 02:00 hour on the
-- autumn DST day stays two separate rows.

select
    hour_start_utc,
    any_value(delivery_date) as delivery_date,
    min(interval_start_local) as hour_start_local,
    any_value(local_hour) as local_hour,
    bool_and(is_peak) as is_peak,
    round(avg(price_eur_mwh), 2) as price_eur_mwh,
    round(avg(price_czk_mwh), 2) as price_czk_mwh,
    min(price_eur_mwh) as min_price_eur_mwh,
    max(price_eur_mwh) as max_price_eur_mwh,
    sum(volume_mwh) as volume_mwh,
    count(*) as periods,
    any_value(mtu_minutes) as mtu_minutes
from {{ ref('fct_dam_prices') }}
group by hour_start_utc

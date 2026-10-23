-- Day-ahead prices with the CZK conversion and the local-time attributes marts group by.

with prices as (

    select * from {{ ref('stg_ote__dam_prices') }}

),

fx as (

    select * from {{ ref('int_fx_eur_czk_daily') }}

)

select
    prices.delivery_date,
    prices.period,
    prices.mtu_minutes,
    prices.interval_start_utc,
    prices.interval_end_utc,
    prices.interval_start_local,
    date_trunc('hour', prices.interval_start_utc) as hour_start_utc,
    hour(prices.interval_start_local) as local_hour,
    hour(prices.interval_start_local) >= {{ var('peak_start_hour') }}
        and hour(prices.interval_start_local) < {{ var('peak_end_hour') }} as is_peak,
    prices.price_eur_mwh,
    prices.price_60min_ref_eur_mwh,
    round(prices.price_eur_mwh * fx.eur_czk, 2) as price_czk_mwh,
    prices.volume_mwh,
    fx.eur_czk,
    fx.fixing_valid_for
from prices
left join fx on fx.date_day = prices.delivery_date

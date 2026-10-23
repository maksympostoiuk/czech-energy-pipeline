-- One row per market time unit (MTU). OTE numbers periods 1..N within the delivery day in
-- Europe/Prague, so clock times are derived from local midnight converted to UTC: that is
-- the only way that works on 23/25-hour DST days (docs/sources.md, section 1).

with source as (

    select * from {{ source('raw', 'ote_dam_results') }}

),

timed as (

    select
        delivery_date,
        period,
        mtu_minutes,
        (delivery_date::timestamp at time zone 'Europe/Prague')
            + to_minutes(mtu_minutes * (period - 1)) as interval_start_utc,
        price_eur_mwh,
        price_60min_ref_eur_mwh,
        volume_mwh,
        _ingested_at as ingested_at
    from source

)

select
    delivery_date,
    period,
    mtu_minutes,
    interval_start_utc,
    interval_start_utc + to_minutes(mtu_minutes) as interval_end_utc,
    interval_start_utc at time zone 'Europe/Prague' as interval_start_local,
    price_eur_mwh,
    price_60min_ref_eur_mwh,
    volume_mwh,
    ingested_at
from timed

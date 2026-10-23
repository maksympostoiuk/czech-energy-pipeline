{{ config(tags=['needs_history']) }}

-- Reconciliation with the source of truth: OTE's official daily report (BASE / PEAK /
-- OFFPEAK price indices and volumes) for sample days covering both market time units,
-- all four kinds of DST day and a negative-price day. Returns the days that disagree.
--
-- Known OTE quirk: on DST days of the hourly market (before 2025-10-01) the report's
-- PEAK/OFFPEAK *volumes* are summed over hour positions 9-20, while its PEAK *price* uses
-- wall-clock 08:00-20:00 like this project. Those two volume checks are skipped there.

{% set price_tolerance = 0.01 %}  -- reports before 2025 are unrounded, ours have 2 decimals
{% set volume_tolerance = 0.001 %}

with official as (

    select * from {{ ref('ote_official_daily_indices') }}

),

ours as (

    select
        prices.delivery_date,
        any_value(prices.mtu_minutes) = 60 and any_value(dim_date.hours_in_day) <> 24
            as is_hourly_dst_day,
        avg(prices.price_eur_mwh) as base_price_eur_mwh,
        avg(prices.price_eur_mwh) filter (where prices.is_peak) as peak_price_eur_mwh,
        avg(prices.price_eur_mwh) filter (where not prices.is_peak) as offpeak_price_eur_mwh,
        sum(prices.volume_mwh) as base_volume_mwh,
        sum(prices.volume_mwh) filter (where prices.is_peak) as peak_volume_mwh,
        sum(prices.volume_mwh) filter (where not prices.is_peak) as offpeak_volume_mwh
    from {{ ref('fct_dam_prices') }} as prices
    inner join {{ ref('dim_date') }} as dim_date on dim_date.date_day = prices.delivery_date
    where prices.delivery_date in (select delivery_date from official)
    group by prices.delivery_date

)

select
    official.delivery_date,
    official.report_file,
    official.base_price_eur_mwh as official_base,
    ours.base_price_eur_mwh as our_base,
    official.peak_price_eur_mwh as official_peak,
    ours.peak_price_eur_mwh as our_peak,
    official.offpeak_price_eur_mwh as official_offpeak,
    ours.offpeak_price_eur_mwh as our_offpeak,
    official.base_volume_mwh as official_volume,
    ours.base_volume_mwh as our_volume
from official
left join ours using (delivery_date)
where ours.delivery_date is null
    or abs(official.base_price_eur_mwh - ours.base_price_eur_mwh) > {{ price_tolerance }}
    or abs(official.peak_price_eur_mwh - ours.peak_price_eur_mwh) > {{ price_tolerance }}
    or abs(official.offpeak_price_eur_mwh - ours.offpeak_price_eur_mwh) > {{ price_tolerance }}
    or abs(official.base_volume_mwh - ours.base_volume_mwh) > {{ volume_tolerance }}
    or (
        not ours.is_hourly_dst_day
        and (
            abs(official.peak_volume_mwh - ours.peak_volume_mwh) > {{ volume_tolerance }}
            or abs(official.offpeak_volume_mwh - ours.offpeak_volume_mwh) > {{ volume_tolerance }}
        )
    )

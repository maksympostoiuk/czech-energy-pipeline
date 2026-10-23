{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['delivery_date', 'period'],
    )
}}

-- Grain: one market time unit as traded, 60 minutes up to 2025-09-30 and 15 minutes from
-- 2025-10-01. Incremental runs rebuild the last week, which covers re-published days and
-- the FX rate of "tomorrow" being replaced by the real fixing.

select
    delivery_date,
    period,
    mtu_minutes,
    interval_start_utc,
    interval_end_utc,
    interval_start_local,
    hour_start_utc,
    local_hour,
    is_peak,
    price_eur_mwh,
    price_czk_mwh,
    price_60min_ref_eur_mwh,
    volume_mwh,
    price_eur_mwh < 0 as is_negative_price,
    eur_czk,
    fixing_valid_for
from {{ ref('int_dam_prices_czk') }}
{% if is_incremental() %}
where delivery_date >= (select max(delivery_date) - interval 7 day from {{ this }})
{% endif %}

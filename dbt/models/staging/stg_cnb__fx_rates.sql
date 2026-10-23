-- ČNB quotes some currencies per 100 or 1000 units; rate_czk is always per 1 unit.

select
    valid_for,
    currency_code,
    amount,
    rate as rate_per_amount_czk,
    rate / amount as rate_czk,
    _ingested_at as ingested_at
from {{ source('raw', 'cnb_fx_rates') }}

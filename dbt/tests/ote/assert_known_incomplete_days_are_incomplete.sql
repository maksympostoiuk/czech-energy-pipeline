-- The exception list must not go stale: if OTE re-publishes a listed day in full, the
-- day shows up here and should be removed from ote_known_incomplete_days (and from
-- ote.KNOWN_INCOMPLETE_DAYS in Python).

select delivery_date, periods, expected_periods
from {{ ref('fct_daily_market_summary') }}
where is_known_incomplete
    and is_complete

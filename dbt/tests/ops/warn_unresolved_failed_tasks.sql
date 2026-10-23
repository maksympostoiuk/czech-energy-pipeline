{{ config(severity='warn') }}

-- Load tasks whose latest attempt failed. A failure followed by a successful retry for
-- the same period (like OTE 2025-07-04 before its allowlisting) is resolved and ignored.

select source, business_date, period_end, status, error, finished_at
from {{ ref('stg_ops__pipeline_runs') }}
qualify row_number() over (
    partition by source, business_date, period_end
    order by finished_at desc
) = 1
    and status = 'failed'

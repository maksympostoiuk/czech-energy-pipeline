select
    run_id,
    source,
    business_date,
    period_end,
    status,
    rows_loaded,
    started_at,
    finished_at,
    date_diff('millisecond', started_at, finished_at) / 1000.0 as duration_s,
    error
from {{ source('ops', 'pipeline_runs') }}

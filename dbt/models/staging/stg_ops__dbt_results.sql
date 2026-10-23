-- One row per dbt node per invocation (models, seeds, tests, source freshness checks).

select
    invocation_id,
    generated_at,
    unique_id,
    resource_type,
    name,
    status,
    status in ('error', 'fail', 'runtime error') as is_failure,
    status = 'warn' as is_warning,
    failures,
    execution_time_s,
    message
from {{ source('ops', 'dbt_results') }}

{{ config(severity='warn') }}

-- Audit list of physically impossible values ČHMÚ delivered. They are already excluded
-- from all aggregates (stg_chmi__observations.is_usable), so this only warns; review the
-- rows and report them to ČHMÚ if they persist after its quality control.
-- Example found during development: Holešov, 2026-09-22 09:50 UTC, global radiation
-- 1673.7 W/m² with quality code 0 ("good").

select
    station_id,
    measure,
    observed_at_utc,
    value,
    quality,
    source_folder
from {{ ref('stg_chmi__observations') }}
where value is not null
    and not is_plausible

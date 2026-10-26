-- For every delivery hour and forecast point: the freshest Open-Meteo forecast that was
-- already published at the cutoff of that hour's delivery day. A row counts as published
-- at issued_at_utc (the latest possible run initialisation) plus the publication delay;
-- with 6 hours this keeps runs up to 00Z of the forecast date, so previous_day1 serves
-- hours before 06:00 UTC of the delivery day and previous_day2 the rest
-- (docs/sources.md, section 5). Never "the latest forecast now": a late job or a reload
-- cannot change what a past delivery day sees.
--
-- Radiation at valid hour T is the mean of the hour before T, so hour H takes radiation
-- from the row valid at H + 1 h; the other variables are instantaneous at H.

with hours as (

    select hour_start_utc, cutoff_at_utc
    from {{ ref('int_delivery_hours') }}

),

forecasts as (

    select
        *,
        issued_at_utc + to_hours({{ var('openmeteo_publication_delay_hours') }})
            as published_at_utc
    from {{ ref('stg_openmeteo__forecasts') }}

),

instant as (

    select
        hours.hour_start_utc,
        forecasts.point_id,
        forecasts.lead_days,
        forecasts.issued_at_utc,
        forecasts.temperature_2m_c,
        forecasts.wind_speed_10m_ms,
        forecasts.wind_speed_100m_ms,
        forecasts.cloud_cover_pct
    from hours
    inner join forecasts
        on forecasts.valid_at_utc = hours.hour_start_utc
        and forecasts.published_at_utc <= hours.cutoff_at_utc
    qualify row_number() over (
        partition by hours.hour_start_utc, forecasts.point_id order by forecasts.lead_days
    ) = 1

),

radiation as (

    select
        hours.hour_start_utc,
        forecasts.point_id,
        forecasts.lead_days,
        forecasts.issued_at_utc,
        forecasts.shortwave_radiation_wm2
    from hours
    inner join forecasts
        on forecasts.valid_at_utc = hours.hour_start_utc + interval 1 hour
        and forecasts.published_at_utc <= hours.cutoff_at_utc
    qualify row_number() over (
        partition by hours.hour_start_utc, forecasts.point_id order by forecasts.lead_days
    ) = 1

)

select
    hour_start_utc,
    point_id,
    instant.temperature_2m_c,
    radiation.shortwave_radiation_wm2,
    instant.wind_speed_10m_ms,
    instant.wind_speed_100m_ms,
    instant.cloud_cover_pct,
    instant.lead_days,
    radiation.lead_days as radiation_lead_days,
    greatest(instant.issued_at_utc, radiation.issued_at_utc) as latest_run_utc
from instant
full outer join radiation using (hour_start_utc, point_id)

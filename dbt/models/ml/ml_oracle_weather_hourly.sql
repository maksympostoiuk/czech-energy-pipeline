-- Observed Czech weather (ČHMÚ, mean of the 8 stations) for the hours of
-- ml_features_hourly. This is NOT known at the cutoff: it exists only for the
-- "perfect weather forecast" experiment, the upper bound of what better weather forecasts
-- could add. Never join it into a model that is evaluated or used in production.

select
    features.hour_start_utc,
    observed.temperature_c as cz_temperature_c,
    observed.global_radiation_wm2 as cz_radiation_wm2,
    observed.wind_speed_ms as cz_wind_10m_ms,
    observed.reporting_stations
from {{ ref('ml_features_hourly') }} as features
inner join {{ ref('int_weather_hourly_cz') }} as observed using (hour_start_utc)

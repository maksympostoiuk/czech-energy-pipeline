select
    selected.station_id,
    selected.short_name,
    selected.region,
    stations.full_name,
    stations.latitude,
    stations.longitude,
    stations.elevation_m,
    stations.begin_date as measuring_since
from {{ ref('chmi_stations_selected') }} as selected
left join {{ ref('stg_chmi__stations') }} as stations using (station_id)

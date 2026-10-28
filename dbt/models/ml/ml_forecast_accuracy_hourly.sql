-- Every stored forecast next to the price that cleared. OTE publishes D+1 around 13:00 on
-- the forecast date, so a forecast is scored the same afternoon by the daily build.

with forecasts as (

    select * from {{ source('ml', 'price_forecast') }}

),

prices as (

    select hour_start_utc, local_hour, is_peak, price_eur_mwh
    from {{ ref('fct_dam_prices_hourly') }}

)

select
    forecasts.hour_start_utc,
    forecasts.delivery_date,
    forecasts.forecast_date,
    prices.local_hour,
    prices.is_peak,
    forecasts.model,
    forecasts.run_type,
    forecasts.model_version,
    forecasts.issued_at,
    forecasts.is_late,
    forecasts.predicted_price_eur_mwh,
    forecasts.p10_eur_mwh,
    forecasts.p90_eur_mwh,
    prices.price_eur_mwh as actual_price_eur_mwh,
    forecasts.predicted_price_eur_mwh - prices.price_eur_mwh as error_eur_mwh,
    abs(forecasts.predicted_price_eur_mwh - prices.price_eur_mwh) as abs_error_eur_mwh,
    case
        when forecasts.p10_eur_mwh is not null
            then prices.price_eur_mwh between forecasts.p10_eur_mwh and forecasts.p90_eur_mwh
    end as is_in_interval
from forecasts
inner join prices using (hour_start_utc)

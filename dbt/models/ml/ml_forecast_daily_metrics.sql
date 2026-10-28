-- Accuracy of every model per delivery day: what the dashboard's rolling MAE and metrics
-- table read. Live and backtest forecasts are kept apart by run_type.

select
    delivery_date,
    model,
    run_type,
    any_value(model_version) as model_version,
    bool_or(is_late) as is_late,
    count(*) as hours,
    avg(abs_error_eur_mwh) as mae_eur_mwh,
    sqrt(avg(error_eur_mwh * error_eur_mwh)) as rmse_eur_mwh,
    avg(abs_error_eur_mwh) filter (where is_peak) as mae_peak_eur_mwh,
    avg(abs_error_eur_mwh) filter (where not is_peak) as mae_offpeak_eur_mwh,
    avg(error_eur_mwh) as bias_eur_mwh,
    avg(is_in_interval::int) as interval_coverage
from {{ ref('ml_forecast_accuracy_hourly') }}
group by delivery_date, model, run_type

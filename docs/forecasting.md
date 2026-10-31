# Day-ahead price forecast: method and results

The same results with charts: [forecasting/report.ipynb](../forecasting/report.ipynb).

The task: before the day-ahead auction closes at **12:00 Prague time on day D**, forecast the
hourly price of the CZ bidding zone for every hour of **D+1**. Results below come from
`uv run forecast report --refresh`, which runs `forecast validate`, `backtest` and
`importance` and executes the notebook, on the warehouse of 2026-10-31. The test period has a
fixed end, so the numbers stay reproducible as new days arrive.

## Known at the cutoff

Every input is something that existed at 12:00 on D. The features are built in dbt
(`ml.ml_features_hourly`, see [sources.md, section 5](sources.md) for the weather forecasts):

| Group | Features |
|---|---|
| Calendar of D+1 | local hour, position in the day (DST-safe), hours in the day, weekday, weekend, public holiday, business day before/after, month, day of year |
| Prices known on D | price at the same hour on D and on D-6 (a week before D+1), mean/min/max at that hour over D-6..D, base/peak/off-peak/min/max/std and negative hours of D, base of D-1, mean base of D-6..D |
| Weather forecast | DWD ICON-EU via Open-Meteo, runs initialised no later than 00Z of D: Czech mean of 2 m temperature, radiation, 10 m and 100 m wind, cloud cover; radiation of three southern German points (solar) and 100 m wind of three northern German points (wind); day means |

Not used: D+1 prices (published ~13:00 on D) and observed weather of D+1. The rule is
enforced by dbt tests on every row and by `tests/test_ml_features_leakage.py`, which builds
the features of D+1 with and without (and with altered) data from after the cutoff and
requires them to be identical.

## Evaluation

- **Walk-forward by day.** For each delivery day the model is trained on all delivery days
  up to D (the prices known at the cutoff) and predicts D+1. In the test it is refitted
  every day, as in production. `forecast/backtest.py` refuses training data from D+1 or
  later, and a unit test checks it.
- **Periods.** Settings were chosen on a **validation period** (delivery days 2025-09-01 to
  2026-02-28, refit weekly) and scored on the **test period** (2026-03-01 to
  2026-10-29: 243 days, 5,832 hours, both DST changes, the -500 EUR/MWh floor on 2026-05-01).
  Training data starts on 2024-01-22.
- **Metrics.** MAE and RMSE in EUR/MWh, split into peak (08:00-20:00) and off-peak, by
  season and hour. No MAPE: prices are often zero or negative. Intervals: coverage of the
  p10-p90 interval (80 % nominal) and pinball loss.
- **Benchmarks.** "Yesterday": the price at the same hour on D. "Week ago": the price at the
  same hour on D-6. On the spring DST day the missing 02:00 falls back to D's base price.

## Choices made on the validation period

| Candidate (with weather forecast) | MAE | RMSE |
|---|---|---|
| Benchmark: yesterday | 22.75 | 35.61 |
| Benchmark: week ago | 25.54 | 39.95 |
| Ridge, alpha 1 / 10 / 100 / **1000** / 10000 | 17.11 / 17.04 / 16.70 / **16.41** / 16.93 | 25.56 / 25.50 / 25.22 / **25.12** / 26.70 |
| LightGBM, L1 loss, price | **13.95** | 22.63 |
| LightGBM, L2 loss, price | 14.52 | 22.36 |
| LightGBM, L1 / L2, deviation from the 7-day same-hour mean | 14.61 / 15.04 | 23.41 / 22.70 |
| LightGBM, L1, 1000 trees instead of 500 | 13.87 | 22.26 |

- **Target:** the price itself. Predicting the deviation from the last week's level was the
  hypothesis (trees cannot extrapolate to unseen price levels), but it was worse for both
  models, so it was dropped.
- **LightGBM:** L1 loss (the metric is MAE), 500 trees, learning rate 0.03, 31 leaves,
  at least 40 rows per leaf, row and column subsampling 0.8. 1000 trees gained under
  0.1 EUR/MWh for twice the training time.
- **Ridge:** alpha 1000, with the hour, weekday and month one-hot and the numbers
  standardised, missing values imputed with the median plus indicators.
- **Intervals:** quantile LightGBM (p10, p50, p90) covered only 69.7 % of the validation
  hours. Conformalizing them (CQR: fit the quantile models without the last 28 training
  days, then widen p10/p90 by the margin that brings those days to 80 %) gave 79.7 %.

## Results on the test period

| Model | Weather | MAE | RMSE | MAE peak | MAE off-peak |
|---|---|---|---|---|---|
| Benchmark: yesterday | - | 32.45 | 49.41 | 38.41 | 26.48 |
| Benchmark: week ago | - | 38.76 | 57.89 | 44.94 | 32.59 |
| Ridge | none | 27.63 | 40.04 | 31.56 | 23.70 |
| Ridge | forecast | 24.10 | 36.20 | 27.24 | 20.97 |
| Ridge | observed (upper bound) | 23.81 | 35.81 | 26.82 | 20.79 |
| LightGBM | none | 25.76 | 39.90 | 29.99 | 21.52 |
| **LightGBM** | **forecast** | **21.30** | **33.91** | **24.63** | **17.97** |
| LightGBM | observed (upper bound) | 21.25 | 33.91 | 24.44 | 18.06 |

- **LightGBM with the weather forecast beats both benchmarks:** MAE 21.30 against 32.45
  for "yesterday" (34 % lower) and 38.76 for "week ago" (45 % lower). It has the lower daily
  MAE on 195 of the 243 days (80 %).
- **The weather forecast is worth 4.5 EUR/MWh of MAE** (25.76 without it). A perfect Czech
  weather forecast adds nothing measurable (21.25), so better weather data for Czechia is not
  where the remaining error is. German forecasts stay forecasts in that experiment, since
  there are no German observations in the warehouse.
- **Ridge** also beats the benchmarks (24.10), 2.8 EUR/MWh behind LightGBM.
- **By season** (LightGBM vs yesterday): spring 18.8 vs 29.4, summer 17.4 vs 28.7, autumn
  31.2 vs 43.0. Autumn 2026 has much higher and more volatile prices than the training
  history.
- **By hour:** night hours 13.7-16.5, midday 23-27 (solar), and the evening ramp at 19:00-20:00
  is hardest (33, vs 46 for the benchmark).
- **Days to note:** the autumn DST day 2026-10-25 (25 hours) 8.3 vs 21.7; the spring DST day
  2026-03-29 31.1 vs 35.4; 2026-05-01 with the -500 EUR/MWh floor 71.5 vs 79.6.

**Intervals** (LightGBM with the weather forecast, conformalized):

| | Coverage of p10-p90 (80 % nominal) | Mean width | Pinball p10 / p50 / p90 |
|---|---|---|---|
| Test period | 76.1 % | 69.2 EUR/MWh | 5.58 / 11.74 / 5.68 |

Without calibration the same models covered about 63 % of the test hours. The calibrated
interval is still a little narrow on the test period: prices in autumn 2026 vary more than
in the four weeks the margin is taken from.

## What drives the forecast

Mean absolute SHAP value on the test hours, in EUR/MWh (LightGBM with the weather
forecast, trained on everything before 2026-03-01; `uv run forecast importance`):

| Feature | Mean \|SHAP\| | Feature | Mean \|SHAP\| |
|---|---|---|---|
| price_same_hour_mean_7d | 18.0 | price_same_hour_week_ago | 4.3 |
| price_same_hour_d | 10.6 | offpeak_d | 3.5 |
| de_wind_100m_ms | 6.5 | day_of_week | 3.4 |
| de_solar_radiation_wm2 | 5.9 | price_same_hour_max_7d | 3.2 |
| is_business_day | 4.9 | de_wind_100m_day_mean_ms | 2.9 |
| cz_radiation_wm2 | 4.5 | cz_temperature_day_mean_c | 1.6 |

Recent prices set the level; German wind and solar, then business days and Czech radiation,
move it hour by hour. German weather matters more than Czech weather, which fits a CZ price that is coupled
with Germany's through SDAC.

## Limitations and honest notes

- The test period was scored more than once: with raw quantile intervals, after adding the
  calibration (its method and 28 days were chosen on the validation period), and after
  rounding the features. None of this changed the chosen model or its settings.
- Differences of about 0.2 EUR/MWh between variants are not meaningful. Float noise of 1e-15
  in the features once moved single hours by up to 8.5 EUR/MWh, because equal values became
  unequal and LightGBM split differently; re-runs moved the MAE of single variants by up to 0.2.
  Features are now rounded to 9 decimals: after rebuilding the mart, 504 of 504 sampled
  hourly forecasts matched exactly.
- Historical weather forecasts come from Open-Meteo's archive. That `previous_dayN` comes
  from the run N days earlier was verified against single runs only from 2026-04-02 on,
  when that archive starts; earlier months are assumed to follow the same rule.
- One hole in the forecast archive (day-2 radiation, 100 m wind and cloud cover on
  2026-04-07..11) is left as missing values; LightGBM handles them, Ridge imputes.
- The model knows nothing about fuel and CO2 prices, cross-border flows or outages; these
  are the likely causes of the autumn error, but that was not measured.

## In production

`.github/workflows/forecast.yml` is scheduled at 01:53, 02:53 and 04:13 UTC. In October 2026
GitHub started these jobs 6-7 hours late, so these times aim to land before the auction
closes at 12:00 Prague time (10:00 UTC in summer, 11:00 in winter) even then. A scheduled
run that does start before 03:20 UTC exits without touching the warehouse, because the
ICON-EU 00Z run reaches Open-Meteo around 03:05 UTC:

1. `pipeline morning` loads today's prices (in case the afternoon run was late) and the
   weather forecasts of the overnight runs.
2. dbt builds `ml.ml_features_hourly` and its upstream models.
3. `forecast predict` checks that tomorrow's features are complete (every hour, today's
   prices, the weather forecast) and fails loudly otherwise, refits LightGBM and the two
   benchmarks on every delivery day up to today, and stores tomorrow's forecast in
   `ml.price_forecast`: price, p10/p50/p90, `issued_at`, `is_late` (issued after the
   cutoff), `model_version` and `code_version` (the commit).

A run that finds tomorrow's forecast already stored stops, so a backup run never replaces
an on-time forecast. A late run still uses only data known at the cutoff, because the
features are defined by the cutoff, not by the time the job runs; `is_late` marks it.

The afternoon `daily.yml` build scores forecasts once OTE publishes the prices:
`ml.ml_forecast_accuracy_hourly` (error per hour, inside the interval or not) and
`ml.ml_forecast_daily_metrics` (MAE, RMSE, bias, coverage per day and model). A source
freshness check on `ml.price_forecast` warns after 26 hours without a new forecast.

`forecast backfill-history --from 2026-03-01` (or the workflow's `history_from` input)
stores the walk-forward forecasts of the same models as `run_type = 'backtest'`, so the
dashboard has a track record from the start; they reproduce the test results above
(MAE about 21.3 vs 32.5).

The dashboard page **Forecast vs actual** reads these tables: the latest forecast with its
interval, the day before against the actual price, the rolling MAE of the model and the
benchmarks, and an accuracy table. Live forecasts replace `backtest` rows for the same day.

## Reproduce

```bash
uv run pipeline backfill --from 2024-01-01      # or keep data/warehouse.duckdb up to date
cd dbt && uv run dbt build && cd ..
uv run forecast validate                         # ~2 minutes
uv run forecast backtest                         # ~20 minutes on 20 cores
uv run forecast importance
uv run forecast report                           # executes forecasting/report.ipynb
```

`uv run forecast report --refresh` runs the three commands above and then the notebook.
Outputs go to `data/forecast/` (predictions as Parquet, metrics as CSV).

# Design decisions

Short records of the decisions that shaped the project: the context, what was chosen,
and what it costs. Newest last.

---

## 1. DuckDB locally, MotherDuck in production

**Context.** A daily pipeline with ~50 MB of history needs a warehouse that GitHub Actions
can write to and a public dashboard can read from, at zero cost.

**Decision.** DuckDB as the engine everywhere: a local file for development and tests,
MotherDuck (hosted DuckDB, free Lite plan) in production. dbt uses `dbt-duckdb` with two
targets of the same SQL dialect.

**Consequences.** No server to run, identical SQL locally and in the cloud, data easy to
export. MotherDuck supports DuckDB only up to 1.5.x, so `duckdb` is pinned to `<1.6`. A file
created with DuckDB 2.0 is unreadable by 1.5, so the local warehouse was migrated through
Parquet. PostgreSQL (Neon) was the alternative; it would add a server, migrations and
`dbt-postgres` without making the project any more capable.

## 2. GitHub Actions as the orchestrator

**Context.** One daily job with four steps (extract + load, freshness, dbt build, record
results) does not justify running Airflow or Dagster.

**Decision.** Scheduled GitHub Actions workflows (`daily`, `backfill`, `docs`) plus `ci` on
every push. Every step after a failure still runs, so all results are recorded, and a
final step fails the run.

**Consequences.** Free and visible in the repository. GitHub delays or drops scheduled runs
at the top of the hour (the first 14:00 UTC run never started, a later one ran three hours
late), so the cron is off-peak (14:17 UTC) with an idempotent backup run at 16:47 UTC.

## 3. Idempotent loads, no PRIMARY KEY on raw tables

**Context.** Retries, the weekly ČHMÚ reload and backfills must never create duplicates.

**Decision.** Every load is `DELETE` + `INSERT` of the incoming natural keys in one
transaction (via an Arrow table). Raw tables carry no PRIMARY KEY: DuckDB checks
constraints eagerly, and a delete + insert of the same key in one transaction fails.
Uniqueness is asserted by dbt tests on the staging models instead.

**Consequences.** Any day can be re-run safely. A row deleted at the source but already
loaded stays until a full reload; that has not happened with these sources.

## 4. Store prices at the traded market time unit

**Context.** The SDAC day-ahead market moved from 60-minute to 15-minute products on
2025-10-01. History is hourly, new data is quarter-hourly.

**Decision.** `fct_dam_prices` keeps one row per traded period with `mtu_minutes`; an hourly
fact (`fct_dam_prices_hourly`) is derived for comparisons across the switch.

**Consequences.** No information is thrown away, and daily indices are exact for both
regimes. Consumers must not average periods of different length within a day; they never
mix inside one delivery day.

## 5. Time: UTC in storage, local time derived explicitly

**Context.** OTE numbers periods 1..N within the Prague delivery day: 92 or 100 quarter-hours
on DST days. DuckDB uses the machine's time zone for casts unless told otherwise, which on a
Prague machine silently shifted daily counts by two hours.

**Decision.** Every session (loader, dbt profile, dashboard) runs with `TimeZone = 'UTC'`.
A period's start is local midnight converted to UTC plus `(period - 1) × MTU`; local time
only ever comes from an explicit `AT TIME ZONE 'Europe/Prague'`. The hourly fact's grain is
the UTC hour, so the repeated local 02:00 on the autumn DST day stays two rows.

**Consequences.** DST days are correct end to end, and tests pin the 92/96/100 counts.

## 6. Fail loudly at the edge, allowlist real anomalies

**Context.** The OTE endpoint is the JSON behind a website chart, not a documented API.

**Decision.** Pydantic validates every response. OTE parsing checks the series titles, the
expected DST-aware period count and that the answer is for the requested day. A short day
fails the load unless it is listed in `ote.KNOWN_INCOMPLETE_DAYS`, with the reason documented.
The same list is a dbt seed, and a pytest test keeps the two equal.

**Consequences.** 2025-07-04, which OTE published only for hours 1–12 (the official XLSX
marks hours 13–24 `ES`), was caught by the validation, checked against the source and
allowlisted. Any new gap still stops the load.

## 7. Prefer quality-checked data, and reload it

**Context.** ČHMÚ publishes fresh 10-minute data in `now/` (quality "not checked") and
quality-controlled data in `recent/` later, and deletes `now/` files after a few days.

**Decision.** Load from `recent/` first and fall back to `now/`; the daily run re-loads the
last 7 days so checked values replace unchecked ones. Physically impossible values (bounds
in a seed) are excluded from aggregates and listed by a warning test.

**Consequences.** The warehouse converges to checked data. One impossible value delivered
as "good" quality (global radiation 1673.7 W/m², Holešov, 2026-09-22) is excluded and visible
on the dashboard's data quality page.

## 8. Test the data against the source of truth

**Context.** Generic dbt tests prove internal consistency, not correctness.

**Decision.** A seed holds OTE's official daily indices (BASE / PEAK / OFFPEAK prices and
volumes) for eight days covering both market time units, all four kinds of DST day and a
negative-price day; a singular test reconciles the marts with them. A second cross-check
requires weekdays without a ČNB fixing to equal Czech public holidays.

**Consequences.** The daily summary matches OTE to the cent. The reconciliation exposed a
quirk in OTE's own reports: on hourly-market DST days, peak *volume* is summed by hour
position 9–20 while peak *price* uses wall-clock 08:00–20:00; the test documents and skips
exactly that comparison.

## 9. CI without data and without network

**Context.** CI must not depend on live APIs or the production warehouse.

**Decision.** Unit tests run on stored real API responses (`tests/fixtures`, refreshed by
`scripts/fetch_fixtures.py`). dbt runs `build --empty` on an empty warehouse, executing every
model and test with zero rows. The dashboard's pages are rendered headlessly with Streamlit
AppTest on that same empty warehouse. Wall-clock and history-dependent tests are tagged
(`freshness`, `needs_history`) and run only in the daily job.

**Consequences.** CI takes under a minute and catches broken SQL, schema drift and dashboard
errors before deployment.

## 10. Forecast the hour, not the quarter-hour

**Context.** Prices have been traded per 15 minutes since 2025-10-01, per hour before. The
forecast needs as much history as possible.

**Decision.** The target is the hourly price of `fct_dam_prices_hourly`: over two and a half
years of comparable history, against about one year of quarter-hours.

**Consequences.** The test covers every season with a long training history, and the model
matches the hourly products most consumers compare. Quarter-hour shape inside the hour is not
forecast; it can be added once more of that history exists.

## 11. Walk-forward evaluation, settings chosen on a separate period

**Context.** A random split would train on the future and flatter the model. Prices also drift
by tens of EUR/MWh within a year, so a single fit-and-score split says little.

**Decision.** Each delivery day is forecast by a model trained only on delivery days up to the
forecast date, then the window moves a day, the same daily refit as in production. Settings are
chosen on 2025-09 to 2026-02 and scored on 2026-03 to 2026-10, against two naive benchmarks. MAE
and RMSE, not MAPE: prices are often zero or negative. The test period has a fixed end, so the
report reproduces the same numbers; later days are the live track record on the dashboard.

**Consequences.** The result is one a production run could have achieved: 34 % lower MAE than the
best benchmark. A plausible hypothesis (predict the deviation from the recent price level) lost on
the validation period and was dropped. The test period was scored more than once: the raw intervals were
visibly overconfident there, so calibration was added and chosen on the validation period, and
features were later rounded. Neither changed the chosen model. This is recorded in the report.

## 12. Features known at the cutoff, never observed weather

**Context.** Observed weather explains prices well, but the weather of D+1 is unknown at 12:00 on
D. Using it would inflate the results without any way to run in production.

**Decision.** `ml_features_hourly` holds what existed at the cutoff, whatever the time of the run:
the calendar, prices of delivery days up to D, and weather forecasts published before the cutoff.
dbt tests check each row's sources against the cutoff, and a pytest test requires identical
features with later data absent, present or altered. Observed weather sits in a separate model,
used once, for an upper bound.

**Consequences.** A late job cannot leak; it is only flagged `is_late`. The upper bound shows the
forecast already captures nearly all of the value of weather: 21.30 against 21.25 with observed
Czech weather. The leak test failed as it should when a leak was introduced on purpose, twice.

## 13. Weather forecasts as issued: Previous Runs, ICON-EU, runs up to 00Z

**Context.** History needs forecasts as they stood then. Open-Meteo's Historical Forecast API
stitches the first hours of each run, which is close to observed weather.

**Decision.** The Previous Runs API, whose `previous_dayN` was matched value by value against
single model runs: for ICON-EU it comes from the run initialised at floor_6h(T - N days), or an
older one. `issued_at` stores that bound. With a 6-hour publication margin only runs up to 00Z of D
are used: day 1 for hours before 06:00 UTC of D+1, day 2 for the rest. ECMWF IFS 0.25° breaks the
rule and is not used.

**Consequences.** Leak safety rests on a stored, tested fact (`tests/test_fixtures.py`), checked
only from 2026-04-02 on, when the single-run archive starts. Most hours use a forecast two days
old, which costs little: radiation MAE 21.7 against 18.8 W/m². Previous_day1 everywhere
would have leaked for most hours.

## 14. Refit every morning, and never replace a forecast made on time

**Context.** A forecast that is not stored as issued cannot be scored honestly, and GitHub starts
scheduled jobs late, 6-7 hours in October 2026.

**Decision.** Fitting takes seconds, so the job refits daily and stores no model files. It writes
`ml.price_forecast` with `issued_at`, `is_late`, the model and code versions. The first
successful run wins; later runs find the forecast and stop. Crons sit early enough for the delay;
a scheduled run that starts before the overnight weather run is out exits. Incomplete inputs fail
the job instead of giving a silently worse forecast.

**Consequences.** Live forecasts stay as issued and are scored by dbt once prices appear.
Depending on GitHub's scheduler stays the weak point: a longer delay means a late forecast,
flagged but too late for the auction.

## 15. Free plans shape the deployment

**Context.** The MotherDuck trial ended on 2026-10-30 and every job failed at connect until a plan
was chosen. The Lite plan has no Read Scaling tokens and caps compute at 10 hours a month.

**Decision.** Stay on Lite. The dashboard uses its own Read/Write token and runs only SELECT
queries. No extra logic to save compute: a measured day of runs takes 7.5 minutes of step time,
about 3.8 hours a month.

**Consequences.** Zero cost, but the dashboard's token could write if it leaked; it lives only in
Streamlit's secrets and can be revoked on its own. How step time maps to billed compute is not
known exactly, and heavy backfills count too.

## 16. Round the features so results reproduce

**Context.** Re-running the whole report after rebuilding the mart moved LightGBM's MAE by up to
0.2 EUR/MWh on identical data, while Ridge did not move. DuckDB sums in parallel, so averages
differ in the 15th digit between builds; equal values become unequal and tree splits change. An
experiment moved single hours by up to 8.5 EUR/MWh with noise of 1e-15.

**Decision.** `features.design` rounds to 9 decimals. Rounding to 6 was tried and failed: some
averages end exactly on a tie in the 7th decimal, so noise still decided their rounding.

**Consequences.** After a full rebuild of the mart, 504 of 504 sampled hourly forecasts match the
stored ones exactly, and a test pins it. It also shows how sensitive trees are, so differences of
about 0.2 EUR/MWh between variants are treated as noise. Revised source data can still move results.

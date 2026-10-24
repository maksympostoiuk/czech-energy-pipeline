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

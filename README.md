# Czech Energy Data Pipeline

Daily pipeline for the Czech electricity market: day-ahead prices from **OTE**, EUR/CZK rates from **ČNB** and weather observations from **ČHMÚ**, loaded with Python into DuckDB, modelled with **dbt** (staging → marts) with data quality tests, scheduled by **GitHub Actions** and visualised in **Streamlit**.

> Work in progress. See [docs/PLAN.md](docs/PLAN.md) for the architecture and roadmap.

## Getting started

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # create .venv with Python 3.12 and all dependencies
cp .env.example .env          # local settings; secrets stay out of git
uv run pre-commit install     # lint and format on every commit
uv run pytest
uv run pipeline config        # show which database the pipeline will use
```

## Running the pipeline

```bash
uv run pipeline run --date 2026-10-21              # all sources for one business date
uv run pipeline run --date 2026-10-21 --source ote # one source
uv run pipeline daily                              # the scheduled run
uv run pipeline backfill --from 2024-01-01         # history up to today (~15 min)
uv run pipeline backfill --from 2024-01-01 --resume  # continue an interrupted backfill
```

`daily` loads tomorrow's and today's day-ahead prices, today's ČNB fixing and re-loads the last
7 days of ČHMÚ observations to pick up quality-control corrections. Every load is an idempotent
upsert on the natural key, and every task is logged in `ops.pipeline_runs`. The command exits
non-zero if any task failed. "Not published yet" (for example, tomorrow's prices before 13:00 CET)
is logged as `no_data` and is not a failure.

| Table | Natural key | Rows per day |
|---|---|---|
| `raw.ote_dam_results` | `delivery_date, period` | 96 (92/100 on DST days) |
| `raw.cnb_fx_rates` | `valid_for, currency_code` | 30 on business days |
| `raw.chmi_observations` | `station_id, element, observed_at` | 8 stations × 6 elements × 144 = 6,912 |
| `raw.chmi_stations` | `station_id` | station list snapshot |

## Transformations (dbt)

```bash
cd dbt
uv run dbt deps               # dbt_utils
uv run dbt build              # seeds, models and tests against data/warehouse.duckdb
uv run dbt docs generate && uv run dbt docs serve   # lineage graph and column docs
```

| Layer | Models | Materialization |
|---|---|---|
| reference (seeds) | Czech public holidays, selected stations, known incomplete OTE days | table |
| staging | `stg_ote__dam_prices`, `stg_cnb__fx_rates`, `stg_chmi__observations`, `stg_chmi__stations`, `stg_ops__pipeline_runs` | view |
| intermediate | `int_fx_eur_czk_daily`, `int_dam_prices_czk`, `int_weather_hourly_station`, `int_weather_hourly_cz` | view |
| marts | `dim_date`, `dim_station`, `fct_dam_prices` (incremental), `fct_dam_prices_hourly`, `fct_daily_market_summary`, `mart_price_vs_weather` | table |

`fct_daily_market_summary` reproduces OTE's official BASE / PEAK / OFFPEAK indices and daily
volumes to the cent, for both the hourly (before 2025-10-01) and the 15-minute market.

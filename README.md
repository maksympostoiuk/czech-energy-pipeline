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

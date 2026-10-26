"""The price forecast's features must be known at the cutoff (12:00 Prague time on D).

A small warehouse of synthetic raw data is transformed by the real dbt models. The
features of delivery day D+1 are then compared across three warehouses that differ only in
what appeared after the cutoff: nothing yet (a run on time), everything (a late run), and
everything with different values. They must be identical. A control checks the opposite:
changing data that was known at the cutoff does change them.

D+1 is 2026-10-25, a 25-hour DST day, so the hour handling is exercised too.
"""

import math
import random
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from dbt.adapters.duckdb.connections import DuckDBConnectionManager
from dbt.cli.main import dbtRunner

from energy_pipeline.extract import openmeteo, ote
from energy_pipeline.load.warehouse import (
    CNB_FX_RATES,
    OPENMETEO_FORECASTS,
    OTE_DAM_RESULTS,
    Warehouse,
)

DBT_DIR = Path(__file__).parent.parent / "dbt"
FORECAST_DATE = date(2026, 10, 24)
DELIVERY_DATE = date(2026, 10, 25)
CUTOFF = datetime(2026, 10, 24, 10, tzinfo=UTC)  # 12:00 CEST
# Same assumption as the dbt var openmeteo_publication_delay_hours (docs/sources.md).
PUBLICATION_DELAY = timedelta(hours=6)
FIRST_DAY, LAST_DAY = date(2026, 10, 15), date(2026, 10, 27)
RAW_TABLES = {table.name: table for table in (OTE_DAM_RESULTS, CNB_FX_RATES, OPENMETEO_FORECASTS)}


def synthetic_rows() -> dict[str, list[dict]]:
    rng = random.Random(42)
    days = [FIRST_DAY + timedelta(days=n) for n in range((LAST_DAY - FIRST_DAY).days + 1)]
    prices = [
        {
            "delivery_date": day,
            "period": period,
            "mtu_minutes": 15,
            "price_eur_mwh": round(rng.uniform(-50, 300), 2),
            "price_60min_ref_eur_mwh": None,
            "volume_mwh": round(rng.uniform(500, 1500), 3),
        }
        for day in days
        for period in range(1, ote.periods_in_day(day, 15) + 1)
    ]
    fx = [
        {
            "valid_for": day,
            "currency_code": "EUR",
            "amount": 1,
            "rate": round(24 + rng.random(), 3),
            "country": "EMU",
            "currency": "euro",
        }
        for day in days
    ]
    start = datetime(2026, 10, 13, tzinfo=UTC)
    forecasts = [
        {
            "point_id": point.point_id,
            "model": openmeteo.MODEL,
            "valid_at": start + timedelta(hours=hour),
            "lead_days": lead,
            "issued_at": openmeteo.issued_at(start + timedelta(hours=hour), lead),
            "temperature_2m_c": round(rng.uniform(-5, 20), 1),
            "shortwave_radiation_wm2": round(rng.uniform(0, 600), 1),
            "wind_speed_10m_ms": round(rng.uniform(0, 12), 2),
            "wind_speed_100m_ms": round(rng.uniform(0, 25), 2),
            "cloud_cover_pct": float(rng.randint(0, 100)),
        }
        for point in openmeteo.POINTS
        for hour in range(16 * 24)
        for lead in openmeteo.LEAD_DAYS
    ]
    return {
        OTE_DAM_RESULTS.name: prices,
        CNB_FX_RATES.name: fx,
        OPENMETEO_FORECASTS.name: forecasts,
    }


# What appeared after the cutoff, per raw table: D+1 prices (published ~13:00 on D), the
# ČNB fixing of D (14:30), and forecast runs published after it.
AFTER_CUTOFF = {
    OTE_DAM_RESULTS.name: lambda row: row["delivery_date"] > FORECAST_DATE,
    CNB_FX_RATES.name: lambda row: row["valid_for"] >= FORECAST_DATE,
    OPENMETEO_FORECASTS.name: lambda row: row["issued_at"] + PUBLICATION_DELAY > CUTOFF,
}
VALUE_COLUMNS = {
    OTE_DAM_RESULTS.name: ["price_eur_mwh", "volume_mwh"],
    CNB_FX_RATES.name: ["rate"],
    OPENMETEO_FORECASTS.name: [
        "temperature_2m_c",
        "shortwave_radiation_wm2",
        "wind_speed_10m_ms",
        "wind_speed_100m_ms",
        "cloud_cover_pct",
    ],
}


def changed(row: dict, table: str) -> dict:
    return {**row, **{column: row[column] + 333 for column in VALUE_COLUMNS[table]}}


def same(rows: list[tuple], others: list[tuple]) -> bool:
    """Equal, up to float rounding: DuckDB sums in parallel, so the last bits can vary."""
    return len(rows) == len(others) and all(
        math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) if isinstance(a, float) else a == b
        for row, other in zip(rows, others, strict=True)
        for a, b in zip(row, other, strict=True)
    )


def release_duckdb() -> None:
    """dbt-duckdb keeps its database open between in-process invocations; close it so the
    file can be copied and read (private API of dbt-duckdb 1.11)."""
    with DuckDBConnectionManager._LOCK:
        if DuckDBConnectionManager._ENV is not None:
            DuckDBConnectionManager._ENV.close()
            DuckDBConnectionManager._ENV = None


@pytest.fixture(scope="module")
def run_dbt(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., None]:
    """dbt in this process: the project is parsed once, then every run reuses the manifest."""
    work = tmp_path_factory.mktemp("dbt")
    runner = dbtRunner()

    def run(*args: str, database: Path) -> None:
        nonlocal runner
        with pytest.MonkeyPatch.context() as env:
            env.setenv("DBT_DUCKDB_PATH", str(database))
            env.delenv("DBT_TARGET", raising=False)  # the local target, never MotherDuck
            result = runner.invoke(
                [
                    *args,
                    "--project-dir",
                    str(DBT_DIR),
                    "--profiles-dir",
                    str(DBT_DIR),
                    "--target-path",
                    str(work / "target"),
                    "--log-path",
                    str(work / "logs"),
                    "--quiet",
                ]
            )
        release_duckdb()
        assert result.success, result.exception
        if args[0] == "parse":
            runner = dbtRunner(manifest=result.result)

    if not (DBT_DIR / "dbt_packages").exists():
        run("deps", database=work / "warehouse.duckdb")
    run("parse", database=work / "warehouse.duckdb")
    return run


@pytest.fixture(scope="module")
def build_features(
    run_dbt: Callable[..., None], tmp_path_factory: pytest.TempPathFactory
) -> Callable[..., list[tuple]]:
    """Load raw rows into a fresh warehouse, run dbt, return the features of D+1."""
    seeded = tmp_path_factory.mktemp("seeded") / "warehouse.duckdb"
    Warehouse(str(seeded)).con.close()
    run_dbt("seed", "--select", "+ml_features_hourly", database=seeded)

    def build(rows: dict[str, list[dict]]) -> list[tuple]:
        path = tmp_path_factory.mktemp("warehouse") / "warehouse.duckdb"
        shutil.copy(seeded, path)  # views reference the database by its file name
        with Warehouse(str(path)) as wh:
            for table, table_rows in rows.items():
                wh.upsert(RAW_TABLES[table], table_rows, "test")
        run_dbt("run", "--select", "+ml_features_hourly", "--full-refresh", database=path)
        with duckdb.connect(str(path), read_only=True) as con:
            con.execute("SET TimeZone = 'UTC'")
            return con.execute(
                """
                select * exclude (price_eur_mwh)
                from ml.ml_features_hourly
                where delivery_date = ?
                order by hour_start_utc
                """,
                [DELIVERY_DATE],
            ).fetchall()

    return build


@pytest.fixture(scope="module")
def late_run(build_features) -> list[tuple]:
    return build_features(synthetic_rows())


def test_features_ignore_everything_after_the_cutoff(build_features, late_run) -> None:
    rows = synthetic_rows()
    on_time = {
        table: [row for row in table_rows if not AFTER_CUTOFF[table](row)]
        for table, table_rows in rows.items()
    }
    late_and_revised = {
        table: [changed(row, table) if AFTER_CUTOFF[table](row) else row for row in table_rows]
        for table, table_rows in rows.items()
    }

    assert len(late_run) == 25  # the autumn DST day
    assert all(None not in row for row in late_run), "every feature is filled"
    assert same(build_features(on_time), late_run)
    assert same(build_features(late_and_revised), late_run)


def test_features_do_change_with_data_known_at_the_cutoff(build_features, late_run) -> None:
    """The control: the comparison above is not vacuous."""
    rows = synthetic_rows()
    known = {
        table: [changed(row, table) if not AFTER_CUTOFF[table](row) else row for row in table_rows]
        for table, table_rows in rows.items()
    }

    assert not same(build_features(known), late_run)

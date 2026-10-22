from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from energy_pipeline.extract import chmi, ote, to_rows
from energy_pipeline.load.warehouse import (
    CHMI_OBSERVATIONS,
    OTE_DAM_RESULTS,
    RAW_TABLES,
    Warehouse,
)


@pytest.fixture
def warehouse() -> Iterator[Warehouse]:
    with Warehouse(":memory:") as wh:
        yield wh


@pytest.fixture
def dam_rows(load_fixture) -> list[dict]:
    periods = ote.parse_dam(load_fixture("ote/dam_2026-10-21_15min.json"), date(2026, 10, 21))
    return to_rows(periods)


def count(warehouse: Warehouse, table: str) -> int:
    return warehouse.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_schema_is_created(warehouse: Warehouse) -> None:
    tables = {
        f"{schema}.{name}"
        for schema, name in warehouse.con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables()"
        ).fetchall()
    }

    assert {t.name for t in RAW_TABLES} | {"ops.pipeline_runs"} <= tables


def test_upsert_is_idempotent(warehouse: Warehouse, dam_rows: list[dict]) -> None:
    assert warehouse.upsert(OTE_DAM_RESULTS, dam_rows, run_id="first") == 96
    warehouse.upsert(OTE_DAM_RESULTS, dam_rows, run_id="second")

    assert count(warehouse, OTE_DAM_RESULTS.name) == 96
    run_ids = warehouse.con.execute(
        f"SELECT DISTINCT _run_id FROM {OTE_DAM_RESULTS.name}"
    ).fetchall()
    assert run_ids == [("second",)]


def test_upsert_replaces_changed_values(warehouse: Warehouse, dam_rows: list[dict]) -> None:
    warehouse.upsert(OTE_DAM_RESULTS, dam_rows, run_id="first")
    corrected = [{**dam_rows[0], "price_eur_mwh": -10.0}]
    warehouse.upsert(OTE_DAM_RESULTS, corrected, run_id="correction")

    price = warehouse.con.execute(
        f"SELECT price_eur_mwh FROM {OTE_DAM_RESULTS.name} WHERE period = 1"
    ).fetchone()[0]
    assert price == -10.0
    assert count(warehouse, OTE_DAM_RESULTS.name) == 96


def test_upsert_keeps_other_keys(warehouse: Warehouse, dam_rows: list[dict]) -> None:
    warehouse.upsert(OTE_DAM_RESULTS, dam_rows, run_id="day1")
    next_day = [{**row, "delivery_date": date(2026, 10, 22)} for row in dam_rows]
    warehouse.upsert(OTE_DAM_RESULTS, next_day, run_id="day2")

    assert count(warehouse, OTE_DAM_RESULTS.name) == 192


def test_upsert_empty_is_noop(warehouse: Warehouse) -> None:
    assert warehouse.upsert(OTE_DAM_RESULTS, [], run_id="x") == 0


def test_observations_keep_utc_and_missing_values(warehouse: Warehouse, load_fixture) -> None:
    payload = load_fixture("chmi/10m-0-20000-0-11520-20261021.json")
    rows = to_rows(chmi.parse_observations(payload, "0-20000-0-11520", "recent"))

    warehouse.upsert(CHMI_OBSERVATIONS, rows, run_id="r")

    first, missing = warehouse.con.execute(
        f"SELECT min(observed_at), count(*) FILTER (value IS NULL) FROM {CHMI_OBSERVATIONS.name}"
    ).fetchone()
    assert first == datetime(2026, 10, 21, tzinfo=UTC)
    assert missing == sum(row["value"] is None for row in rows) > 0


def test_log_run(warehouse: Warehouse) -> None:
    warehouse.log_run(
        run_id="r",
        source="ote",
        business_date=date(2026, 10, 21),
        status="failed",
        rows_loaded=0,
        started_at=datetime.now(UTC),
        error="boom",
    )

    assert warehouse.con.execute(
        "SELECT source, status, error FROM ops.pipeline_runs"
    ).fetchall() == [("ote", "failed", "boom")]

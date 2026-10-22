from collections.abc import Iterator
from datetime import date

import httpx
import pytest
import respx

from energy_pipeline.extract import chmi, cnb, ote
from energy_pipeline.http import make_client
from energy_pipeline.load.warehouse import Warehouse
from energy_pipeline.pipeline import SOURCES, Task, daily_tasks, run_tasks

DAY = date(2026, 10, 21)
TODAY = date(2026, 10, 22)


@pytest.fixture
def warehouse() -> Iterator[Warehouse]:
    with Warehouse(":memory:") as wh:
        yield wh


@pytest.fixture
def api(load_fixture) -> Iterator[respx.MockRouter]:
    """All three sources answering with the stored real responses for DAY."""
    with respx.mock(assert_all_called=False) as router:
        router.get(ote.URL).mock(
            return_value=httpx.Response(200, json=load_fixture("ote/dam_2026-10-21_15min.json"))
        )
        router.get(cnb.DAILY_URL).mock(
            return_value=httpx.Response(200, json=load_fixture("cnb/daily_2026-10-16_friday.json"))
        )
        router.get(chmi.STATIONS_URL.format(day=TODAY)).mock(
            return_value=httpx.Response(200, json=load_fixture("chmi/meta1-stations.json"))
        )
        # Every station gets Libuš' file re-labelled, which is enough for loading logic.
        libus_day = load_fixture("chmi/10m-0-20000-0-11520-20261021.json")
        for station_id in chmi.STATIONS:
            payload = {**libus_day, "data": {**libus_day["data"]}}
            payload["data"]["data"] = {
                **libus_day["data"]["data"],
                "values": [[station_id, *row[1:]] for row in libus_day["data"]["data"]["values"]],
            }
            router.get(chmi.DAY_FILE_URLS["recent"].format(wsi=station_id, day=DAY)).mock(
                return_value=httpx.Response(200, json=payload)
            )
        yield router


def run(warehouse: Warehouse, tasks: list[Task]):
    with make_client() as client:
        return run_tasks(tasks, client, warehouse, TODAY)


def counts(warehouse: Warehouse) -> dict[str, int]:
    tables = ("ote_dam_results", "cnb_fx_rates", "chmi_observations", "chmi_stations")
    return {
        table: warehouse.con.execute(f"SELECT count(*) FROM raw.{table}").fetchone()[0]
        for table in tables
    }


@pytest.mark.usefixtures("api")
def test_run_loads_all_sources_idempotently(warehouse: Warehouse) -> None:
    tasks = [Task(source, DAY) for source in SOURCES]

    first = run(warehouse, tasks)
    after_first = counts(warehouse)
    run(warehouse, tasks)

    assert [r.status for r in first] == ["success"] * 3
    assert after_first["ote_dam_results"] == 96
    assert after_first["cnb_fx_rates"] == 30
    assert after_first["chmi_observations"] == len(chmi.STATIONS) * len(chmi.ELEMENTS) * 144
    assert after_first["chmi_stations"] > 0
    assert counts(warehouse) == after_first
    assert warehouse.con.execute("SELECT count(*) FROM ops.pipeline_runs").fetchone()[0] == 6


def test_failed_source_does_not_stop_others(warehouse: Warehouse, api: respx.MockRouter) -> None:
    api.get(ote.URL).mock(return_value=httpx.Response(200, json={"unexpected": True}))

    results = run(warehouse, [Task("ote", DAY), Task("cnb", DAY)])

    assert [r.status for r in results] == ["failed", "success"]
    assert "ValidationError" in results[0].error
    logged = warehouse.con.execute(
        "SELECT source, status FROM ops.pipeline_runs ORDER BY source"
    ).fetchall()
    assert logged == [("cnb", "success"), ("ote", "failed")]


def test_unpublished_prices_are_no_data(
    warehouse: Warehouse, api: respx.MockRouter, load_fixture
) -> None:
    api.get(ote.URL).mock(
        return_value=httpx.Response(200, json=load_fixture("ote/dam_2026-10-30_not_published.json"))
    )

    [result] = run(warehouse, [Task("ote", date(2026, 10, 30))])

    assert (result.status, result.rows_loaded) == ("no_data", 0)


def test_daily_tasks() -> None:
    tasks = daily_tasks(TODAY)

    assert Task("ote", date(2026, 10, 23)) in tasks
    assert Task("cnb", TODAY) in tasks
    chmi_days = sorted(t.business_date for t in tasks if t.source == "chmi")
    assert chmi_days[0] == date(2026, 10, 15)
    assert chmi_days[-1] == date(2026, 10, 21)

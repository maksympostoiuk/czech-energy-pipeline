"""Run extract + load tasks and record every task in ops.pipeline_runs.

A task is one source for one business date. Tasks are independent: one failing source
does not stop the others, and the caller decides the exit code from the results.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import uuid4

import httpx

from energy_pipeline.extract import chmi, cnb, ote, to_rows
from energy_pipeline.load.warehouse import (
    CHMI_OBSERVATIONS,
    CHMI_STATIONS,
    CNB_FX_RATES,
    OTE_DAM_RESULTS,
    Warehouse,
)

log = logging.getLogger(__name__)

Source = Literal["ote", "cnb", "chmi"]
SOURCES: tuple[Source, ...] = ("ote", "cnb", "chmi")
CHMI_RELOAD_DAYS = 7  # quality-control corrections arrive in recent/ for about a week


@dataclass(frozen=True)
class Task:
    source: Source
    business_date: date


@dataclass(frozen=True)
class TaskResult:
    task: Task
    status: Literal["success", "no_data", "failed"]
    rows_loaded: int
    error: str | None = None


def load_ote(client: httpx.Client, warehouse: Warehouse, day: date, run_id: str) -> int:
    return warehouse.upsert(OTE_DAM_RESULTS, to_rows(ote.fetch_dam(client, day)), run_id)


def load_cnb(client: httpx.Client, warehouse: Warehouse, day: date, run_id: str) -> int:
    return warehouse.upsert(CNB_FX_RATES, to_rows(cnb.fetch_daily(client, day)), run_id)


def load_chmi(client: httpx.Client, warehouse: Warehouse, day: date, run_id: str) -> int:
    rows = 0
    for station_id in chmi.STATIONS:
        observations = chmi.fetch_station_day(client, station_id, day)
        rows += warehouse.upsert(CHMI_OBSERVATIONS, to_rows(observations), run_id)
    return rows


LOADERS: dict[Source, Callable[[httpx.Client, Warehouse, date, str], int]] = {
    "ote": load_ote,
    "cnb": load_cnb,
    "chmi": load_chmi,
}


def daily_tasks(today: date) -> list[Task]:
    """The scheduled run, see docs/sources.md section 4."""
    return [
        Task("ote", today + timedelta(days=1)),
        Task("ote", today),
        Task("cnb", today),
        *(Task("chmi", today - timedelta(days=n)) for n in range(1, CHMI_RELOAD_DAYS + 1)),
    ]


def run_tasks(
    tasks: Iterable[Task], client: httpx.Client, warehouse: Warehouse, today: date
) -> list[TaskResult]:
    run_id = uuid4().hex
    tasks = list(tasks)
    log.info("run %s: %d tasks", run_id, len(tasks))

    if any(task.source == "chmi" for task in tasks):
        # Station metadata changes rarely; observations do not depend on it, so a
        # failure here is logged and the run goes on.
        try:
            stations = chmi.fetch_stations(client, today)
            warehouse.upsert(CHMI_STATIONS, to_rows(stations), run_id)
        except Exception:
            log.exception("ČHMÚ station metadata failed")

    results = []
    for task in tasks:
        started_at = datetime.now(UTC)
        try:
            rows = LOADERS[task.source](client, warehouse, task.business_date, run_id)
            result = TaskResult(task, "success" if rows else "no_data", rows)
        except Exception as error:
            log.exception("%s %s failed", task.source, task.business_date)
            result = TaskResult(task, "failed", 0, f"{type(error).__name__}: {error}")
        warehouse.log_run(
            run_id=run_id,
            source=task.source,
            business_date=task.business_date,
            status=result.status,
            rows_loaded=result.rows_loaded,
            started_at=started_at,
            error=result.error,
        )
        log.info(
            "%-4s %s  %-7s %5d rows",
            task.source,
            task.business_date,
            result.status,
            result.rows_loaded,
        )
        results.append(result)
    return results

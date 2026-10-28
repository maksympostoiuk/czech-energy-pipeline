"""Run extract + load tasks and record every task in ops.pipeline_runs.

A task is one source for one day (the scheduled run) or one period (backfill). Tasks are
independent: one failing source does not stop the others, and the caller decides the
exit code from the results.
"""

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import uuid4

import httpx

from energy_pipeline.extract import chmi, cnb, openmeteo, ote, to_rows
from energy_pipeline.load.warehouse import (
    CHMI_OBSERVATIONS,
    CHMI_STATIONS,
    CNB_FX_RATES,
    OPENMETEO_FORECASTS,
    OTE_DAM_RESULTS,
    Warehouse,
)

log = logging.getLogger(__name__)

Source = Literal["ote", "cnb", "chmi", "openmeteo"]
SOURCES: tuple[Source, ...] = ("ote", "cnb", "chmi", "openmeteo")
CHMI_RELOAD_DAYS = 7  # quality-control corrections arrive in recent/ for about a week
# Open-Meteo: tomorrow's hours, plus the last two days again. A value whose run was not out
# yet at load time is filled from an older run; the reload replaces it with the final one.
OPENMETEO_RELOAD_DAYS = 2
# A backfill loads ČNB from this many days earlier, so the first delivery days (e.g. the
# 1 January holiday) have a preceding fixing to carry forward.
CNB_BACKFILL_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class Task:
    source: Source
    business_date: date
    until: date | None = None  # inclusive end of a backfill period; None = one day

    @property
    def period_end(self) -> date:
        return self.until or self.business_date

    def days(self) -> Iterator[date]:
        day = self.business_date
        while day <= self.period_end:
            yield day
            day += timedelta(days=1)


@dataclass(frozen=True)
class TaskResult:
    task: Task
    status: Literal["success", "no_data", "failed"]
    rows_loaded: int
    error: str | None = None


def load_ote(client: httpx.Client, warehouse: Warehouse, task: Task, run_id: str) -> int:
    return sum(
        warehouse.upsert(OTE_DAM_RESULTS, to_rows(ote.fetch_dam(client, day)), run_id)
        for day in task.days()
    )


def load_cnb(client: httpx.Client, warehouse: Warehouse, task: Task, run_id: str) -> int:
    if task.until is None:
        return warehouse.upsert(
            CNB_FX_RATES, to_rows(cnb.fetch_daily(client, task.business_date)), run_id
        )
    rows = 0
    for year in range(task.business_date.year, task.period_end.year + 1):
        rates = [
            rate
            for rate in cnb.fetch_year(client, year)
            if task.business_date <= rate.valid_for <= task.period_end
        ]
        rows += warehouse.upsert(CNB_FX_RATES, to_rows(rates), run_id)
    return rows


def load_chmi(client: httpx.Client, warehouse: Warehouse, task: Task, run_id: str) -> int:
    rows = 0
    for station_id in chmi.STATIONS:
        if task.until is None:
            observations = chmi.fetch_station_day(client, station_id, task.business_date)
        else:
            observations = chmi.fetch_station_range(
                client, station_id, task.business_date, task.until
            )
        rows += warehouse.upsert(CHMI_OBSERVATIONS, to_rows(observations), run_id)
    return rows


def load_openmeteo(client: httpx.Client, warehouse: Warehouse, task: Task, run_id: str) -> int:
    forecasts = openmeteo.fetch_forecasts(client, task.business_date, task.period_end)
    return warehouse.upsert(OPENMETEO_FORECASTS, to_rows(forecasts), run_id)


LOADERS: dict[Source, Callable[[httpx.Client, Warehouse, Task, str], int]] = {
    "ote": load_ote,
    "cnb": load_cnb,
    "chmi": load_chmi,
    "openmeteo": load_openmeteo,
}


def daily_tasks(today: date) -> list[Task]:
    """The scheduled run, see docs/sources.md section 4."""
    return [
        Task("ote", today + timedelta(days=1)),
        Task("ote", today),
        Task("cnb", today),
        *(Task("chmi", today - timedelta(days=n)) for n in range(1, CHMI_RELOAD_DAYS + 1)),
        Task(
            "openmeteo",
            today - timedelta(days=OPENMETEO_RELOAD_DAYS),
            today + timedelta(days=1),
        ),
    ]


def morning_tasks(today: date) -> list[Task]:
    """Before the price forecast: today's prices (in case yesterday's run was late) and the
    weather forecasts of the runs published overnight."""
    return [
        Task("ote", today),
        Task(
            "openmeteo",
            today - timedelta(days=OPENMETEO_RELOAD_DAYS),
            today + timedelta(days=1),
        ),
    ]


def _periods(
    start: date, end: date, next_start: Callable[[date], date]
) -> Iterator[tuple[date, date]]:
    while start <= end:
        following = next_start(start)
        yield start, min(end, following - timedelta(days=1))
        start = following


def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def _next_year(day: date) -> date:
    return date(day.year + 1, 1, 1)


def backfill_tasks(start: date, end: date, sources: Iterable[Source] = SOURCES) -> list[Task]:
    """Split start..end into the chunks each source serves best (docs/sources.md section 4):
    OTE per day, ČNB per calendar year, ČHMÚ and Open-Meteo per calendar month."""
    tasks: list[Task] = []
    for source in sources:
        if source == "ote":
            tasks += [Task("ote", day) for day in Task("ote", start, end).days()]
        elif source == "cnb":
            cnb_start = start - timedelta(days=CNB_BACKFILL_LOOKBACK_DAYS)
            tasks += [Task("cnb", s, e) for s, e in _periods(cnb_start, end, _next_year)]
        else:
            tasks += [Task(source, s, e) for s, e in _periods(start, end, _next_month)]
    return tasks


def pending(tasks: Iterable[Task], warehouse: Warehouse) -> list[Task]:
    """Drop tasks that already succeeded, so an interrupted backfill can resume."""
    done = warehouse.succeeded_periods()
    return [t for t in tasks if (t.source, t.business_date, t.period_end) not in done]


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
    for number, task in enumerate(tasks, start=1):
        started_at = datetime.now(UTC)
        try:
            rows = LOADERS[task.source](client, warehouse, task, run_id)
            result = TaskResult(task, "success" if rows else "no_data", rows)
        except Exception as error:
            log.exception("%s %s failed", task.source, task.business_date)
            result = TaskResult(task, "failed", 0, f"{type(error).__name__}: {error}")
        warehouse.log_run(
            run_id=run_id,
            source=task.source,
            business_date=task.business_date,
            period_end=task.period_end,
            status=result.status,
            rows_loaded=result.rows_loaded,
            started_at=started_at,
            error=result.error,
        )
        log.info(
            "[%d/%d] %-4s %s..%s  %-7s %7d rows",
            number,
            len(tasks),
            task.source,
            task.business_date,
            task.period_end,
            result.status,
            result.rows_loaded,
        )
        results.append(result)
    return results

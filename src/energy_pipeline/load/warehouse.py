"""DuckDB warehouse: raw tables, idempotent upserts and the run log.

Raw tables have no PRIMARY KEY on purpose: DuckDB checks constraints eagerly, so a
DELETE + INSERT of the same key inside one transaction fails. Uniqueness of the natural
keys is asserted by dbt tests on the staging models instead.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import duckdb
import pyarrow as pa

from energy_pipeline.config import Settings

LOAD_COLUMNS = "_run_id VARCHAR NOT NULL, _ingested_at TIMESTAMPTZ NOT NULL"


@dataclass(frozen=True)
class RawTable:
    name: str
    key: tuple[str, ...]
    columns: str

    @property
    def ddl(self) -> str:
        return f"CREATE TABLE IF NOT EXISTS {self.name} ({self.columns}, {LOAD_COLUMNS})"


OTE_DAM_RESULTS = RawTable(
    name="raw.ote_dam_results",
    key=("delivery_date", "period"),
    columns="""
        delivery_date DATE NOT NULL,
        period SMALLINT NOT NULL,
        mtu_minutes SMALLINT NOT NULL,
        price_eur_mwh DOUBLE NOT NULL,
        price_60min_ref_eur_mwh DOUBLE,
        volume_mwh DOUBLE NOT NULL
    """,
)
CNB_FX_RATES = RawTable(
    name="raw.cnb_fx_rates",
    key=("valid_for", "currency_code"),
    columns="""
        valid_for DATE NOT NULL,
        currency_code VARCHAR NOT NULL,
        amount INTEGER NOT NULL,
        rate DOUBLE NOT NULL,
        country VARCHAR,
        currency VARCHAR
    """,
)
CHMI_OBSERVATIONS = RawTable(
    name="raw.chmi_observations",
    key=("station_id", "element", "observed_at"),
    columns="""
        station_id VARCHAR NOT NULL,
        element VARCHAR NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        value DOUBLE,
        flag VARCHAR,
        quality TINYINT NOT NULL,
        source_folder VARCHAR NOT NULL
    """,
)
CHMI_STATIONS = RawTable(
    name="raw.chmi_stations",
    key=("station_id",),
    columns="""
        station_id VARCHAR NOT NULL,
        gh_id VARCHAR,
        full_name VARCHAR NOT NULL,
        longitude DOUBLE,
        latitude DOUBLE,
        elevation_m DOUBLE,
        begin_date DATE
    """,
)
OPENMETEO_FORECASTS = RawTable(
    name="raw.openmeteo_forecasts",
    key=("point_id", "model", "valid_at", "lead_days"),
    columns="""
        point_id VARCHAR NOT NULL,
        model VARCHAR NOT NULL,
        valid_at TIMESTAMPTZ NOT NULL,
        lead_days TINYINT NOT NULL,
        issued_at TIMESTAMPTZ NOT NULL,
        temperature_2m_c DOUBLE,
        shortwave_radiation_wm2 DOUBLE,
        wind_speed_10m_ms DOUBLE,
        wind_speed_100m_ms DOUBLE,
        cloud_cover_pct DOUBLE
    """,
)
DBT_RESULTS = RawTable(
    name="ops.dbt_results",
    key=("invocation_id", "unique_id"),
    columns="""
        invocation_id VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        unique_id VARCHAR NOT NULL,
        resource_type VARCHAR NOT NULL,
        name VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        failures INTEGER,
        execution_time_s DOUBLE,
        message VARCHAR
    """,
)
RAW_TABLES = (
    OTE_DAM_RESULTS,
    CNB_FX_RATES,
    CHMI_OBSERVATIONS,
    CHMI_STATIONS,
    OPENMETEO_FORECASTS,
    DBT_RESULTS,
)

PIPELINE_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    business_date DATE NOT NULL,  -- first day of the loaded period
    period_end DATE NOT NULL,  -- last day; equals business_date for daily tasks
    status VARCHAR NOT NULL,  -- success | no_data | failed
    rows_loaded INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    error VARCHAR
)
"""


class Warehouse:
    def __init__(self, database: str, config: dict[str, str] | None = None) -> None:
        """database: a local file, ":memory:" or "md:<name>" for MotherDuck."""
        if database.startswith("md:"):
            # Connect to the account first: the database may not exist yet on a first run.
            name = database.removeprefix("md:")
            self.con = duckdb.connect("md:", config=config or {})
            self.con.execute(f"CREATE DATABASE IF NOT EXISTS {name}")
            self.con.execute(f"USE {name}")
        else:
            if database != ":memory:":
                Path(database).parent.mkdir(parents=True, exist_ok=True)
            self.con = duckdb.connect(database, config=config or {})
        self.con.execute("SET TimeZone = 'UTC'")
        self.create_schema()

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.database, settings.duckdb_config)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.con.close()

    def create_schema(self) -> None:
        self.con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        self.con.execute("CREATE SCHEMA IF NOT EXISTS ops")
        for table in RAW_TABLES:
            self.con.execute(table.ddl)
        self.con.execute(PIPELINE_RUNS_DDL)

    def upsert(self, table: RawTable, rows: list[dict], run_id: str) -> int:
        """Replace rows with the same natural key, so re-running a day never duplicates it."""
        if not rows:
            return 0
        ingested_at = datetime.now(UTC)
        incoming = pa.Table.from_pylist(
            [{**row, "_run_id": run_id, "_ingested_at": ingested_at} for row in rows]
        )
        key_match = " AND ".join(f"target.{column} = incoming.{column}" for column in table.key)

        self.con.register("incoming", incoming)
        try:
            self.con.begin()
            self.con.execute(f"DELETE FROM {table.name} AS target USING incoming WHERE {key_match}")
            self.con.execute(f"INSERT INTO {table.name} BY NAME SELECT * FROM incoming")
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise
        finally:
            self.con.unregister("incoming")
        return len(rows)

    def log_run(
        self,
        *,
        run_id: str,
        source: str,
        business_date: date,
        period_end: date,
        status: str,
        rows_loaded: int,
        started_at: datetime,
        error: str | None = None,
    ) -> None:
        self.con.execute(
            """
            INSERT INTO ops.pipeline_runs (
                run_id, source, business_date, period_end, status,
                rows_loaded, started_at, finished_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                source,
                business_date,
                period_end,
                status,
                rows_loaded,
                started_at,
                datetime.now(UTC),
                error,
            ],
        )

    def succeeded_periods(self) -> set[tuple[str, date, date]]:
        """(source, business_date, period_end) of every task that ever loaded data."""
        rows = self.con.execute(
            """
            SELECT DISTINCT source, business_date, period_end
            FROM ops.pipeline_runs
            WHERE status = 'success'
            """
        ).fetchall()
        return set(rows)

"""dbt artifacts -> ops.dbt_results.

The fixtures are real dbt artifacts from this project cut down to a few results of each
kind (model, seed, passing test, warning test, source freshness).
"""

import shutil
from pathlib import Path

import duckdb
import pytest

from energy_pipeline.cli import main
from energy_pipeline.config import get_settings
from energy_pipeline.load.dbt_results import parse_artifact

FIXTURES = Path(__file__).parent / "fixtures" / "dbt"


def test_parse_run_results(load_fixture) -> None:
    results = {r.name: r for r in parse_artifact(load_fixture("dbt/run_results.head.json"))}

    warn = results["warn_chmi_values_outside_physical_range"]
    assert (warn.resource_type, warn.status, warn.failures) == ("test", "warn", 1)
    assert results["stg_chmi__stations"].resource_type == "model"
    generic = next(r for r in results.values() if r.name.startswith("dbt_expectations"))
    assert "." not in generic.name  # the hash suffix of generic tests is dropped


def test_parse_source_freshness(load_fixture) -> None:
    results = parse_artifact(load_fixture("dbt/sources.head.json"))

    assert {r.name for r in results} >= {"raw.ote_dam_results", "raw.cnb_fx_rates"}
    assert all(r.resource_type == "source" for r in results)
    assert all(r.message.startswith("last load") for r in results)


def test_record_dbt_results_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    monkeypatch.setenv("PIPELINE_ENV", "local")
    monkeypatch.setenv("PIPELINE_DUCKDB_PATH", str(database))
    get_settings.cache_clear()
    artifacts = [
        shutil.copy(FIXTURES / name, tmp_path)
        for name in ("run_results.head.json", "sources.head.json")
    ]

    assert main(["record-dbt-results", *map(str, artifacts)]) == 0
    assert main(["record-dbt-results", *map(str, artifacts)]) == 0
    get_settings.cache_clear()

    with duckdb.connect(str(database)) as con:
        rows = con.execute(
            "SELECT resource_type, count(*) FROM ops.dbt_results GROUP BY 1"
        ).fetchall()
    assert dict(rows)["test"] == 2
    assert dict(rows)["source"] == 3

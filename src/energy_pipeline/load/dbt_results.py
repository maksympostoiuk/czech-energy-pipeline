"""Turn dbt artifacts into rows of ops.dbt_results, so data quality is queryable.

run_results.json (dbt build / test) and sources.json (dbt source freshness) are written
to dbt/target/ after every invocation; the daily workflow records both.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DbtResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    invocation_id: str
    generated_at: datetime
    unique_id: str
    resource_type: str  # model, seed, test, source (freshness), ...
    name: str
    status: str  # success / pass / warn / error / fail / skipped / runtime error
    failures: int | None
    execution_time_s: float | None
    message: str | None


def _name(unique_id: str) -> str:
    # model.czech_energy.fct_dam_prices, test.czech_energy.<name>.<hash>,
    # source.czech_energy.raw.ote_dam_results
    parts = unique_id.split(".")
    if parts[0] == "test" and len(parts) > 3:
        return parts[2]
    if parts[0] == "source":
        return ".".join(parts[2:])
    return parts[-1]


def parse_artifact(payload: dict[str, Any]) -> list[DbtResult]:
    metadata = payload["metadata"]
    results = []
    for result in payload["results"]:
        unique_id = result["unique_id"]
        message = result.get("message")
        if "max_loaded_at_time_ago_in_s" in result:
            message = f"last load {result['max_loaded_at_time_ago_in_s'] / 3600:.1f} h ago"
        results.append(
            DbtResult(
                invocation_id=metadata["invocation_id"],
                generated_at=metadata["generated_at"],
                unique_id=unique_id,
                resource_type=unique_id.split(".")[0],
                name=_name(unique_id),
                status=result["status"],
                failures=result.get("failures"),
                execution_time_s=result.get("execution_time"),
                message=message,
            )
        )
    return results

"""Walk-forward evaluation, the way the forecast runs in production.

For every delivery day of the period the model is trained only on delivery days whose
prices were published by the forecast cutoff (delivery_date <= forecast_date), and predicts
the next day from features known at the cutoff. With refit_every=N the model is refitted
every N days and reused in between, which can only make it staler, never peek.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from joblib import Parallel, delayed

from energy_pipeline.forecast.features import KEYS, TARGET
from energy_pipeline.forecast.models import Model


@dataclass(frozen=True)
class Period:
    start: date
    end: date | None = None  # None: the last delivery day with published prices


# Settings are chosen on the validation period; the test period is scored once at the end.
VALIDATION = Period(date(2025, 9, 1), date(2026, 2, 28))
TEST = Period(date(2026, 3, 1))
OUTPUT_COLUMNS = [*KEYS, "local_hour", "is_peak", TARGET]


def delivery_days(frame: pd.DataFrame, period: Period) -> list[date]:
    """Days of the period whose every hour has a published price."""
    known = frame.groupby("delivery_date")[TARGET].apply(lambda s: s.notna().all())
    days = [day for day, complete in known.items() if complete and day >= period.start]
    return sorted(day for day in days if period.end is None or day <= period.end)


def training_rows(frame: pd.DataFrame, delivery_day: date) -> pd.DataFrame:
    """What a forecast for `delivery_day` may learn from: the days up to its forecast date."""
    forecast_date = delivery_day - timedelta(days=1)
    rows = frame[(frame["delivery_date"] <= forecast_date) & frame[TARGET].notna()]
    if rows.empty or rows["delivery_date"].max() >= delivery_day:
        raise ValueError(f"no valid training data for {delivery_day}")
    return rows


def _blocks(days: Sequence[date], refit_every: int) -> list[list[date]]:
    return [list(days[i : i + refit_every]) for i in range(0, len(days), refit_every)]


def _run_block(
    frame: pd.DataFrame, make_model: Callable[[], Model], block: list[date]
) -> pd.DataFrame:
    model = make_model()
    model.fit(training_rows(frame, block[0]))
    test = frame[frame["delivery_date"].isin(block)]
    return pd.concat([test[OUTPUT_COLUMNS], model.predict(test)], axis=1)


def walk_forward(
    frame: pd.DataFrame,
    make_model: Callable[[], Model],
    days: Sequence[date],
    *,
    variant: str,
    refit_every: int = 1,
    n_jobs: int = -1,
) -> pd.DataFrame:
    """Predictions for every hour of `days`, one row per hour with model and variant."""
    # Threads: LightGBM and NumPy release the GIL while fitting, and no data is copied.
    parts = Parallel(n_jobs=n_jobs, prefer="threads")(
        delayed(_run_block)(frame, make_model, block) for block in _blocks(days, refit_every)
    )
    result = pd.concat(parts, ignore_index=True)
    result.insert(0, "variant", variant)
    result.insert(0, "model", make_model().name)
    return result

"""OTE day-ahead market results. See docs/sources.md, section 1."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field

from energy_pipeline.extract import SourceFormatError
from energy_pipeline.http import get_json

URL = "https://www.ote-cr.cz/pw-data/chart-data/01"
PRAGUE = ZoneInfo("Europe/Prague")

VOLUME = "Volume (MWh)"
HOURLY_PRICE = "Price (EUR/MWh)"
QUARTER_PRICE = "15min price (EUR/MWh)"
QUARTER_HOUR_REF = "60min price reference (EUR/MWh)"
SERIES_BY_MTU: dict[int, set[str]] = {
    60: {VOLUME, HOURLY_PRICE},  # delivery days up to 2025-09-30
    15: {VOLUME, QUARTER_PRICE, QUARTER_HOUR_REF},  # from 2025-10-01
}

# Days where OTE officially published only the first periods. These are the only days
# allowed to be short; any other gap still fails the load. See docs/sources.md.
KNOWN_INCOMPLETE_DAYS: dict[date, str] = {
    date(2025, 7, 4): "official report marks hours 13-24 as 'ES'/'SN' instead of results",
}


class _Point(BaseModel):
    x: int
    y: float


class _Line(BaseModel):
    title: str
    point: list[_Point]


class _Data(BaseModel):
    lines: list[_Line] = Field(alias="dataLine")


class _Graph(BaseModel):
    title: str


class _Chart(BaseModel):
    data: _Data
    graph: _Graph


class DamPeriod(BaseModel):
    """One market time unit of one delivery day; `period` is 1-based in local time."""

    model_config = ConfigDict(frozen=True)

    delivery_date: date
    period: int
    mtu_minutes: Literal[15, 60]
    price_eur_mwh: float
    price_60min_ref_eur_mwh: float | None
    volume_mwh: float


def periods_in_day(day: date, mtu_minutes: int) -> int:
    """92/96/100 quarter-hours (23/24/25 hours) depending on DST in Europe/Prague."""
    start = datetime.combine(day, time(), PRAGUE).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), PRAGUE).astimezone(UTC)
    return int((end - start) / timedelta(minutes=mtu_minutes))


def parse_dam(payload: object, delivery_date: date) -> list[DamPeriod]:
    """Validate one day of results. An empty list means OTE has not published the day yet."""
    chart = _Chart.model_validate(payload)
    if not chart.data.lines:
        return []

    expected_title_date = delivery_date.strftime("%d.%m.%Y")
    if not chart.graph.title.endswith(expected_title_date):
        raise SourceFormatError(f"OTE answered {chart.graph.title!r} for {delivery_date}")

    series = {line.title: {p.x: p.y for p in line.point} for line in chart.data.lines}
    mtu = next((m for m, titles in SERIES_BY_MTU.items() if set(series) == titles), None)
    if mtu is None:
        raise SourceFormatError(f"Unknown OTE series for {delivery_date}: {sorted(series)}")

    expected_periods = list(range(1, periods_in_day(delivery_date, mtu) + 1))
    if delivery_date in KNOWN_INCOMPLETE_DAYS:
        # Accept a contiguous run of periods from the start of the day.
        expected_periods = expected_periods[: len(series[VOLUME])]
    for title, points in series.items():
        if sorted(points) != expected_periods:
            raise SourceFormatError(
                f"OTE {title!r} on {delivery_date}: {len(points)} periods, "
                f"expected {len(expected_periods)}"
            )

    price = series[QUARTER_PRICE] if mtu == 15 else series[HOURLY_PRICE]
    hour_ref = series.get(QUARTER_HOUR_REF, {})
    return [
        DamPeriod(
            delivery_date=delivery_date,
            period=period,
            mtu_minutes=mtu,
            price_eur_mwh=price[period],
            price_60min_ref_eur_mwh=hour_ref.get(period),
            volume_mwh=series[VOLUME][period],
        )
        for period in expected_periods
    ]


def fetch_dam(client: httpx.Client, delivery_date: date) -> list[DamPeriod]:
    payload = get_json(client, URL, {"language": "en", "report_date": delivery_date.isoformat()})
    return parse_dam(payload, delivery_date)

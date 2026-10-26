"""Open-Meteo weather forecasts as they were issued. See docs/sources.md, section 5.

The Previous Runs API returns, for every valid hour T, the value a model predicted N days
before T. For DWD ICON-EU that value comes from the run initialised at floor_6h(T - N days),
or from an older run when that one is missing or not out yet, never from a later one. Every
row therefore carries `issued_at`, the latest possible initialisation of its run, which the
feature models compare with the forecast cutoff.
"""

import math
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from energy_pipeline.extract import SourceFormatError
from energy_pipeline.http import get_json

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODEL = "dwd_icon_eu"
LEAD_DAYS = (1, 2)
RUN_INTERVAL = timedelta(hours=6)  # the main runs Previous Runs serves: 00, 06, 12, 18 UTC

# Open-Meteo variable -> raw column, with the unit the response must declare.
VARIABLES: dict[str, tuple[str, str]] = {
    "temperature_2m": ("temperature_2m_c", "°C"),
    "shortwave_radiation": ("shortwave_radiation_wm2", "W/m²"),  # mean of the preceding hour
    "wind_speed_10m": ("wind_speed_10m_ms", "m/s"),
    "wind_speed_100m": ("wind_speed_100m_ms", "m/s"),
    "cloud_cover": ("cloud_cover_pct", "%"),
}
# The free tier allows 600 calls a minute and 5,000 an hour; a request spanning more than
# two weeks or ten variables counts as several calls, so backfill requests are spaced out.
CALLS_PER_MINUTE = 300
MAX_GRID_DISTANCE_DEG = 0.1  # ICON-EU grid is 0.0625°: the nearest cell is always closer


@dataclass(frozen=True)
class Point:
    point_id: str
    name: str
    latitude: float
    longitude: float


# The 8 ČHMÚ stations (ids and coordinates from ČHMÚ metadata), so forecasts and observations
# describe the same places, plus German solar (south) and wind (north) regions: CZ prices are
# coupled with Germany through SDAC. Must match the dbt seed openmeteo_points.
POINTS = (
    Point("0-20000-0-11520", "Praha-Libuš", 50.007778, 14.446944),
    Point("0-20000-0-11723", "Brno-Tuřany", 49.153056, 16.688889),
    Point("0-20000-0-11782", "Ostrava-Mošnov", 49.691944, 18.112778),
    Point("0-20000-0-11450", "Plzeň-Mikulka", 49.764722, 13.378889),
    Point("0-20000-0-11546", "České Budějovice", 48.951944, 14.469722),
    Point("0-20000-0-11502", "Ústí nad Labem", 50.68333, 14.04111),
    Point("0-20000-0-11628", "Košetice", 49.573504, 15.08046),
    Point("0-20000-0-11774", "Holešov", 49.320556, 17.57),
    Point("de-muenchen", "München", 48.14, 11.58),
    Point("de-nuernberg", "Nürnberg", 49.45, 11.08),
    Point("de-stuttgart", "Stuttgart", 48.78, 9.18),
    Point("de-husum", "Husum", 54.48, 9.05),
    Point("de-emden", "Emden", 53.37, 7.21),
    Point("de-magdeburg", "Magdeburg", 52.13, 11.63),
)


class OpenMeteoError(RuntimeError):
    """Open-Meteo refused the request and said why (HTTP 400 with a JSON reason)."""


class _Location(BaseModel):
    latitude: float
    longitude: float
    utc_offset_seconds: int
    hourly_units: dict[str, str]
    hourly: dict[str, list[str] | list[float | None]]


class Forecast(BaseModel):
    """One point, one valid hour, one lead: the values predicted `lead_days` before."""

    model_config = ConfigDict(frozen=True)

    point_id: str
    model: str
    valid_at: AwareDatetime
    lead_days: int = Field(ge=1, le=7)
    issued_at: AwareDatetime  # latest possible initialisation of the run behind the values
    temperature_2m_c: float | None
    shortwave_radiation_wm2: float | None
    wind_speed_10m_ms: float | None
    wind_speed_100m_ms: float | None
    cloud_cover_pct: float | None


def issued_at(valid_at: datetime, lead_days: int) -> datetime:
    """floor_6h(valid_at - lead_days): no value of that lead comes from a later run."""
    earlier = valid_at - timedelta(days=lead_days)
    return earlier.replace(hour=earlier.hour - earlier.hour % 6, minute=0, second=0)


def hourly_variables() -> list[str]:
    return [f"{name}_previous_day{n}" for name in VARIABLES for n in LEAD_DAYS]


def call_weight(locations: int, days: int) -> float:
    """API calls one request counts as (open-meteo.com/en/pricing)."""
    return locations * max(1.0, len(hourly_variables()) / 10) * max(1.0, days / 14)


class _Pacer:
    """Spaces requests by their weight, so long backfills stay under the per-minute limit."""

    def __init__(self, calls_per_minute: float) -> None:
        self.seconds_per_call = 60 / calls_per_minute
        self.next_request = 0.0

    def wait(self, calls: float) -> None:
        now = time.monotonic()
        if self.next_request > now:
            time.sleep(self.next_request - now)
        self.next_request = max(now, self.next_request) + calls * self.seconds_per_call


_pacer = _Pacer(CALLS_PER_MINUTE)


def parse_forecasts(payload: object, points: tuple[Point, ...] = POINTS) -> list[Forecast]:
    # One coordinate gives an object, several give a list in request order.
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(points):
        raise SourceFormatError(f"Open-Meteo returned {len(items)} locations for {len(points)}")

    rows = []
    for point, item in zip(points, items, strict=True):
        location = _Location.model_validate(item)
        distance = max(
            abs(location.latitude - point.latitude), abs(location.longitude - point.longitude)
        )
        if distance > MAX_GRID_DISTANCE_DEG:
            raise SourceFormatError(
                f"Open-Meteo grid cell for {point.point_id} is {distance:.2f}° off"
            )
        if location.utc_offset_seconds != 0:
            raise SourceFormatError("Open-Meteo times are not UTC")

        times = location.hourly.get("time")
        if not times:
            raise SourceFormatError(f"Open-Meteo returned no hours for {point.point_id}")
        valid_ats = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in times]
        series = {}
        for variable in hourly_variables():
            _column, unit = VARIABLES[variable.rsplit("_previous_day", 1)[0]]
            if location.hourly_units.get(variable) != unit:
                raise SourceFormatError(
                    f"Open-Meteo {variable}: unit {location.hourly_units.get(variable)!r}, "
                    f"expected {unit!r}"
                )
            values = location.hourly.get(variable)
            if values is None or len(values) != len(times):
                raise SourceFormatError(f"Open-Meteo {variable}: missing or wrong length")
            series[variable] = values

        for i, valid_at in enumerate(valid_ats):
            for lead in LEAD_DAYS:
                values = {
                    column: series[f"{variable}_previous_day{lead}"][i]
                    for variable, (column, _unit) in VARIABLES.items()
                }
                if all(value is None for value in values.values()):
                    continue  # before the archive starts, or beyond the model's horizon
                rows.append(
                    Forecast(
                        point_id=point.point_id,
                        model=MODEL,
                        valid_at=valid_at,
                        lead_days=lead,
                        issued_at=issued_at(valid_at, lead),
                        **values,
                    )
                )
    return rows


def fetch_forecasts(
    client: httpx.Client, start: date, end: date, points: tuple[Point, ...] = POINTS
) -> list[Forecast]:
    """Day-1 and day-2 forecasts for every hour of start..end (UTC dates, inclusive)."""
    days = (end - start).days + 1
    params = {
        "latitude": ",".join(str(p.latitude) for p in points),
        "longitude": ",".join(str(p.longitude) for p in points),
        "models": MODEL,
        "timezone": "UTC",
        "wind_speed_unit": "ms",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(hourly_variables()),
    }
    _pacer.wait(math.ceil(call_weight(len(points), days)))
    try:
        payload = get_json(client, URL, params)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 400:
            raise OpenMeteoError(_reason(error.response)) from error
        raise
    return parse_forecasts(payload, points)


def _reason(response: httpx.Response) -> str:
    try:
        return str(response.json()["reason"])
    except (ValueError, KeyError, TypeError):
        return response.text[:200]

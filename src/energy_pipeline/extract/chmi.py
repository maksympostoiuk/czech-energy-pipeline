"""ČHMÚ 10-minute station observations. See docs/sources.md, section 3."""

from datetime import date, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from energy_pipeline.extract import SourceFormatError
from energy_pipeline.http import get_json

BASE_URL = "https://opendata.chmi.cz/meteorology/climate"
OBSERVATIONS_HEADER = "STATION,ELEMENT,DT,VAL,FLAG,QUALITY"
STATIONS_HEADER = "WSI,GH_ID,FULL_NAME,GEOGR1,GEOGR2,ELEVATION,BEGIN_DATE"

STATIONS = (
    "0-20000-0-11520",  # Praha, Libuš
    "0-20000-0-11723",  # Brno, Tuřany
    "0-20000-0-11782",  # Mošnov (Ostrava)
    "0-20000-0-11450",  # Plzeň, Mikulka
    "0-20000-0-11546",  # České Budějovice, Rožnov
    "0-20000-0-11502",  # Ústí nad Labem, Kočkov
    "0-20000-0-11628",  # Košetice (Vysočina)
    "0-20000-0-11774",  # Holešov (Zlín)
)
ELEMENTS = frozenset({"T", "F", "RGLB10", "SSV10M", "SRA10M", "H"})

# recent/ is rebuilt after quality control and has yesterday by ~12:15 UTC;
# now/ is fresher but unchecked, so it is only the fallback.
Folder = Literal["recent", "now"]
DAY_FILE_URLS: dict[Folder, str] = {
    "recent": BASE_URL + "/recent/data/10min/10m-{wsi}-{day:%Y%m%d}.json",
    "now": BASE_URL + "/now/data/10m-{wsi}-{day:%Y%m%d}.json",
}
STATIONS_URL = BASE_URL + "/now/metadata/meta1-{day:%Y%m%d}.json"


class _Table(BaseModel):
    header: str
    values: list[list[Any]]


class _Collection(BaseModel):
    data: _Table


class _Envelope(BaseModel):
    stream: str = Field(alias="datovyTokID")
    data: _Collection


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True)

    station_id: str
    element: str
    observed_at: AwareDatetime
    value: float | None  # None when ČHMÚ marks the value missing (quality 4)
    flag: str | None
    quality: int
    source_folder: Folder


class Station(BaseModel):
    model_config = ConfigDict(frozen=True)

    station_id: str
    gh_id: str
    full_name: str
    longitude: float
    latitude: float
    elevation_m: float
    begin_date: date


def _table(payload: object, stream: str, header: str) -> list[list[Any]]:
    envelope = _Envelope.model_validate(payload)
    if envelope.stream != stream or envelope.data.data.header != header:
        raise SourceFormatError(
            f"ČHMÚ {envelope.stream} with header {envelope.data.data.header!r}, "
            f"expected {stream} with {header!r}"
        )
    return envelope.data.data.values


def parse_observations(
    payload: object, station_id: str, source_folder: Folder
) -> list[Observation]:
    observations = []
    for station, element, observed_at, value, flag, quality in _table(
        payload, "Open.Data.10M", OBSERVATIONS_HEADER
    ):
        if station != station_id:
            raise SourceFormatError(f"ČHMÚ file for {station_id} contains {station}")
        if element not in ELEMENTS:
            continue
        observations.append(
            Observation(
                station_id=station,
                element=element,
                observed_at=datetime.fromisoformat(observed_at),
                value=None if value == "" else value,
                flag=flag or None,
                quality=int(quality),
                source_folder=source_folder,
            )
        )
    return observations


def parse_stations(payload: object) -> list[Station]:
    return [
        Station(
            station_id=wsi,
            gh_id=gh_id,
            full_name=name,
            longitude=lon,
            latitude=lat,
            elevation_m=elevation,
            begin_date=datetime.fromisoformat(begin).date(),
        )
        for wsi, gh_id, name, lon, lat, elevation, begin in _table(
            payload, "Open.Data.Metadata", STATIONS_HEADER
        )
    ]


def fetch_station_day(client: httpx.Client, station_id: str, day: date) -> list[Observation]:
    """Observations of one station for one UTC day; empty if neither folder has the file."""
    for folder, url in DAY_FILE_URLS.items():
        payload = get_json(client, url.format(wsi=station_id, day=day), allow_not_found=True)
        if payload is not None:
            return parse_observations(payload, station_id, folder)
    return []


def fetch_stations(client: httpx.Client, today: date) -> list[Station]:
    """Latest station list; today's metadata file may not be written yet just after midnight."""
    for day in (today, today - timedelta(days=1)):
        payload = get_json(client, STATIONS_URL.format(day=day), allow_not_found=True)
        if payload is not None:
            return parse_stations(payload)
    raise SourceFormatError(f"No ČHMÚ station metadata for {today} or the day before")

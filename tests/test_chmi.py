import copy
from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from energy_pipeline.extract import SourceFormatError, chmi
from energy_pipeline.http import make_client

LIBUS = "0-20000-0-11520"
DAY = date(2026, 10, 21)
RECENT_URL = chmi.DAY_FILE_URLS["recent"].format(wsi=LIBUS, day=DAY)
NOW_URL = chmi.DAY_FILE_URLS["now"].format(wsi=LIBUS, day=DAY)


@pytest.fixture
def day_file(load_fixture) -> dict:
    return load_fixture("chmi/10m-0-20000-0-11520-20261021.json")


def test_parse_observations_keeps_selected_elements(day_file: dict) -> None:
    observations = chmi.parse_observations(day_file, LIBUS, "now")

    assert {o.element for o in observations} == chmi.ELEMENTS
    assert len(observations) == len(chmi.ELEMENTS) * 144
    assert {o.source_folder for o in observations} == {"now"}


def test_parse_observations_types(day_file: dict) -> None:
    observations = chmi.parse_observations(day_file, LIBUS, "now")
    first_temperature = next(o for o in observations if o.element == "T")

    assert first_temperature.observed_at == datetime(2026, 10, 21, tzinfo=UTC)
    assert isinstance(first_temperature.value, float)
    assert first_temperature.flag is None


def test_parse_observations_missing_values_become_none(day_file: dict) -> None:
    observations = chmi.parse_observations(day_file, LIBUS, "now")
    missing = [o for o in observations if o.value is None]

    assert missing
    assert {o.quality for o in missing} == {4}


def test_parse_observations_rejects_other_header(day_file: dict) -> None:
    payload = copy.deepcopy(day_file)
    payload["data"]["data"]["header"] = "STATION,ELEMENT,DT,VALUE,FLAG,QUALITY"

    with pytest.raises(SourceFormatError, match="header"):
        chmi.parse_observations(payload, LIBUS, "now")


def test_parse_observations_rejects_foreign_station(day_file: dict) -> None:
    with pytest.raises(SourceFormatError, match="contains 0-20000-0-11520"):
        chmi.parse_observations(day_file, "0-20000-0-11723", "now")


def test_parse_stations(load_fixture) -> None:
    stations = chmi.parse_stations(load_fixture("chmi/meta1-stations.json"))
    libus = next(s for s in stations if s.station_id == LIBUS)

    assert set(chmi.STATIONS) <= {s.station_id for s in stations}
    assert libus.full_name == "Praha, Libuš"
    assert (round(libus.latitude, 3), round(libus.longitude, 3)) == (50.008, 14.447)


@respx.mock
def test_fetch_prefers_quality_checked_recent_folder(day_file: dict) -> None:
    recent = respx.get(RECENT_URL).mock(return_value=httpx.Response(200, json=day_file))
    now = respx.get(NOW_URL).mock(return_value=httpx.Response(200, json=day_file))

    with make_client() as client:
        observations = chmi.fetch_station_day(client, LIBUS, DAY)

    assert recent.called
    assert not now.called
    assert {o.source_folder for o in observations} == {"recent"}


@respx.mock
def test_fetch_falls_back_to_now_folder(day_file: dict) -> None:
    respx.get(RECENT_URL).mock(return_value=httpx.Response(404))
    respx.get(NOW_URL).mock(return_value=httpx.Response(200, json=day_file))

    with make_client() as client:
        observations = chmi.fetch_station_day(client, LIBUS, DAY)

    assert {o.source_folder for o in observations} == {"now"}


@respx.mock
def test_fetch_returns_empty_when_day_is_gone() -> None:
    respx.get(RECENT_URL).mock(return_value=httpx.Response(404))
    respx.get(NOW_URL).mock(return_value=httpx.Response(404))

    with make_client() as client:
        assert chmi.fetch_station_day(client, LIBUS, DAY) == []


@respx.mock
def test_fetch_stations_falls_back_to_previous_day(load_fixture) -> None:
    today = date(2026, 10, 23)
    respx.get(chmi.STATIONS_URL.format(day=today)).mock(return_value=httpx.Response(404))
    respx.get(chmi.STATIONS_URL.format(day=date(2026, 10, 22))).mock(
        return_value=httpx.Response(200, json=load_fixture("chmi/meta1-stations.json"))
    )

    with make_client() as client:
        assert chmi.fetch_stations(client, today)

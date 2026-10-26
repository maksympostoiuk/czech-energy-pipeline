from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from energy_pipeline.extract import SourceFormatError, openmeteo
from energy_pipeline.http import make_client

PRAHA, MUENCHEN = openmeteo.POINTS[0], openmeteo.POINTS[8]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("valid_at", "lead_days", "expected"),
    [
        (utc(2026, 10, 26, 5), 1, utc(2026, 10, 25, 0)),
        (utc(2026, 10, 26, 6), 1, utc(2026, 10, 25, 6)),
        (utc(2026, 10, 26, 23), 2, utc(2026, 10, 24, 18)),
        (utc(2026, 3, 29, 0), 1, utc(2026, 3, 28, 0)),
    ],
)
def test_issued_at_is_the_latest_possible_run(
    valid_at: datetime, lead_days: int, expected: datetime
) -> None:
    assert openmeteo.issued_at(valid_at, lead_days) == expected


def test_parse_two_locations(load_fixture) -> None:
    rows = openmeteo.parse_forecasts(
        load_fixture("openmeteo/previous_icon_eu_2026-10-24_2loc.json"), (PRAHA, MUENCHEN)
    )

    assert len(rows) == 2 * 48 * 2  # points, hours, leads
    assert {r.point_id for r in rows} == {PRAHA.point_id, MUENCHEN.point_id}
    first = rows[0]
    assert (first.point_id, first.valid_at, first.lead_days) == (
        PRAHA.point_id,
        utc(2026, 10, 24, 0),
        1,
    )
    assert first.model == "dwd_icon_eu"
    assert first.issued_at == utc(2026, 10, 23, 0)
    assert all(r.issued_at <= r.valid_at for r in rows)
    assert all(r.temperature_2m_c is not None for r in rows)


def test_parse_single_location_object(load_fixture) -> None:
    rows = openmeteo.parse_forecasts(
        load_fixture("openmeteo/previous_icon_eu_2026-06-15.json"), (PRAHA,)
    )

    assert len(rows) == 24 * 2
    noon = next(r for r in rows if r.valid_at == utc(2026, 6, 15, 12) and r.lead_days == 1)
    assert noon.shortwave_radiation_wm2 is not None
    assert noon.shortwave_radiation_wm2 > 0


def test_archive_holes_stay_null(load_fixture) -> None:
    rows = openmeteo.parse_forecasts(
        load_fixture("openmeteo/previous_icon_eu_2026-04-09_gap.json"), (PRAHA,)
    )
    day2 = [r for r in rows if r.lead_days == 2]

    assert len(day2) == 24  # kept: temperature and wind at 10 m are there
    assert all(r.shortwave_radiation_wm2 is None for r in day2)
    assert all(r.temperature_2m_c is not None for r in day2)


def test_hours_without_any_value_are_skipped(load_fixture) -> None:
    payload = load_fixture("openmeteo/previous_icon_eu_2026-06-15.json")
    for variable in openmeteo.hourly_variables():
        if variable.endswith("_previous_day2"):
            payload["hourly"][variable][0] = None

    rows = openmeteo.parse_forecasts(payload, (PRAHA,))

    assert len(rows) == 24 * 2 - 1
    assert not any(r.valid_at == utc(2026, 6, 15, 0) and r.lead_days == 2 for r in rows)


def test_wrong_number_of_locations_fails(load_fixture) -> None:
    payload = load_fixture("openmeteo/previous_icon_eu_2026-10-24_2loc.json")

    with pytest.raises(SourceFormatError, match="2 locations for 1"):
        openmeteo.parse_forecasts(payload, (PRAHA,))


def test_locations_in_another_order_fail(load_fixture) -> None:
    payload = load_fixture("openmeteo/previous_icon_eu_2026-10-24_2loc.json")

    with pytest.raises(SourceFormatError, match="grid cell"):
        openmeteo.parse_forecasts(payload, (MUENCHEN, PRAHA))


def test_unexpected_unit_fails(load_fixture) -> None:
    payload = load_fixture("openmeteo/previous_icon_eu_2026-06-15.json")
    payload["hourly_units"]["wind_speed_10m_previous_day1"] = "km/h"

    with pytest.raises(SourceFormatError, match="km/h"):
        openmeteo.parse_forecasts(payload, (PRAHA,))


def test_missing_variable_fails(load_fixture) -> None:
    payload = load_fixture("openmeteo/previous_icon_eu_2026-06-15.json")
    del payload["hourly"]["cloud_cover_previous_day2"]

    with pytest.raises(SourceFormatError, match="cloud_cover_previous_day2"):
        openmeteo.parse_forecasts(payload, (PRAHA,))


@respx.mock
def test_fetch_sends_the_documented_request(load_fixture) -> None:
    route = respx.get(openmeteo.URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("openmeteo/previous_icon_eu_2026-10-24_2loc.json")
        )
    )

    with make_client() as client:
        rows = openmeteo.fetch_forecasts(
            client, date(2026, 10, 24), date(2026, 10, 25), (PRAHA, MUENCHEN)
        )

    params = route.calls.last.request.url.params
    assert len(rows) == 192
    assert params["models"] == "dwd_icon_eu"
    assert params["timezone"] == "UTC"
    assert params["wind_speed_unit"] == "ms"
    assert params["latitude"] == f"{PRAHA.latitude},{MUENCHEN.latitude}"
    assert (params["start_date"], params["end_date"]) == ("2026-10-24", "2026-10-25")
    assert params["hourly"].split(",") == openmeteo.hourly_variables()


@respx.mock
def test_refused_request_raises_with_the_reason(load_fixture) -> None:
    respx.get(openmeteo.URL).mock(
        return_value=httpx.Response(400, json=load_fixture("openmeteo/error_invalid_model.json"))
    )

    with make_client() as client, pytest.raises(openmeteo.OpenMeteoError, match="no_such_model"):
        openmeteo.fetch_forecasts(client, date(2026, 6, 15), date(2026, 6, 15), (PRAHA,))


def test_call_weight_follows_the_pricing_rule() -> None:
    assert openmeteo.call_weight(locations=1, days=1) == 1
    assert openmeteo.call_weight(locations=14, days=4) == 14
    assert openmeteo.call_weight(locations=14, days=28) == 28


def test_pacer_spaces_requests_by_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 100.0}
    slept = []
    monkeypatch.setattr(openmeteo.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(openmeteo.time, "sleep", slept.append)
    pacer = openmeteo._Pacer(calls_per_minute=60)

    pacer.wait(10)  # first request: no wait, the next one is due 10 s later
    clock["now"] += 4
    pacer.wait(1)

    assert slept == [6.0]


def test_points_are_unique_and_include_every_chmi_station() -> None:
    from energy_pipeline.extract import chmi

    ids = [p.point_id for p in openmeteo.POINTS]
    assert len(ids) == len(set(ids))
    assert set(chmi.STATIONS) <= set(ids)

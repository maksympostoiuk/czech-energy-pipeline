import copy
from datetime import date

import httpx
import pytest
import respx

from energy_pipeline.extract import SourceFormatError, ote
from energy_pipeline.http import make_client


@pytest.mark.parametrize(
    ("day", "mtu", "expected"),
    [
        (date(2026, 10, 21), 15, 96),
        (date(2026, 3, 29), 15, 92),  # spring forward
        (date(2025, 10, 26), 15, 100),  # fall back
        (date(2024, 10, 27), 60, 25),
        (date(2025, 9, 30), 60, 24),
    ],
)
def test_periods_in_day_follows_prague_dst(day: date, mtu: int, expected: int) -> None:
    assert ote.periods_in_day(day, mtu) == expected


@pytest.mark.parametrize(
    ("fixture", "day", "mtu", "count"),
    [
        ("dam_2026-10-21_15min.json", date(2026, 10, 21), 15, 96),
        ("dam_2026-03-29_15min_dst_92.json", date(2026, 3, 29), 15, 92),
        ("dam_2025-10-26_15min_dst_100.json", date(2025, 10, 26), 15, 100),
        ("dam_2025-09-30_hourly.json", date(2025, 9, 30), 60, 24),
        ("dam_2024-10-27_hourly_dst_25h.json", date(2024, 10, 27), 60, 25),
    ],
)
def test_parse_dam_regimes(load_fixture, fixture: str, day: date, mtu: int, count: int) -> None:
    periods = ote.parse_dam(load_fixture(f"ote/{fixture}"), day)

    assert len(periods) == count
    assert [p.period for p in periods] == list(range(1, count + 1))
    assert {p.mtu_minutes for p in periods} == {mtu}
    assert {p.delivery_date for p in periods} == {day}


def test_parse_dam_15min_values(load_fixture) -> None:
    periods = ote.parse_dam(load_fixture("ote/dam_2026-10-21_15min.json"), date(2026, 10, 21))
    first = periods[0]

    assert first.price_eur_mwh == 165.34
    assert first.price_60min_ref_eur_mwh == 169.84
    assert first.volume_mwh == 879.275


def test_parse_dam_hourly_has_no_hour_reference(load_fixture) -> None:
    periods = ote.parse_dam(load_fixture("ote/dam_2025-09-30_hourly.json"), date(2025, 9, 30))

    assert all(p.price_60min_ref_eur_mwh is None for p in periods)


def test_parse_dam_not_published_is_empty(load_fixture) -> None:
    payload = load_fixture("ote/dam_2026-10-30_not_published.json")

    assert ote.parse_dam(payload, date(2026, 10, 30)) == []


def test_parse_dam_rejects_missing_period(load_fixture) -> None:
    payload = copy.deepcopy(load_fixture("ote/dam_2026-10-21_15min.json"))
    for line in payload["data"]["dataLine"]:
        line["point"].pop()

    with pytest.raises(SourceFormatError, match="95 periods, expected 96"):
        ote.parse_dam(payload, date(2026, 10, 21))


def test_parse_dam_rejects_unknown_series(load_fixture) -> None:
    payload = copy.deepcopy(load_fixture("ote/dam_2026-10-21_15min.json"))
    payload["data"]["dataLine"][1]["title"] = "15min cena (EUR/MWh)"

    with pytest.raises(SourceFormatError, match="Unknown OTE series"):
        ote.parse_dam(payload, date(2026, 10, 21))


def test_parse_dam_rejects_other_day(load_fixture) -> None:
    payload = load_fixture("ote/dam_2026-10-21_15min.json")

    with pytest.raises(SourceFormatError, match="for 2026-10-22"):
        ote.parse_dam(payload, date(2026, 10, 22))


@respx.mock
def test_fetch_dam_requests_english_titles(load_fixture) -> None:
    route = respx.get(ote.URL, params={"language": "en", "report_date": "2026-10-21"}).mock(
        return_value=httpx.Response(200, json=load_fixture("ote/dam_2026-10-21_15min.json"))
    )

    with make_client() as client:
        periods = ote.fetch_dam(client, date(2026, 10, 21))

    assert route.called
    assert len(periods) == 96

from datetime import date

import httpx
import pytest
import respx
from pydantic import ValidationError

from energy_pipeline.extract import cnb
from energy_pipeline.http import make_client


def test_parse_daily_keeps_all_currencies(load_fixture) -> None:
    rates = cnb.parse_daily(load_fixture("cnb/daily_2026-10-16_friday.json"))
    by_code = {rate.currency_code: rate for rate in rates}

    assert len(rates) == 30
    assert by_code["EUR"].rate == 24.325
    assert by_code["EUR"].valid_for == date(2026, 10, 16)
    assert by_code["HUF"].amount == 100


def test_parse_daily_on_saturday_returns_friday_fixing(load_fixture) -> None:
    rates = cnb.parse_daily(load_fixture("cnb/daily_2026-10-17_saturday.json"))

    assert {rate.valid_for for rate in rates} == {date(2026, 10, 16)}


@pytest.mark.parametrize("payload", [{"rates": []}, {"error": "x"}, {"rates": [{"rate": -1}]}])
def test_parse_daily_rejects_unexpected_shapes(payload: object) -> None:
    with pytest.raises(ValidationError):
        cnb.parse_daily(payload)


@respx.mock
def test_fetch_daily_sends_date_and_retries_server_errors(load_fixture) -> None:
    route = respx.get(cnb.DAILY_URL, params={"date": "2026-10-16", "lang": "EN"}).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=load_fixture("cnb/daily_2026-10-16_friday.json")),
        ]
    )

    with make_client() as client:
        rates = cnb.fetch_daily(client, date(2026, 10, 16))

    assert route.call_count == 2
    assert len(rates) == 30

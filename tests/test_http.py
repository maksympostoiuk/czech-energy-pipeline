import time

import httpx
import pytest
import respx

from energy_pipeline.http import get_json, get_text, make_client

URL = "https://example.test/data"


@respx.mock
def test_not_found_is_none_only_when_allowed() -> None:
    respx.get(URL).mock(return_value=httpx.Response(404))

    with make_client() as client:
        assert get_json(client, URL, allow_not_found=True) is None
        assert get_text(client, URL, allow_not_found=True) is None
        with pytest.raises(httpx.HTTPStatusError, match="404"):
            get_json(client, URL)


@respx.mock
def test_client_errors_are_not_retried() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(400))

    with make_client() as client, pytest.raises(httpx.HTTPStatusError):
        get_json(client, URL)

    assert route.call_count == 1


@respx.mock
def test_min_interval_spaces_requests_to_one_host() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, json={}))

    with make_client(min_interval=0.05) as client:
        started = time.perf_counter()
        for _ in range(3):
            get_json(client, URL)
        elapsed = time.perf_counter() - started

    assert elapsed >= 0.1

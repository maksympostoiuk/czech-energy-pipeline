"""Shared HTTP client: one place for timeouts, the User-Agent and retries."""

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from energy_pipeline import __version__

USER_AGENT = f"czech-energy-pipeline/{__version__} (+https://github.com/maksympostoiuk/czech-energy-pipeline)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def make_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in RETRYABLE_STATUS
    return isinstance(error, httpx.TransportError)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=20),
    reraise=True,
)
def _get(client: httpx.Client, url: str, params: dict[str, str] | None) -> httpx.Response:
    response = client.get(url, params=params)
    if response.status_code != 404:
        response.raise_for_status()
    return response


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, str] | None = None,
    *,
    allow_not_found: bool = False,
) -> Any | None:
    """GET a JSON document, retrying transient failures.

    With allow_not_found, a 404 returns None instead of raising: for file-based sources
    such as ČHMÚ a missing file is an expected answer, not an error.
    """
    response = _get(client, url, params)
    if response.status_code == 404:
        if allow_not_found:
            return None
        response.raise_for_status()
    return response.json()

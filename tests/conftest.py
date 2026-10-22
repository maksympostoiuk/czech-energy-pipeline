import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from tenacity import wait_none

from energy_pipeline import http

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Parsed JSON of a stored real response from tests/fixtures/."""

    def load(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text("utf-8"))

    return load


@pytest.fixture(autouse=True)
def no_retry_wait() -> Iterator[None]:
    """Retries stay on, but without the exponential back-off sleeps."""
    original = http._get.retry.wait
    http._get.retry.wait = wait_none()
    yield
    http._get.retry.wait = original

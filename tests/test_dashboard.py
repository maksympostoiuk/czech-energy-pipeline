"""Render every dashboard page headlessly and fail on any exception.

Runs against DASHBOARD_DUCKDB_PATH (CI: the empty warehouse from `dbt build --empty`,
which checks the pages handle "no data") or else the local data/warehouse.duckdb.
"""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).parent.parent
APP = ROOT / "dashboard" / "app.py"
DATABASE = os.environ.get("DASHBOARD_DUCKDB_PATH") or str(ROOT / "data" / "warehouse.duckdb")
PAGES = ["views/day_ahead.py", "views/history.py", "views/weather.py", "views/quality.py"]

pytestmark = pytest.mark.skipif(
    not Path(DATABASE).exists(), reason="no warehouse file to render the dashboard from"
)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("DASHBOARD_DUCKDB_PATH", DATABASE)
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    return AppTest.from_file(str(APP), default_timeout=60)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(app: AppTest, page: str) -> None:
    app.run()
    app.switch_page(page).run()

    assert not app.exception, [e.value for e in app.exception]
    assert app.title, "every page starts with a title"

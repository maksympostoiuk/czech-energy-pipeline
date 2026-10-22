from pathlib import Path

import pytest

from energy_pipeline.cli import main
from energy_pipeline.config import Settings


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the developer's real .env and environment out of the tests."""
    monkeypatch.chdir(tmp_path)
    for name in ("PIPELINE_ENV", "PIPELINE_DUCKDB_PATH", "MOTHERDUCK_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_local_env_uses_duckdb_file() -> None:
    settings = Settings()

    assert settings.env == "local"
    assert settings.database == str(Path("data/warehouse.duckdb"))


def test_prod_env_uses_motherduck(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_ENV", "prod")
    monkeypatch.setenv("MOTHERDUCK_TOKEN", "test-token")

    assert Settings().database == "md:czech_energy"


def test_prod_env_without_token_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_ENV", "prod")

    with pytest.raises(ValueError, match="MOTHERDUCK_TOKEN"):
        _ = Settings().database


def test_cli_config_prints_target(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config"]) == 0
    assert "env:      local" in capsys.readouterr().out

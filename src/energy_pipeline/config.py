"""Runtime settings, read from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PIPELINE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "prod"] = "local"
    duckdb_path: Path = Path("data/warehouse.duckdb")
    motherduck_database: str = "czech_energy"
    # Standard variable name that DuckDB's MotherDuck extension reads on its own.
    motherduck_token: SecretStr | None = Field(default=None, validation_alias="MOTHERDUCK_TOKEN")

    @property
    def database(self) -> str:
        """Connection target for duckdb.connect(): a local file or a MotherDuck database."""
        if self.env == "prod":
            if self.motherduck_token is None:
                raise ValueError("MOTHERDUCK_TOKEN must be set when PIPELINE_ENV=prod")
            return f"md:{self.motherduck_database}"
        return str(self.duckdb_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()

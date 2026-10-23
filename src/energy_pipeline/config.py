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
    motherduck_database: str = Field(default="czech_energy", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    # Standard variable name that DuckDB's MotherDuck extension reads on its own.
    motherduck_token: SecretStr | None = Field(default=None, validation_alias="MOTHERDUCK_TOKEN")
    # Only needed where the home directory path is not ASCII (a Cyrillic Windows user name):
    # the MotherDuck extension then fails to download its components into ~/.duckdb.
    duckdb_extension_directory: Path | None = None

    @property
    def database(self) -> str:
        """Connection target for duckdb.connect(): a local file or a MotherDuck database."""
        if self.env == "prod":
            if self.motherduck_token is None:
                raise ValueError("MOTHERDUCK_TOKEN must be set when PIPELINE_ENV=prod")
            return f"md:{self.motherduck_database}"
        return str(self.duckdb_path)

    @property
    def duckdb_config(self) -> dict[str, str]:
        """Options for duckdb.connect(config=...). The token is passed explicitly, so a token
        kept only in .env works too (DuckDB itself reads only the process environment)."""
        config = {}
        if self.duckdb_extension_directory is not None:
            config["extension_directory"] = self.duckdb_extension_directory.as_posix()
        if self.env == "prod" and self.motherduck_token is not None:
            config["motherduck_token"] = self.motherduck_token.get_secret_value()
        return config


@lru_cache
def get_settings() -> Settings:
    return Settings()

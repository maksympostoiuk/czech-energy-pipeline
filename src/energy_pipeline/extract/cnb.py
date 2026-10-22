"""ČNB exchange rate fixing. See docs/sources.md, section 2."""

from datetime import date

import httpx
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

from energy_pipeline.http import get_json

DAILY_URL = "https://api.cnb.cz/cnbapi/exrates/daily"
YEAR_URL = "https://api.cnb.cz/cnbapi/exrates/daily-year"


class FxRate(BaseModel):
    """One currency of one fixing, as published (rate is CZK per `amount` units)."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    valid_for: date = Field(alias="validFor")
    currency_code: str = Field(alias="currencyCode", min_length=3, max_length=3)
    amount: PositiveInt
    rate: PositiveFloat
    country: str | None = None  # not in the yearly backfill endpoint
    currency: str | None = None


class DailyRates(BaseModel):
    rates: list[FxRate] = Field(min_length=1)


def parse_daily(payload: object) -> list[FxRate]:
    return DailyRates.model_validate(payload).rates


def fetch_year(client: httpx.Client, year: int) -> list[FxRate]:
    """Every fixing of a calendar year (business days only), all currencies in one request."""
    return parse_daily(get_json(client, YEAR_URL, {"year": str(year)}))


def fetch_daily(client: httpx.Client, day: date) -> list[FxRate]:
    """Fixing valid on `day`: for weekends and holidays ČNB returns the last earlier one."""
    payload = get_json(client, DAILY_URL, {"date": day.isoformat(), "lang": "EN"})
    return parse_daily(payload)

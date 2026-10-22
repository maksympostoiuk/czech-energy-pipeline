"""Download real API responses into tests/fixtures/ so tests never touch the network.

Run once (or when a source changes format):  uv run python scripts/fetch_fixtures.py
Each case is picked to cover one edge case documented in docs/sources.md.
"""

import json
import urllib.request
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
USER_AGENT = "czech-energy-pipeline fixture fetcher"

OTE = "https://www.ote-cr.cz/pw-data/chart-data/01?language=en&report_date={day}"
CNB_DAILY = "https://api.cnb.cz/cnbapi/exrates/daily?date={day}&lang=EN"
CNB_MONTH = "https://api.cnb.cz/cnbapi/exrates/daily-currency-month?currency=EUR&yearMonth={month}"
CHMI = "https://opendata.chmi.cz/meteorology/climate"

# The `now/` folder only keeps the last few days: refresh these dates before re-running.
CHMI_NOW_DAY = "20261021"
CHMI_META_DAY = "20261022"

CASES: dict[str, str] = {
    # OTE day-ahead market: hourly MTU until 2025-09-30, 15-minute MTU from 2025-10-01.
    "ote/dam_2025-09-30_hourly.json": OTE.format(day="2025-09-30"),
    "ote/dam_2024-10-27_hourly_dst_25h.json": OTE.format(day="2024-10-27"),
    "ote/dam_2026-10-21_15min.json": OTE.format(day="2026-10-21"),
    "ote/dam_2025-10-26_15min_dst_100.json": OTE.format(day="2025-10-26"),
    "ote/dam_2026-03-29_15min_dst_92.json": OTE.format(day="2026-03-29"),
    "ote/dam_2026-10-30_not_published.json": OTE.format(day="2026-10-30"),
    # ČNB: weekends and holidays return the last valid fixing (validFor != requested date).
    "cnb/daily_2026-10-16_friday.json": CNB_DAILY.format(day="2026-10-16"),
    "cnb/daily_2026-10-17_saturday.json": CNB_DAILY.format(day="2026-10-17"),
    "cnb/daily_2026-09-28_holiday.json": CNB_DAILY.format(day="2026-09-28"),
    "cnb/eur_month_2026-09.json": CNB_MONTH.format(month="2026-09"),
    # ČHMÚ: daily 10-minute file from now/, station and quality-code metadata.
    "chmi/10m-0-20000-0-11520-20261021.json": (
        f"{CHMI}/now/data/10m-0-20000-0-11520-{CHMI_NOW_DAY}.json"
    ),
    "chmi/meta1-stations.json": f"{CHMI}/now/metadata/meta1-{CHMI_META_DAY}.json",
    "chmi/meta4-quality-codes.json": f"{CHMI}/now/metadata/meta4-{CHMI_META_DAY}.json",
}

# Historical CSV files are ~200 kB each; the first lines are enough to pin the format.
CSV_CASES: dict[str, str] = {
    "chmi/10m-0-20000-0-11520-T-202501.head.csv": (
        f"{CHMI}/historical_csv/data/10min/temperature/2025/10m-0-20000-0-11520-T-202501.csv"
    ),
}
CSV_HEAD_LINES = 50


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> None:
    for name, url in CASES.items():
        raw = fetch(url)
        json.loads(raw)  # fail early on HTML error pages; the bytes are stored untouched
        target = FIXTURES / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        print(f"{name}  <-  {url}")

    for name, url in CSV_CASES.items():
        lines = fetch(url).decode("utf-8").splitlines()[:CSV_HEAD_LINES]
        target = FIXTURES / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", "utf-8")
        print(f"{name}  <-  {url}")


if __name__ == "__main__":
    main()

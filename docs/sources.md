# Data sources

Findings from probing the three public sources on 2026-10-22. Every claim here is backed by a
stored real response in [`tests/fixtures/`](../tests/fixtures) and pinned by
[`tests/test_fixtures.py`](../tests/test_fixtures.py). To refresh the fixtures, run
`uv run python scripts/fetch_fixtures.py`.

| Source | What | Grain | Published | Auth |
|---|---|---|---|---|
| OTE | Day-ahead market price and volume, CZ bidding zone | 15 min (60 min before 2025-10-01) | D-1 around 13:00 CET | none |
| ČNB | Official CZK exchange rate fixing | business day | business days after 14:30 CET | none |
| ČHMÚ | Weather station observations | 10 min | hourly, finalised 00:02 UTC next day | none |

---

## 1. OTE: day-ahead market (DAM)

**Endpoint**

```
GET https://www.ote-cr.cz/pw-data/chart-data/01?language=en&report_date=YYYY-MM-DD
```

The address the OTE website shows (`/cs/kratkodobe-trhy/elektrina/denni-trh/@@chart-data?...`) answers
with a `301` redirect to this one, so we call the target directly. This is the JSON that powers the
chart on the OTE website. It is public but **not a documented API**, so the parser must fail loudly
when the shape changes. The fallback is the official XLSX daily reports on the same site.

**Response shape**

```json
{
  "axis":  {"x": {"legend": "Hour"}, "y": {...}, "y2": {...}},
  "data":  {"dataLine": [
      {"title": "Volume (MWh)",          "type": "2", "point": [{"x": "1", "y": 879.275}, ...]},
      {"title": "15min price (EUR/MWh)", "type": "1", "point": [{"x": "1", "y": 165.34}, ...]},
      {"title": "60min price reference (EUR/MWh)", "type": "1", "point": [...]}
  ]},
  "graph": {"title": "Day-Ahead Market Results - 21.10.2026"}
}
```

Series are identified **by title**: `type` is `"1"` for both price lines, so it cannot tell them apart.
With `language=cs` the titles are in Czech, so we always request `language=en`.

**Two regimes**

| Delivery day | Series | Points per day |
|---|---|---|
| ≤ 2025-09-30 | `Volume (MWh)`, `Price (EUR/MWh)` | 24, or 23/25 on DST days |
| ≥ 2025-10-01 | `Volume (MWh)`, `15min price (EUR/MWh)`, `60min price reference (EUR/MWh)` | 96, or 92/100 on DST days |

- On 2025-10-01 SDAC switched to a 15-minute market time unit (MTU).
- `60min price reference` is the hourly reference price repeated on each of its four quarters. We
  store it, but the 15-minute price is the primary price.
- Volume is MWh **per period**, so 15-minute volumes are about a quarter of the old hourly ones.
- History is available at least back to 2015 (2015-01-01 checked).

**Time handling.** `x` is the 1-based sequential index of the period within the delivery day in
`Europe/Prague`. Never derive the clock time as `(x-1) * 15 min` in local time, because that breaks on DST
days. Instead, convert local midnight of the delivery day to UTC and add `(x-1) * MTU` in UTC.

| Day | Periods | Local clock |
|---|---|---|
| last Sunday of March | 92 / 23 | 02:00–03:00 does not exist |
| last Sunday of October | 100 / 25 | 02:00–03:00 happens twice |

**Known incomplete day: 2025-07-04.** The JSON has only periods 1–12. The official XLSX report
(`/pubweb/attachments/01/2025/month07/day04/DM_04_07_2025_EN.xlsx`) shows the same thing: hours
13–24 contain `ES` (`SN` in the Czech version) instead of prices, so those hours have no market
results. Such days are listed in `ote.KNOWN_INCOMPLETE_DAYS`, where a contiguous run of periods from
the start of the day is accepted. Any other short day still fails the load. dbt must exclude the
same list from its period-count test.

**Report quirk on DST days before 2025-10-01.** In OTE's daily XLSX report, PEAK/OFFPEAK
prices use wall-clock 08:00–20:00. PEAK/OFFPEAK volumes on 23/25-hour days, however, are summed
over hour positions 9–20. This project uses wall-clock time for both, so the reconciliation test
skips those two volume checks on hourly-market DST days.

**Not yet published.** A future date returns HTTP `200` with `"dataLine": []`. That is "not published
yet", not an error and not zero prices.

**Plausibility.** SDAC harmonised clearing price limits are −500 / +4000 EUR/MWh. The maximum can be
raised automatically, so keep the limits in config rather than hard-coding them in tests.

---

## 2. ČNB: exchange rate fixing

**Endpoints** (OpenAPI spec: `https://api.cnb.cz/cnbapi/api-docs`)

```
GET https://api.cnb.cz/cnbapi/exrates/daily?date=YYYY-MM-DD&lang=EN
GET https://api.cnb.cz/cnbapi/exrates/daily-year?year=YYYY                                    # backfill
GET https://api.cnb.cz/cnbapi/exrates/daily-currency-month?currency=EUR&yearMonth=YYYY-MM   # one currency
```

**Response shape**

```json
{"rates": [{"validFor": "2026-10-16", "order": 200, "country": "EMU", "currency": "euro",
            "amount": 1, "currencyCode": "EUR", "rate": 24.325}, ...]}
```

- `daily` returns **the last valid fixing** for the requested date. For Saturday 2026-10-17 it
  returns `validFor: 2026-10-16`, and for the St. Wenceslas holiday on 2026-09-28 it returns
  `2026-09-25`. The business key is therefore `(validFor, currencyCode)`, never the requested date.
- 30 currencies. `rate` is CZK per `amount` units: `amount` is 100 for HUF, JPY, INR and others, and
  1000 for IDR. Always divide `rate` by `amount`.
- `daily-year` returns every business day of a year for all currencies (31 in 2024) in one
  request, which is what the backfill uses. Its rows have no `country` / `currency` names, so
  those raw columns are nullable and staging takes names from the latest daily load.
- `daily-currency-month` returns business days only (21 rows for 2026-09).
- Calendar days without a fixing are filled in dbt by carrying the last fixing forward
  (`int_fx_rates_daily_filled`).

---

## 3. ČHMÚ: weather observations

Official description: `https://opendata.chmi.cz/meteorology/climate/Klimatologicka_data_popis.pdf`.

**Folders**

| Folder | Content | Files | Kept / updated |
|---|---|---|---|
| `now/data/` | today and the last couple of days | `10m-{WSI}-{YYYYMMDD}.json` | rewritten hourly; the previous day is final at 00:02 UTC; deleted after ~1–3 days |
| `recent/data/10min/` | current month, daily files | `10m-{WSI}-{YYYYMMDD}.json` | rebuilt 2× a day after quality control |
| `recent/data/10min/{MM}/` | current year, monthly files | `10m-{WSI}-{YYYYMM}.json` (~5.8 MB) | built on the 3rd of each month; the 2 previous months are rebuilt |
| `historical/data/10min/{YYYY}/` | 2018 … last year, monthly | `10m-{WSI}-{YYYYMM}.json` | once a year, by end of February |
| `historical_csv/data/10min/{group}/{YYYY}/` | same, **one file per element** | `10m-{WSI}-{ELEMENT}-{YYYYMM}.csv` (~200 kB) | once a year |
| `now/metadata/` | station list (meta1), elements per station (meta2), flags (meta3), quality codes (meta4) | `meta{1-4}-{YYYYMMDD}.json` | daily |

`now/` keeps only the last few days, so **a missed daily run loses data from `now/`**. It can be
recovered from `recent/` for the current year.

**JSON shape** (`now/` and `recent/`)

```json
{"zaznamID": "...", "datovyTokID": "Open.Data.10M", "datumVytvoreni": "2026-10-22T00:01:02Z",
 "data": {"type": "DataCollection", "data": {
   "header": "STATION,ELEMENT,DT,VAL,FLAG,QUALITY",
   "values": [["0-20000-0-11520", "T", "2026-10-21T00:00:00Z", 8.1, "", 5.0], ...]}}}
```

- Long format: one row per station × element × 10 minutes, so 144 rows per element per day.
- `DT` is UTC.
- **Missing values are empty strings** (`""`) with `QUALITY = 4`, not `null`. An example is `SSV10M`
  (sunshine) at night.
- The historical CSV differs: the column is `VALUE` instead of `VAL`, `DT` has no seconds
  (`2025-01-01T00:00Z`) and there is a trailing empty column.

**Quality codes (meta4)**

| QUALITY | Meaning | Our handling |
|---|---|---|
| 0 | good | use |
| 1 | suspect | use, count in DQ report |
| 2 | poor, do not use yet | exclude |
| 3 | estimated | use |
| 4 | missing | exclude (value is empty) |
| 5 | unknown, not checked yet | use; most data in `now/` has this code |

Values are quality-checked after publication. The daily run therefore re-loads a rolling window of
recent days from `recent/`, and the idempotent upsert replaces earlier unchecked values.

**Physically impossible values exist.** For example, Holešov on 2026-09-22 at 09:50 UTC reported
global radiation of 1673.7 W/m² with quality 0 ("good"), which is above the solar constant.
Physical bounds per measure live in the dbt seed `chmi_measure_ranges`. Values outside them are
excluded from aggregates and listed by a warn-level test.

**Elements we keep** (10-minute; the hourly `1h` files do not contain T or F)

| Code | Meaning | Unit | Why |
|---|---|---|---|
| `T` | air temperature at 2 m | °C | heating and cooling demand |
| `F` | wind speed at 10 m | m/s | wind generation |
| `RGLB10` | global solar radiation | W/m² | solar generation (better than sunshine duration) |
| `SSV10M` | sunshine duration in the 10 min | s (0–600) | solar, readable for the dashboard |
| `SRA10M` | precipitation | mm | context |
| `H` | relative humidity | % | context |

**Stations.** We use 8 WMO stations spread over the regions, each measuring all six elements
including global radiation. Praha-Ruzyně was skipped because it has no `RGLB10`.

| WSI | Name | GH_ID | Lat | Lon | Elevation (m) |
|---|---|---|---|---|---|
| `0-20000-0-11520` | Praha, Libuš | P1PLIB01 | 50.008 | 14.447 | 302 |
| `0-20000-0-11723` | Brno, Tuřany | B2BTUR01 | 49.153 | 16.689 | 241 |
| `0-20000-0-11782` | Mošnov (Ostrava) | O1MOSN01 | 49.692 | 18.113 | 253 |
| `0-20000-0-11450` | Plzeň, Mikulka | L1PLMI01 | 49.765 | 13.379 | 360 |
| `0-20000-0-11546` | České Budějovice, Rožnov | C2CBUD01 | 48.952 | 14.470 | 395 |
| `0-20000-0-11502` | Ústí nad Labem, Kočkov | U1ULKO01 | 50.683 | 14.041 | 375 |
| `0-20000-0-11628` | Košetice (Vysočina) | P3KOSE01 | 49.574 | 15.080 | 532 |
| `0-20000-0-11774` | Holešov (Zlín) | B1HOLE01 | 49.321 | 17.570 | 222 |

---

## 4. Ingestion strategy

**Daily run** (14:00 UTC):

| Source | Fetch | Notes |
|---|---|---|
| OTE | D+1, plus re-fetch D | an empty `dataLine` for D+1 means "retry later", not a failure |
| ČNB | today | upsert on `(validFor, currencyCode)` |
| ČHMÚ | D-1…D-7, each from `recent/` (quality-checked, has D-1 by ~12:15 UTC) or else `now/` | picks up quality-control corrections; `source_folder` records which one |

**Backfill from 2024-01-01** (`pipeline backfill --from 2024-01-01 [--to …] [--source …] [--resume]`).
Requests are throttled to one every 0.25 s per host. Every chunk is logged in `ops.pipeline_runs`
with `business_date..period_end`, and `--resume` skips chunks that already succeeded.

| Source | Requests | Volume |
|---|---|---|
| OTE | ~1,000 (one task per day) | < 10 MB |
| ČNB | 3 (one task per calendar year, `daily-year`) | ~1.5 MB |
| ČHMÚ 2024–2025 (`historical_csv`) | 8 stations × 6 elements × 24 months = 1,152 files | ~230 MB |
| ČHMÚ 2026 (`recent/{MM}/` monthly JSON, all elements) | 8 × 9 files | ~420 MB, filtered to our elements |
| ČHMÚ current month (daily files) | 8 per day | ~1.5 MB per day |

ČHMÚ is backfilled one task per calendar month. For each station the archives are tried from the
most to the least compact: historical CSV (a 404 on the first element means the year is not
archived yet), then the monthly JSON in `recent/{MM}/`, then the daily files.

The archives are not always complete. For example, the CSV for 2025-12-31 has 6,864 of 6,912
rows, because a few 10-minute values are absent rather than marked missing. dbt tests should
therefore check completeness against a threshold, not demand exactly 144 values per day.

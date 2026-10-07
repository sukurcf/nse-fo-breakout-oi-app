# NSE F&O Breakout + OI Monitor

A private, single-user NSE stock breakout monitor. It evaluates completed five-minute candles against the opening 15-minute and 30-minute ranges, records collision levels, and displays confirmed price events with optional raw futures/options open-interest context.

**Implementation status:** This repository contains a local dashboard, deterministic replay engine, SQLite audit history, and normalized CSV imports. It does **not** connect to live market data or TradeFinder, send external notifications, verify a data licence, or place orders. Live integrations remain blocked until the owner resolves the gates in [`docs/open-decisions.md`](docs/open-decisions.md).

**Deployment target:** Run the service and dashboard on a reliable, always-on Windows computer. Open the dashboard in Chrome or Edge on that computer or on an Android phone connected to the same trusted local network. The app is not to be exposed to the public internet. Market-data providers and Telegram still require internet access.

**Trading boundary:** Alerts and analysis only. The user makes every trading decision. V1 must not place, modify, or cancel orders.

## Implemented workflow

- Add a symbol only after confirming its NSE F&O eligibility and provider cash-instrument ID.
- Record manually supplied bullish and bearish Beacon lists; preserve dual-direction conflicts.
- Import normalized, timezone-aware, five-minute candle CSVs from a source you are permitted to use.
- Replay the exact documented opening-range and collision rules. Incomplete ranges, stale/incomplete bars, gaps, invalid OHLC, and mapping mismatches suppress affected events.
- Import raw contract-level OI snapshots. OI remains uncalibrated context; the app does not calculate a score or claim that OI proves direction.
- Review signals, watchlist health, corrections, and audit events in the local dashboard; idempotent in-app notification records are stored with each signal, and signal history and the full local database can be exported.

Imported prices are **historical replay results**, not live alerts. The application does not scrape TradeFinder or connect to a broker/data provider. Telegram, browser push, LAN access, exchange-calendar lookup, automatic backups, and order execution are disabled.

## Run locally

Python 3.12–3.14 is supported; Python 3.14.8 is pinned in [`.python-version`](.python-version).

### Windows PowerShell

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m nse_fo_breakout
```

### macOS / Linux

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m nse_fo_breakout
```

Open <http://127.0.0.1:8000> on the host computer. The server binds to loopback, and the app rejects non-loopback clients. It is not accessible from an Android phone or another LAN device. Do not override the bind address or expose the port.

The local database is created at `data/nse-breakout.sqlite3`, which is ignored by Git. `NSE_BREAKOUT_DB_PATH` can point to another local path. `NSE_BREAKOUT_PORT` can select a port from 1024–65535.

Use **Download local backup** from the dashboard for an owner-initiated, consistent SQLite snapshot of all local inputs, corrections, signals, and audit records. Store that file only in a private local location; data-retention, encryption, and backup schedules remain owner decisions. To restore, stop the service, preserve the existing database, place the backup at the configured database path, and restart the service. Do not upload provider data or backups to cloud storage without separate licence and security approval.

## Normalized imports

1. Select the session date in IST.
2. Add a symbol mapping and explicitly confirm NSE F&O eligibility. The provider instrument ID must match the candle CSV.
3. Optionally record manual Beacon observations. Unmapped symbols are not monitored; both directions are retained when they conflict.
4. Import a normalized candle CSV with these required columns:

   `exchange,symbol,provider_instrument_id,interval_start,interval_end,open,high,low,close,status,provider_time,source`

   `volume` is optional. Use `exchange=NSE`, timezone-aware ISO 8601 timestamps, regular-session five-minute intervals, and rows in chronological order per symbol. `status` must identify each bar (`complete`, `corrected`, `incomplete`, `stale`, or `invalid`). Source labels must not contain URLs, credentials, or other secrets. Invalid and incomplete inputs are retained where safely parseable, but cannot produce signals.
5. Optionally import raw OI snapshots using the CSV columns shown in the dashboard. Futures and options remain separate contracts; no score, percentage feature, nearby-strike aggregate, or calibrated interpretation is produced.

Use only data and access methods permitted by the relevant provider and licence. The app does not transform raw provider exports automatically; imports must already follow the documented normalized contract.

## Tests

Run the deterministic rule, importer, storage, and local API tests with:

```sh
.venv/bin/python -m unittest discover -s tests -q
```

For Windows, use `.\.venv\Scripts\python.exe -m unittest discover -s tests -q`.

## Trading-day schedule

All session times use **India Standard Time (IST)** and the NSE trading calendar.

| Activity | Requirement |
|---|---|
| Startup | Initialize and check data sources early enough to report health before market monitoring starts. The exact lead time is not yet chosen. |
| Beacon monitoring | Check bullish and bearish lists every five minutes during the configurable 9:15–15:30 session window. Ignore pre-open. |
| Price monitoring | Process completed five-minute candles during 9:15–15:30. Do not signal from unfinished candles. |
| 15-minute range | Build from 9:15–9:30; freeze when all three candles are complete. |
| 30-minute range | Build from 9:15–9:45; freeze when all six candles are complete. |
| Daily reset | Prepare the next trading day before its session; preserve prior-day history. |

The first and last Beacon poll boundaries are configurable and still need a final decision. Missing opening candles invalidate the affected range for that symbol and day; do not substitute a partial range.

## Read the specification

1. [`docs/product-requirements.md`](docs/product-requirements.md) — users, scope, requirements, and non-goals.
2. [`docs/strategy-rules.md`](docs/strategy-rules.md) — exact candle, range, collision, signal, and duplicate-suppression rules.
3. [`docs/architecture-and-deployment.md`](docs/architecture-and-deployment.md) — components, local deployment, network boundary, and suggested implementation baseline.
4. [`docs/integrations-and-data.md`](docs/integrations-and-data.md) — approved data access, provider adapters, OI handling, and data-quality rules.
5. [`docs/data-model-and-audit.md`](docs/data-model-and-audit.md) — proposed persistence model and audit requirements.
6. [`docs/testing-and-release.md`](docs/testing-and-release.md) — examples, acceptance tests, and release gates.
7. [`docs/operations.md`](docs/operations.md) — daily operation, recovery, and support.
8. [`docs/open-decisions.md`](docs/open-decisions.md) — unanswered decisions and live-use blockers.
9. [`docs/implementation-roadmap.md`](docs/implementation-roadmap.md) — suggested implementation order.
10. [`AGENTS.md`](AGENTS.md) — guardrails for future implementation work.

## Important before implementation

Live integrations must not be built until the owner confirms that the selected data provider supplies the required real-time stock, futures, options, and OI data under an appropriate licence, and confirms that the chosen TradeFinder access method is permitted. If permitted automation is unavailable, use a manual symbol import. Never bypass authentication, CAPTCHA, anti-bot protection, or provider limits.

The OI spike thresholds, scoring weights, nearby-strike count, exact local-network security setup, retention period, and several release targets are intentionally marked **TBD**. They must be selected through provider verification, testing, and owner approval—not guessed or presented as proven.

Do not store API keys, passwords, access tokens, account identifiers, or other secrets in this repository. Do not commit live user data.

# Implementation roadmap

Build in small, testable phases. Live integrations depend on approved access and data entitlements. The strategy engine and fixtures can be developed before provider selection.

## Current implementation status

- Phase 0 is still blocked for live use: provider permissions, data licences, calendar, stale-data thresholds, LAN security, notification credentials, and retention/backup decisions are unresolved.
- The repository now contains a deterministic replay engine, append-only local SQLite inputs/derived history, normalized manual imports, a loopback-only dashboard, and automated tests for the locked strategy rules.
- No live adapter, TradeFinder automation, Telegram delivery, calibrated OI score, exchange-calendar integration, LAN access, or order execution is enabled. This is not a trusted live-alert release candidate.

## Phase 0 — Confirm gates

- Select an approved TradeFinder collection method or commit to manual import.
- Compare data providers and verify the required real-time fields, entitlements, licences, limits, and costs.
- Confirm Windows host requirements, private-LAN security, credential storage, and database setup.
- Decide release targets, data retention, backup, OI thresholds, strike selection, and observation period.

**Exit:** Relevant items in `open-decisions.md` have owner-approved answers or are explicitly blocked from live use.

## Phase 1 — Pure strategy engine

- Implement candle validation, range construction, direct breakouts, collision state, collision breakouts, event deduplication/re-arming, and correction replay.
- Use pure functions or explicit state transitions that can be replayed deterministically.
- Build all `STR-*` unit tests from `testing-and-release.md`.
- Use synthetic candle fixtures; do not connect to live accounts.

**Exit:** All locked core-rule examples pass and produce explainable state transitions.

## Phase 2 — Persistence and audit

- Implement the logical entities and migrations in `data-model-and-audit.md`.
- Persist event state and notification outbox atomically.
- Restore today's state after restart; prove idempotency and correction handling.
- Add local export/backup primitives without uploading data.

**Exit:** Restart/replay tests neither lose state nor duplicate an alert event.

## Phase 3 — Local dashboard and health

- Build the responsive signal dashboard and separate watchlist/system-health views.
- Add signal history, filters, export, pause/resume, and user-facing data-quality status.
- Enforce the local/private network boundary and authentication design before enabling LAN access.

**Exit:** Windows and Android browsers can use the dashboard in the approved network configuration.

## Phase 4 — Approved data adapters

- Add provider adapters behind the internal candle/instrument/OI contracts.
- Begin with a sandbox or recorded fixture mode where available.
- Add permitted TradeFinder API/export integration only after access approval; otherwise implement manual import.
- Add reconnect, rate limiting, mapping diagnostics, freshness checks, and historical reconstruction.

**Exit:** Entitlement, licence, field mapping, timestamps, limits, and outage behavior are verified against the chosen provider.

## Phase 5 — OI feature and scoring

- Store contract-level futures/options observations.
- Compute reliable 5/10/15-minute changes and time-of-day baseline features.
- Implement separate Call/Put views and clearly labeled nearby-strike aggregates.
- Calibrate thresholds/weights from approved historical data; version the configuration.
- Keep “not calibrated,” “stale,” and “unavailable” states explicit. Never gate the price signal.

**Exit:** Owner approves scoring and labels after replay; until then, raw OI context is shown without a confirmation claim.

## Phase 6 — Notifications and local operations

- Add persistent Telegram outbox, grouping, retries, and delivery status.
- Add in-app updates; add browser push only after HTTPS and pairing are working.
- Package/start the service on Windows with pre-session health checks, logging, restart recovery, and backup instructions.

**Exit:** Failure, restart, daily reset, and duplicate-delivery tests pass on the target Windows host.

## Phase 7 — Historical replay and observation

- Replay the approved market sample, compare signals to expected outputs, and record discrepancies.
- Run an owner-approved observation period with live data but no automated trades.
- Verify that every delivered alert has reproducible source candles and OI timestamps.
- Review performance separately from rule correctness; use explicit fees/slippage/entry/exit assumptions for any trading-results evaluation.

**Exit:** Owner signs off after data access, backtest/replay, observation, health, and release-gate review.

## Always out of scope without separate approval

Automated order placement, portfolio actions, public hosting, multi-user access, unapproved scraping, and new data-sharing destinations.

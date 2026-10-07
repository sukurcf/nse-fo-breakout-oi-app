# Architecture and deployment

## 1. Deployment decision

The intended first deployment is **local on a reliable, always-on Windows computer**, not a cloud server. The app's backend, database, and dashboard run on that computer. The owner can use the dashboard in Chrome/Edge on the host or, when LAN access is enabled, from an Android phone on the same trusted private network.

The app must not be exposed to the public internet. Do not configure router port forwarding or a public tunnel by default. Market data, approved Beacon access, and Telegram notifications still require outgoing internet access.

The host must stay powered, awake, connected to the network, and synchronized to a reliable clock during market monitoring. If it is offline, the dashboard must show an outage; the app cannot promise alerts while the host is off.

## 2. Logical components

```text
TradeFinder approved API/export or manual import
                       |
                       v
              Beacon intake adapter
                       |
                       v
          Daily symbol registry / mapping
                       |
          +------------+-------------+
          |                          |
          v                          v
   Approved price feed        Futures/options feed
          |                          |
          v                          v
   Candle normalizer          OI snapshot normalizer
          |                          |
          +------------+-------------+
                       v
       Opening-range and collision engine
                       |
                       v
         Confirmed signal / OI context
               |              |
               v              v
        Local database     Alert outbox
               |              |
               v              +----> Telegram
       Local web dashboard   +----> In-app updates
               |
               +----> Windows browser / Android on trusted LAN
```

## 3. Service responsibilities

1. **Beacon adapter:** Poll an approved endpoint/export every five minutes or accept a manual import. Record both directions, provider time, receipt time, and source status.
2. **Instrument registry:** Resolve symbols to provider instrument identifiers, exchange, lot/tick metadata, and F&O eligibility. Do not guess mappings.
3. **Price adapter:** Receive completed 5-minute candles where possible; otherwise build candles from authorized live updates. Normalize candle boundaries and preserve source timestamps.
4. **Derivatives adapter:** Collect futures and options observations by contract. Keep provider-specific request and response logic outside the strategy engine.
5. **Strategy engine:** Apply only the rules in `strategy-rules.md`. It should be deterministic, stateless over a supplied event sequence, and separately persist its evolving state.
6. **OI feature/scoring module:** Compute contract changes and configurable multi-factor context. The price signal remains valid without an OI score.
7. **Persistence/audit:** Save source inputs, state transitions, signals, alert attempts, and corrections needed for recovery and replay.
8. **Notification outbox:** Deliver grouped Telegram and dashboard notifications with retries and idempotency.
9. **Web dashboard:** Responsive signal view, watchlist/status view, and searchable history.
10. **Health monitor:** Report provider state, data freshness, missing candles, mapping errors, database health, and notification failures.

## 4. Suggested implementation baseline

These are implementation recommendations, not product facts from the answers:

- Python service using a supported, pinned Python version.
- FastAPI or an equivalent small HTTP framework for local API and dashboard endpoints.
- A responsive browser UI; keep the UI usable on Android without a native phone app.
- Server-sent events or a modest polling interval for in-app dashboard updates. Native browser push requires a secure browser origin and is not a V1 dependency.
- SQLAlchemy (or an equivalent typed persistence layer) and schema migrations. PostgreSQL is preferred for the production local installation; SQLite is acceptable for early tests and deterministic fixtures.
- `asyncio` or a maintained scheduler/client library for provider reconnects, market windows, and Beacon polling.
- Provider adapters and a strategy interface with fake/replay implementations for tests.

Choose the exact package versions after checking provider SDK support, Windows packaging, security maintenance, and the selected provider. Do not put broker-specific code in the strategy engine.

## 5. Local network boundary

- Run the backend on the Windows host. The dashboard may be restricted to loopback until LAN access is explicitly configured.
- For Android access, bind only to the trusted private LAN interface and restrict the Windows firewall to the private network. Require a local authentication/pairing mechanism; being on Wi-Fi alone is not authentication.
- Do not use a public IP, internet-facing reverse proxy, router port forwarding, or a public tunnel.
- Use HTTPS for LAN access if native browser notifications or credential-bearing browser requests require a secure origin. Exact certificate and pairing setup is an open decision.
- If secure LAN setup is not ready, use the dashboard on the host and Telegram for phone alerts; do not weaken authentication to make phone access easier.
- Avoid logging full API URLs if they include secrets, authorization headers, tokens, account IDs, or personal data.

## 6. Startup and market-day lifecycle

1. Start early enough to load configuration, initialize the local database, verify the exchange calendar, check provider entitlements/connectivity, and report status before 09:15 IST. Exact startup lead time is TBD.
2. Ignore pre-open data.
3. Operate on NSE trading days, 09:15–15:30 IST. Beacon check boundaries are configurable; cadence is five minutes during the configured session window.
4. Build and freeze the 15-minute range at 09:30 and the 30-minute range at 09:45 only when their full required candles are valid.
5. Process the final completed 15:25–15:30 candle if supplied by the selected feed.
6. Stop routine signal notifications outside the configured session. Keep the app idle or stop it according to the selected Windows startup policy.
7. Before the next trading session, create new daily state while preserving history.

Use the exchange calendar rather than assuming every weekday is a normal session. Treat special sessions explicitly. All job times must use `Asia/Kolkata`, not the host's local timezone.

## 7. Database and recovery

- PostgreSQL is the preferred local production database; SQLite is acceptable for early development/replay.
- Use migrations; do not mutate an existing database schema ad hoc.
- Persist the watchlist, source candles, ranges, active and replaced collisions, signals, OI observations, audit events, and notification outbox/delivery states.
- Use transaction boundaries so the signal, state transition, and outbox event cannot be left inconsistent.
- On restart, restore the current trading day's state and reconcile recent source events before processing new ones.
- Use deterministic event IDs and idempotent inserts/delivery so reconnects do not duplicate signals.
- Backup frequency, location, retention, and restore procedure require owner approval.

## 8. Provider failure behavior

- Retry transient network errors with bounded exponential backoff and jitter. Show provider state while retrying.
- Do not poll faster than provider limits.
- Do not silently reuse an old Beacon list as current.
- Do not create signals from stale or missing price data.
- If an approved backup provider is configured, preserve the source identity and reconcile symbol/candle differences before using it.
- If no safe backup exists, stop affected calculations, preserve already confirmed signals, and make the outage visible.
- Notification failures do not erase signals; retry from the persistent outbox and show delivery status.

## 9. Secrets and private data

- Keep tokens and API credentials in a secure local store such as Windows Credential Manager or an owner-controlled secret configuration outside the repository.
- Do not put real credentials in `.env.example`, source code, test fixtures, screenshots, logs, exports, or commits.
- Use fake tokens and synthetic data in tests.
- Keep raw provider data local unless the licence requires another approved handling method.
- Do not upload the database, audit log, or OI snapshots to a cloud service without a separate decision and licence review.

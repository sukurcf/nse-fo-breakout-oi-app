# Open decisions and implementation gates

The completed questionnaire defines the core strategy and product boundary, but these choices remain unresolved. Keep them visible; do not silently fill them with guesses.

## 1. Must be answered before live integrations

| ID | Decision | Current state | Why it matters |
|---|---|---|---|
| G-01 | Exact TradeFinder Beacon page, subscription, URL, and approved automated access method | TBD; manual import is the fallback | Prevents unauthorized or unreliable collection |
| G-02 | Exact market-data provider, product/account permissions, and real-time entitlements | Candidate providers only; compare Dhan/Zerodha-compatible options | Determines available fields, latency, quotas, and integration design |
| G-03 | Data licence for live display, local storage, derived signals, historical replay, and export | Must be verified | Determines what the app may process and retain |
| G-04 | Provider costs, API limits, subscription caps, historical depth, and outage behavior | Provider-dependent | Required for cost and capacity planning |
| G-05 | Approved backup provider and reconciliation policy | Preferred if available; none selected | Prevents unsafe source switching |
| G-06 | OI baseline method, normalized features, thresholds, score weights, and score labels | TBD by backtesting | Avoids invented or misleading “spike/confirmation” claims |
| G-07 | Nearby-strike count, liquidity cutoff, expiry selection, and rollover details | Configurable; exact rules TBD | Required for reproducible contract selection |
| G-08 | Price/OI stale-data thresholds and measurable alert-latency target | Configurable; numeric values TBD after provider testing | Required to decide when data is too old and whether latency is acceptable |
| G-09 | Exact Beacon first/last poll boundaries | Cadence 5 minutes in 09:15–15:30; boundaries configurable | Avoids ambiguity at session open/close |
| G-10 | Windows version, host hardware, startup mechanism, and required uptime | Reliable, always-on Windows computer intended | Determines packaging and recovery design |
| G-11 | Android-to-host access: authentication/pairing, HTTPS certificate, and LAN firewall setup | Same trusted LAN intended; implementation TBD | Prevents exposing private data on an untrusted network |
| G-12 | Local PostgreSQL installation vs initial SQLite deployment | PostgreSQL preferred for production; SQLite acceptable for early tests | Affects local installation and migration path |
| G-13 | Credential storage mechanism and token renewal flow | Owner-controlled secure local setup; exact provider flow TBD | Required before live credentials are configured |
| G-14 | Candle correction policy for sending a follow-up/correction alert | Recompute and audit required; notification behavior TBD | Prevents silent changes to already delivered signals |
| G-15 | Retention period for candles, OI snapshots, signals, and audit events | TBD | Affects storage, privacy, and ability to explain old alerts |
| G-16 | Backup schedule, local destination, encryption, and restore test | Automatic backup preferred; exact setup TBD | Required for recoverability |
| G-17 | Historical test dates/symbols, expected outputs, and data source | Multiple market conditions required; exact set TBD | Required to measure strategy correctness |
| G-18 | Observation-mode duration and objective release criteria | Required before reliance; duration/thresholds TBD | Prevents premature live reliance |
| G-19 | Quantitative availability/correctness target | High reliability and rule correctness required; numeric target TBD | Required to define release acceptance |
| G-20 | Monthly budget and any approved data/subscription charges | Private owner decision | Prevents unexpected recurring costs |

## 2. Required for trading-results analysis, not for price-signal logic

The owner wants both signal accuracy and trading results evaluated. Before any profitability or strategy-performance claim, define:

- Entry timing and entry price assumptions.
- Stop, exit, target, and time-based exit.
- Position sizing and maximum risk.
- Brokerage, exchange fees, taxes, slippage, and fills.
- Treatment of gaps, partial fills, circuits, and unavailable liquidity.
- Metrics such as expectancy and drawdown, not only win rate.

Until then, the system may test whether it implements the signal rules, but it must not claim that those signals are profitable.

## 3. Safe defaults while decisions are open

- No live Beacon automation until permission is verified; use manual import if needed.
- No live market feed until entitlement and licence are verified; use synthetic/approved historical fixtures for development.
- No hard-coded OI spike threshold. Show raw values and “OI score not calibrated” until an owner-approved configuration exists.
- Missing OI never blocks a valid price signal. Stale/missing price data does suppress affected signals.
- No public access, router port forwarding, cloud database, multi-user access, or order execution.
- No partial opening ranges, unfinished candles, stale-price signals, silent data substitution, or duplicate alert behavior.
- Keep original observations and corrections. Do not silently delete audit evidence.


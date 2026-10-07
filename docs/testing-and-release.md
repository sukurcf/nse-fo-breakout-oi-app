# Testing and release plan

## 1. Test layers

1. **Rule unit tests:** Deterministic in-memory candles and state; no network and no real credentials.
2. **Persistence tests:** State restoration, transaction behavior, migrations, idempotent signal/outbox writes, and corrections.
3. **Adapter contract tests:** Recorded/synthetic provider fixtures with sanitized data. Verify provider-to-normalized field mapping and error behavior.
4. **Historical replay:** Multiple trending, sideways, volatile, expiry, and gap sessions, subject to licensed data availability.
5. **Operational tests:** Windows startup/restart, local dashboard access, provider reconnect, stale data, Telegram failure, backup/restore, and daily reset.
6. **Observation mode:** Live data and alerts are observed without automated trading. Owner reviews signal explanations against an approved chart/data source.

The current local replay build has deterministic strategy, import, persistence/recovery, and HTTP boundary tests. Run them with `python -m unittest discover -s tests -q` from the repository root. Tests use synthetic fixtures only; they do not validate provider permissions, market data, or strategy profitability.

## 2. Required strategy test cases

| ID | Input / condition | Expected result |
|---|---|---|
| STR-01 | Three valid candles 09:15–09:30 | Correct 15M high/low; freeze at 09:30 |
| STR-02 | Six valid candles 09:15–09:45 | Correct 30M high/low; freeze at 09:45 |
| STR-03 | A required 15M opening candle is missing | 15M range incomplete; no 15M signals that day |
| STR-04 | A required 30M opening candle is missing | 30M range incomplete; no 30M signals that day |
| STR-05 | Close strictly above a completed range high | One bullish direct signal |
| STR-06 | Close strictly below a completed range low | One bearish direct signal |
| STR-07 | Close exactly at the range level | No direct signal |
| STR-08 | Wick crosses range but close does not | No direct signal |
| STR-09 | Wick above range high; close strictly inside range | High-side collision only; no final signal on this candle |
| STR-10 | Wick below range low; close strictly inside range | Low-side collision only; no final signal on this candle |
| STR-11 | Collision candle closes exactly on an original range edge | No collision |
| STR-12 | Candle crosses both range edges and closes inside | Record both collision sides; no breakout from that candle |
| STR-13 | Later completed candle closes strictly beyond active collision high/low | Correct collision breakout signal |
| STR-14 | Candle after collision only wicks beyond collision level | No collision breakout signal |
| STR-15 | New valid collision on the same timeframe/side | Latest level active; prior level retained in history |
| STR-16 | New collision on one side | Opposite-side active collision unchanged |
| STR-17 | One candle confirms 15M and 30M in same direction | Two signal records; one grouped notification if configured |
| STR-18 | One candle confirms direct and collision levels | Distinct confirmed event records; notification may group |
| STR-19 | Consecutive closes remain beyond same unchanged level | One alert, not a repeated alert per candle |
| STR-20 | Price closes back on the non-breakout side, then breaks again | A new valid event may alert |
| STR-21 | Opposite-direction close break later in the day | Opposite event is allowed |
| STR-22 | OI unavailable/stale but price rule confirms | Send price signal with OI status; do not invent confirmation |
| STR-23 | OI prior value is zero or very small | Avoid misleading percentage; use valid absolute/liquidity context |
| STR-24 | Same provider event is replayed after restart | No duplicate signal or notification |
| STR-25 | Candle correction changes range/collision/signal state | Recompute downstream state; preserve prior version and audit |
| STR-26 | Symbol first enters after range completion | Reconstruct from valid historical candles or suppress affected timeframe |
| STR-27 | Symbol appears in both Beacon directions | Preserve both observations and flag; no silent direction choice |
| STR-28 | Stale/out-of-order/missing price data | Suppress affected signals and show the reason |
| STR-29 | Provider outage and reconnect | Retry safely, reconcile before trusting state, no duplicate event |
| STR-30 | Exchange holiday/special session | Follow configured calendar/session rules; do not assume a normal day |

## 3. Integration and notification acceptance

- Provider adapter maps cash, futures, options, expiries, strikes, and OI fields to the internal contracts without losing source timestamps.
- Unsupported symbols/contract fields fail visibly; they are not replaced with guessed data.
- Provider limits are respected; retries do not create a request storm.
- A failed Beacon poll reports unavailable and does not present the prior list as current.
- A failed Telegram delivery leaves the signal persisted, reports retry state, and does not duplicate a delivered message.
- Grouped notification content still links to each individual signal event.
- Android and Windows users can read the dashboard in supported browsers on the approved local network.
- Secrets and live user data do not appear in logs, fixtures, screenshots, or the repository.

## 4. Replay and performance evaluation

- Use NSE five-minute history and futures/options OI history only where licensed and available.
- Compare outputs with a selected provider-approved or TradingView chart, allowing for documented feed differences.
- Save expected-output examples from the owner's known cases before using a backtest as release evidence.
- Measure signal correctness, missed/duplicate alerts, data freshness, and alert latency.
- Thresholds and score weights must be selected by testing; do not choose them to make a sample backtest look profitable.
- Trading-results analysis is separate from signal correctness. Before any profitability claim, define entry, exit, stop, target, size, fees, slippage, expectancy, and drawdown.
- Do not report a win-rate or return target as a promise.

## 5. Release gates

The replay implementation is not a live release candidate. Provider outage/reconnect behavior, licensed historical replay, Telegram delivery, Windows packaging/startup, LAN access, backup/restore, and an owner-approved observation period remain unimplemented or unverified.

A release candidate must not be used as a trusted live-alert tool until the owner has approved:

1. TradeFinder's exact page/subscription and permitted access method, or manual-import mode.
2. The price/derivatives provider, required entitlements, data licence, API limits, and fees.
3. The OI baseline/score configuration or an explicitly uncalibrated raw-context mode.
4. Measurable stale-data and latency limits.
5. Local Windows/LAN security and credential storage.
6. Historical replay dataset, expected outputs, and observation-mode duration.
7. Retention, backup, and restore policy.
8. Successful strategy, recovery, alert, and outage tests.

V1 remains alerts-only. No release gate can authorize order placement.

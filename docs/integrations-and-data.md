# Integrations and data contracts

## 1. Integration principles

- Use only documented and permitted interfaces.
- Confirm provider account permissions, data licence, rate limits, terms, and charges before live use.
- Isolate each provider behind an adapter so the signal engine consumes a stable internal data contract.
- Keep raw provider timestamps and instrument identifiers for traceability.
- Never treat missing or stale values as current. Surface a clear status instead.
- The current provider choices are candidates, not decisions. The answers mention Dhan/Zerodha-compatible sources and prefer one provider if it covers the required data reliably and legally.

## 2. TradeFinder Breakout Beacon

### Required adapter output

For every poll, normalize:

- Poll/request ID.
- Source name and method (approved API, approved export, or manual import).
- Provider timestamp, local receipt timestamp, and session date.
- Bullish symbol list and bearish symbol list.
- Raw-to-normalized symbol mapping result.
- Request status and any error/latency details safe to log.

### Permission and fallback

- Exact TradeFinder page/subscription and allowed access method remain unconfirmed.
- Do not automate collection until TradeFinder's terms or written approval permit it.
- Do not bypass login, CAPTCHA, bot detection, anti-automation measures, or rate limits.
- If no permitted automated method exists, accept a manual list/import and record when it was supplied.
- If a poll fails, retry with bounded backoff and show “Beacon unavailable.” Do not represent the prior list as a fresh result.

### Symbol handling

- Normalize to an exchange-qualified symbol and provider instrument ID through an explicit mapping.
- Retain repeated observations, direction changes, and simultaneous bullish/bearish appearances.
- A mapping failure is visible and prevents price processing for that symbol until corrected.
- Support a few hundred monitored symbols as a capacity target; final maximum is TBD and must be tested against provider limits.

## 3. Price and instrument data

The selected source must be qualified to provide, under the approved account and licence:

- NSE cash-stock live prices or completed 5-minute OHLC candles.
- Historical same-day 5-minute candles for a symbol added after the opening range formed.
- Futures instrument metadata and prices for derivatives context.
- Futures OI, OI change, volume, and timestamps.
- Options contract list and contract-level OI, OI change, volume, option price, expiry, strike, and timestamps for relevant strikes/expiries.
- NSE holiday/special-session calendar or a trustworthy source of that calendar.

Provider qualification must also verify API quotas, reconnect behavior, subscription caps, data latency, historical depth, correction policy, and fees. Prefer a single provider only if it meets the full data and licensing requirements; otherwise use separate approved adapters.

### Candle normalization contract

Each normalized candle must contain:

| Field | Meaning |
|---|---|
| `exchange` | NSE segment/source market |
| `symbol` / `instrument_id` | Stable normalized symbol and provider ID |
| `interval_start` | Timezone-aware interval start |
| `interval_end` | Timezone-aware exclusive end |
| `open`, `high`, `low`, `close` | Provider values at instrument precision |
| `volume` | Volume, if supplied |
| `provider_time` | Provider timestamp/version time |
| `received_at` | Local receipt time |
| `source` | Provider adapter identity |
| `status` | Incomplete, complete, corrected, stale, or invalid |

Use five-minute start-inclusive/end-exclusive intervals, with session times interpreted in `Asia/Kolkata`. The strategy consumes only validated complete candles.

## 4. Futures and options OI

### Contract identity

OI is contract-level. A normalized option observation must identify at least:

- Underlying stock and provider instrument ID.
- Contract type: Call, Put, or Future.
- Expiry date.
- Strike for options; no strike for a future.
- Exchange/segment.
- OI and provider-reported OI change, if available.
- Volume and LTP/price.
- Provider timestamp and local receipt timestamp.
- Quote freshness/liquidity status.

Do not combine contracts invisibly. Always retain contract-level values and label any aggregate by its strike/expiry selection.

### Selection rules

- Futures: current/near expiry and next relevant expiry when rollover requires, using listed instruments and liquidity-aware rules.
- Options: use expiries actually listed for each stock; do not assume weekly expiries exist for every symbol.
- Start with nearby strikes around ATM. Prefer futures price for ATM context; keep the cash stock price as the breakout reference.
- Track Calls and Puts separately.
- Exact number of nearby strikes, expiry selection/rollover policy, and liquidity cutoffs are TBD.

### OI feature windows and interpretation

- Compute changes over 5, 10, and 15 minutes from reliable snapshots.
- “Previous OI” means the previous reliable observation, preferably the preceding five-minute observation.
- Compare against an intraday time-of-day baseline when enough valid history is available.
- Include option volume expansion, option price expansion, underlying movement, strike distance, and futures OI/price relationship.
- If prior OI is zero or too small for meaningful percentage change, use absolute change with volume/liquidity context; do not show a misleading percentage.
- If a contract is stale or illiquid, exclude it from scoring and label the reason.
- Thresholds, baseline method, score weights, and exact “material update” rule are TBD. Do not hard-code an unvalidated threshold.
- If thresholds are not calibrated, show raw contract values and “OI score not calibrated.” A valid price signal still goes out.
- Do not describe OI alone as buying, selling, or bullish/bearish proof.
- IV and Greeks are outside V1.

## 5. Data freshness and outage policy

Freshness thresholds are configurable and require provider testing. Before approval:

- Every observation must show its provider timestamp.
- Missing/stale price data suppresses affected signals.
- Missing/stale OI marks context unavailable/stale but does not suppress a valid price signal.
- Missing historical candles suppress the affected timeframe.
- Out-of-order or corrected data triggers reconciliation before downstream state is trusted.
- Provider outages appear in the health view; use a backup only if the owner approved its licence and data equivalence.

## 6. Notifications

- Telegram is an outbound integration for one owner. Store its bot credential and destination securely outside source control.
- The dashboard must also show persisted in-app alerts.
- Group simultaneous notifications while preserving one record per confirmed signal.
- Retry delivery and maintain an idempotent outbox.
- Browser native push requires a secure origin. It is optional until local HTTPS and device pairing are specified.
- If Telegram fails, keep the signal and show the delivery failure; never mark it delivered without provider confirmation.

## 7. Candidate provider comparison checklist

Compare candidate providers using a recorded scorecard; do not select by name alone.

| Criterion | Evidence to collect |
|---|---|
| Real-time cash candles/quotes | Documentation, entitlement, latency, candle finalization/correction behavior |
| Futures and options coverage | Instrument list, contract fields, expiry/strike coverage |
| OI and volume | Field definitions, refresh frequency, historical access |
| Historical data | Intraday history depth and permitted use for replay |
| Limits and reliability | WebSocket/REST quotas, concurrent subscriptions, reconnect and outage behavior |
| Licensing | Private display, local storage, replay, derived signals, exports |
| Cost | Setup, subscription, per-user or per-segment charges |
| Windows integration | Supported auth flow/SDK and secure local token renewal |


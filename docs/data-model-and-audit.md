# Data model and audit

This is a logical model. Exact SQL types and indexes should be selected during implementation. Store every state needed to restart and reproduce a signal.

## 1. Time and identity

- Store timezone-aware timestamps; normalize machine processing to UTC and convert explicitly to `Asia/Kolkata` for session rules and display.
- Key daily strategy state by the NSE exchange trading date, normalized symbol, and strategy timeframe.
- Preserve provider instrument IDs and source names alongside normalized symbols.
- Keep a strategy/configuration version on calculated states and signals so later rule changes do not rewrite history ambiguously.

## 2. Proposed entities

### `trading_session`

One row per exchange trading date.

- Exchange, trading date, session type (normal/special), open/close times, calendar source, calendar version, session status.
- Keeps prior dates available for replay; a daily reset creates a new row rather than deleting old state.

### `instrument`

Provider-independent normalized instrument registry.

- Exchange/segment, symbol, instrument type (cash/future/option), provider ID, underlying symbol, expiry, strike, Call/Put, tick size, lot size, active status, mapping source, last-verified timestamp.

### `beacon_observation`

One row per intake result/symbol/direction.

- Poll ID, source/method, trading date, symbol/raw symbol, direction, provider timestamp, receipt timestamp, mapping status, result status, error code/details safe for logs.
- Store both directions if one symbol appears in both.

### `monitored_symbol`

Daily watchlist membership.

- Trading date, symbol, first-seen timestamp, last-seen timestamp, manual/Beacon source, current eligibility, same-day exclusion state/reason.
- Unique key: `(trading_date, symbol)`.

### `market_candle`

Normalized five-minute price candle.

- Instrument, interval start/end, OHLC, volume if supplied, provider/source, provider timestamp, received timestamp, completeness/freshness/correction status, source version.
- Unique key should identify the instrument, five-minute interval, and provider version while retaining corrected versions for audit.

### `opening_range`

Frozen opening range per stock, trading date, and timeframe.

- Trading date, symbol, timeframe (15m/30m), range high/low, contributing candle IDs, complete/incomplete status, calculated timestamp, invalidation reason, strategy version.
- Unique active state for `(trading_date, symbol, timeframe)`; preserve prior versions after correction.

### `collision_level`

Collision state and history.

- Trading date, symbol, timeframe, side (high/low), level, forming candle ID, original range ID, created timestamp, active/superseded/corrected status, replaced level ID, strategy version.
- Keep replaced levels. At most one active level per trading date/symbol/timeframe/side.

### `oi_observation`

One provider snapshot per contract and observation time.

- Underlying, contract instrument ID, contract type, expiry, strike, Call/Put where relevant, OI, provider OI change, computed change/percentage, volume, LTP, bid/ask/IV/Greeks only if later approved, provider timestamp, received timestamp, source, freshness/liquidity status.
- Preserve raw values; store computed features with their calculation window and configuration version.

### `signal_event`

One row per confirmed strategy event.

- Deterministic event ID, trading date, symbol, timeframe, signal type, direction, source candle ID, close, threshold/range/collision level, opening range high/low, confirmed timestamp, state (active/corrected/superseded), strategy version, OI status/score version, grouped notification ID if applicable.
- A later 15m and 30m confirmation remain separate events even if notification is grouped.

### `notification_outbox`

Durable notification work item.

- Notification/group ID, channel, related signal event IDs, payload version, created time, attempt count, next retry, status, last error, provider message ID, delivered timestamp.
- Unique idempotency key prevents a restart from sending the same event twice.

### `audit_event`

Append-only state and operational record.

- Event ID, timestamp, trading date, symbol if applicable, event type, previous state reference, new state reference, reason, source IDs, candle/OI references, configuration version, correlation/request ID.
- Do not write credentials or unrestricted provider payloads into log text.

### `provider_health`

Current and historical integration health.

- Provider/adapter, state, last success, last failure, last source timestamp, last receipt timestamp, latency, retry count, quota/error status, safe diagnostic message.

## 3. Invariants and transaction boundaries

- A signal must reference its completed source candle and the exact range/collision level that it crossed.
- An incomplete opening range cannot produce a signal for that timeframe.
- A collision signal must reference a collision level created by an earlier candle.
- OI status and price signal status are separate; missing OI cannot erase or block a valid price signal.
- Persist the strategy state transition, signal event, audit entry, and notification outbox item atomically where practical.
- Store provider corrections as new versions, not destructive edits.
- Use idempotent writes and delivery. A process restart must not duplicate a watchlist row or signal event.
- Keep all time windows explicit: 5/10/15-minute feature windows must identify the exact source observations.

## 4. Retention, export, and backup

The exact retention period and backup schedule/location are not yet approved. Make them configurable and document the active value. Until an owner-approved policy exists:

- Do not silently delete signal or audit records.
- Do not upload raw data to cloud storage.
- Support an owner-initiated local export and a documented local restore procedure.
- Warn before local storage grows beyond a configured limit; do not silently drop the oldest data required to explain signals.


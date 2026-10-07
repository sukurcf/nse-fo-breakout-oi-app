# Strategy rules

This document defines the signal engine independently of any data vendor. Identical validated input candles and the same strategy configuration must produce identical state transitions and signals.

## 1. Terms and time conventions

- **Instrument:** The NSE cash-market stock used for price breakout decisions.
- **Candle:** One five-minute OHLC bar.
- **Candle interval:** Start-inclusive, end-exclusive; e.g. `09:15–09:20`, `09:20–09:25`.
- **Close:** The final close of a completed provider candle, or a locally built candle after its interval has ended and its close is finalized.
- **Range:** The fixed high/low of the opening period for one stock and trading date.
- **Collision:** A completed candle's wick crosses a range edge and its close returns strictly inside the original range.
- **Collision level:** The extreme of the original range and the rejecting candle; a later completed candle must close beyond it.
- **Trading date:** The exchange-local date for an NSE trading session, not the computer's current date if it differs.
- **Clock:** Use timezone-aware timestamps. Interpret session times in `Asia/Kolkata`; render all user-facing times in IST.

Only regular-session candles are used. Ignore pre-open. The exchange calendar determines valid sessions, including holidays and special sessions; special-session behavior must be verified with the selected calendar/data source.

## 2. Symbol eligibility

1. On each permitted Beacon poll, record bullish and bearish observations.
2. Add each valid NSE F&O stock to that day's watchlist on its first observation.
3. Keep it in the watchlist until the trading day ends even if Beacon no longer lists it.
4. Preserve later Beacon direction changes and dual-direction observations for audit; they do not themselves create trading signals.
5. Support manual adds and same-day exclusions.
6. If a symbol is first observed after a range has completed, obtain that day's required historical 5-minute candles and reconstruct the ranges. If required candles are unavailable or incomplete, suppress signals for the affected timeframe.
7. A symbol with an unknown provider mapping, halt, circuit state, or unavailable price feed cannot generate a reliable signal. Show the reason in system health.

## 3. Candle validation

Before using a candle:

- Confirm that it belongs to the intended symbol, exchange, trading date, and five-minute interval.
- Confirm that its interval is complete and its OHLC values are valid.
- Reject or quarantine duplicates and out-of-order data until reconciliation is complete.
- Track provider time, receipt time, and correction/version status.
- Treat stale data according to a configured threshold. Until that threshold is approved, do not describe stale data as live or create new signals from it.
- Never substitute a missing value or an unfinished close with a guessed value.

If the provider revises a candle, preserve both the original and revised input, recalculate affected downstream state in timestamp order, and audit any changed signal. Do not silently erase a previously delivered alert. Whether a correction should send a Telegram update is an open decision.

## 4. Opening ranges

### 4.1 First 15-minute range

Use the three complete candles beginning at 09:15, 09:20, and 09:25:

- `15M High` = maximum high across the three candles.
- `15M Low` = minimum low across the three candles.
- Freeze at 09:30 only if all three valid candles are present.
- If any is missing or invalid, mark the 15-minute range incomplete for that stock and date. Do not build a partial range and do not issue that day's 15-minute signals.

Direct 15-minute breakout checks start only after the range is complete:

- Close strictly above `15M High` → `15M HIGH BREAK` (bullish).
- Close strictly below `15M Low` → `15M LOW BREAK` (bearish).
- Equality or wick-only crossing → no direct signal.

### 4.2 First 30-minute range

Use the six complete candles beginning at 09:15 through 09:40:

- `30M High` = maximum high across the six candles.
- `30M Low` = minimum low across the six candles.
- Freeze at 09:45 only if all six valid candles are present.
- If any is missing or invalid, mark the 30-minute range incomplete for that stock and date. Do not build a partial range and do not issue that day's 30-minute signals.

Direct 30-minute breakout checks start only after the range is complete:

- Close strictly above `30M High` → `30M HIGH BREAK` (bullish).
- Close strictly below `30M Low` → `30M LOW BREAK` (bearish).
- Equality or wick-only crossing → no direct signal.

Each timeframe is independent. An incomplete 15-minute range does not by itself invalidate a complete 30-minute range, or vice versa.

## 5. Collision formation

Collision detection starts only after its matching range is complete. A collision candle does not create a final signal.

For a range with high `H` and low `L`, and a completed 5-minute candle with high `C_H`, low `C_L`, and close `C`:

- **High-side collision:** `C_H > H` and `L < C < H`.
  - Create/update the active high collision level: `max(H, C_H)`.
- **Low-side collision:** `C_L < L` and `L < C < H`.
  - Create/update the active low collision level: `min(L, C_L)`.
- **Both sides:** If the same candle crosses both range edges and closes strictly inside the range, record both collisions.
- A close exactly at `H` or `L` is not strictly inside and does not form a collision.
- A wick crossing without a close strictly inside the original range is not a collision.
- Direct breakouts and collision formation are evaluated independently. A later valid collision may form after a direct breakout.

Maintain separate 15-minute and 30-minute collision states. On the same timeframe and side, the **latest valid collision replaces the active level**; retain the replaced level and its forming candle in history. Updating one side must not change the other side.

## 6. Collision breakout confirmation

A collision breakout must be confirmed by a **later** completed candle; the collision-forming candle itself can never confirm its own level:

- Close strictly above active high collision level → `15M COLLISION HIGH BREAK` or `30M COLLISION HIGH BREAK` (bullish).
- Close strictly below active low collision level → corresponding `COLLISION LOW BREAK` (bearish).
- Equality or a wick-only crossing → no signal.

A confirmed collision level remains in history and is not deleted by a signal. A later valid collision on the same side replaces the active level. The active level is invalidated only by a same-day state correction/recomputation or the next trading-day reset; do not discard it merely because it has signalled.

## 7. Repeated events and signal records

- A price signal is independent of OI availability and OI score.
- Record each confirmed timeframe/type as a separate signal, even when several confirmations are grouped into one notification.
- If one candle confirms both the 15-minute and 30-minute versions, retain both. If it confirms a direct and a collision level, retain both distinct confirmations.
- Suppress repeat alerts on consecutive completed candles that remain beyond the same unchanged threshold.
- Re-arm a high-side threshold after a completed candle closes at or below that threshold; re-arm a low-side threshold after a completed candle closes at or above it. A later close beyond the threshold is a new valid event.
- Allow an opposite-direction event later in the day.
- Continue monitoring after a signal. Preserve old collision levels and signal history even when a new same-side collision supersedes the active one.
- Use a deterministic event key so a reconnect or restart cannot deliver the same event twice.
- A candle correction can invalidate or change a derived event. Preserve the old alert and record the recomputed state; do not silently delete evidence.

## 8. Signal catalogue

| Direction | Signal |
|---|---|
| Bullish | `15M HIGH BREAK` |
| Bearish | `15M LOW BREAK` |
| Bullish | `15M COLLISION HIGH BREAK` |
| Bearish | `15M COLLISION LOW BREAK` |
| Bullish | `30M HIGH BREAK` |
| Bearish | `30M LOW BREAK` |
| Bullish | `30M COLLISION HIGH BREAK` |
| Bearish | `30M COLLISION LOW BREAK` |

Only confirmed signals appear on the main signal dashboard. Beacon appearances, waiting states, wick-only crossings, rejection candles, and collision formation belong in internal state/audit views only.

## 9. OI is context, not a signal prerequisite

The engine may attach futures/options context to a confirmed price signal:

- Futures OI and OI change/percentage, volume, and futures price movement.
- Call and Put OI by contract, OI change over 5/10/15 minutes, option volume and option price movement.
- Expiry, strike, distance from the futures price, and snapshot/provider timestamps.
- A multi-factor score only when the configuration has been calibrated and approved.

Do not blindly sum contracts or expiries into a stock-level value. Keep raw contract data visible and label aggregates separately. If OI is missing, stale, illiquid, or not calibrated, send the price signal with that status. OI alone cannot prove whether traders are buying or selling.

## 10. Daily state

At the next exchange trading date:

- Begin a new daily symbol list and new range/collision state.
- Preserve all previous dates, signals, Beacon observations, candles, OI snapshots, and audit events according to the approved retention policy.
- Do not carry a prior day's range, collision level, or deduplication state into the new day.

## 11. Reference pseudocode

```text
for each valid trading date:
    initialize daily state and provider health

    every configured 5-minute Beacon poll:
        record both directions
        add eligible new symbols to today's watchlist

    for each monitored symbol and each completed 5-minute candle in order:
        validate candle and provider freshness
        update opening ranges while their windows are forming
        freeze a range only when every required candle is valid

        for each completed range (15M and/or 30M):
            detect valid high/low collisions
            update only the matching active collision side
            evaluate strict direct close breaks
            evaluate collision breaks only against a level formed by an earlier candle
            emit distinct events and suppress consecutive duplicates
            attach available OI context without gating the price event
            persist state, audit event, signal, and notification outbox entry
```

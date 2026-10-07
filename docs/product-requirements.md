# Product requirements

## 1. Product summary

Build a private, single-user app that monitors NSE F&O stocks, finds approved bullish and bearish Breakout Beacon symbols, and identifies confirmed 5-minute breakouts of the first 15-minute and 30-minute ranges. It also reads futures/options OI and related activity as supporting context. The main dashboard and alerts show confirmed price signals only.

The app is an information and discipline tool. It does not promise profitability and does not trade for the user.

## 2. User and platforms

- **Primary user:** One private user; no multi-user account system is required for V1.
- **Host:** A reliable, always-on Windows computer selected by the owner.
- **Dashboard clients:** Modern Chrome or Edge on Windows and Android. The phone layout is a priority.
- **Deployment:** Local computer and, when enabled, its trusted private LAN. No public hosting or public inbound access by default.
- **Language and formatting:** English, IST, and Indian number formatting.

## 3. Outcomes

1. Collect Beacon symbols without losing a symbol that disappears later in the session.
2. Evaluate the same completed 5-minute candle and range rules consistently for every monitored symbol.
3. Send an alert soon after a valid completed candle is available, subject to provider latency.
4. Explain every alert with its candle, price, level, data timestamp, and available OI context.
5. Recover safely after a restart or provider reconnect without losing state or repeating an already delivered event.
6. Validate strategy logic through historical replay and observation mode before the user relies on live alerts.

## 4. Scope for V1

### Required

- Approved or manually imported Beacon input, with bullish and bearish observations recorded.
- A daily monitoring list that retains each symbol for the full trading day.
- Five-minute stock-price candles, 15-minute and 30-minute opening ranges, direct breakouts, and collision breakouts as defined in `strategy-rules.md`.
- Futures and nearby options OI context, including OI change, volume, option price, expiry, strike, and provider timestamps where supplied.
- OI confirmation/scoring context that does not block a valid price alert. Exact thresholds must be calibrated and approved.
- Responsive signal dashboard; separate system-health/status view; searchable signal history.
- Telegram and in-app/browser alert delivery, with grouped notifications for simultaneous signals and separate stored signal records.
- Audit history, duplicate suppression, retry/reconnect behavior, persistence, and safe daily reset.
- Backtest/replay fixtures and an observation mode.
- Local Windows deployment instructions and secure handling of credentials.

### Explicitly out of scope for V1

- Automatic order placement, order modification, cancellation, portfolio management, or position sizing.
- Public or multi-tenant service, public internet exposure, or a cloud-hosted database.
- Index instruments; stock F&O instruments come first.
- Treating an OI increase as proof of option buying, selling, or direction.
- IV and Greeks; these may be considered later.
- Showing waiting symbols, Beacon appearances, wick-only crossings, rejection candles, or collision formation on the main signal screen.
- Claims of a fixed win rate, guaranteed profit, or investment advice.

## 5. Functional requirements

### 5.1 Symbol collection and watchlist

- Collect both Beacon directions every five minutes during the configured regular-session window.
- Add a symbol the first time it appears that trading day, even if it appears late.
- Keep monitoring it after it disappears from Beacon; record subsequent appearances and direction changes with timestamps.
- If the same symbol appears in both directions, retain both observations and flag the conflict; do not silently choose one.
- Support manual add and same-day exclusion.
- Map Beacon symbols to provider instruments explicitly. Log mapping failures and do not calculate from an unknown or mismatched instrument.
- If automated Beacon access is not permitted or available, support a manual symbol-list import. Do not automate access by bypassing a protection.
- Show the watchlist and intake health on a separate optional view. Do not show unconfirmed Beacon events on the main signal view.

### 5.2 Price and strategy

- Use cash-market stock prices for breakout calculations.
- Use only complete 5-minute candles; use candle high, low, close, and timestamp.
- Build exact 9:15–9:30 and 9:15–9:45 opening ranges. Freeze each range only when its required candles are complete.
- Do not create a final signal while the relevant range is forming.
- A missing opening candle invalidates that symbol's corresponding range for the day; never use a partial range.
- Apply strict level comparisons. A close exactly on a level is neither a breakout nor an inside-range collision close.
- Evaluate direct and collision breakouts on completed candles only.
- Keep 15-minute and 30-minute strategy state separate.
- Continue monitoring after any signal. Later genuine events, including an opposite-direction signal, are allowed.
- Do not duplicate notifications for consecutive candles beyond the same unchanged level. Re-arm after price closes back on the non-breakout side of that threshold.

The complete rules and edge cases are in `strategy-rules.md`.

### 5.3 OI context

- Read futures OI and options OI by contract, not as if OI were one value belonging to a stock.
- Keep Calls and Puts separate; show contract-level data and calculated nearby-strike aggregates separately.
- Prefer a futures price for ATM/strike context while keeping the cash stock price as the breakout reference.
- Compare reliable observations over 5, 10, and 15 minutes and against an intraday time-of-day baseline where data supports it.
- Include volume, option price, underlying movement, strike distance, expiry, and futures OI context when available.
- Ignore stale or illiquid observations for scoring and label missing/stale data clearly.
- Do not gate a valid price signal on OI. Send the price signal with an OI status of supportive, mixed, stale, unavailable, or not calibrated only when the configured scoring rules justify that label.
- Never call OI alone proof of buying or selling.

### 5.4 Dashboard and notifications

- Show confirmed signals, newest first. Clearly mark direction with text/iconography as well as color.
- For each signal show symbol, direction, timeframe, direct/collision type, completed candle close, exact level, opening range high/low, candle time, source, and data timestamp.
- Show OI/futures context when available, including contract and provider timestamps.
- Link to the symbol's TradingView 5-minute chart when a valid link can be built.
- Provide filters for direction, symbol, 15M/30M, direct/collision, and OI status.
- Keep signals visible through the trading day and in searchable history. Dismissing a notification does not delete the record.
- Group simultaneous confirmations into a notification while retaining separate signal records.
- Send one alert per valid event. Preserve a signal even if its notification fails; retry delivery and show the failure.
- Provide a visible “No confirmed signals currently” state and system health.
- Provide pause/resume controls. Outside market hours, do not send routine signal notifications.
- In-app dashboard updates are required. Native browser push is optional until the LAN HTTPS/security approach is selected; Telegram is the practical phone notification channel.

### 5.5 Audit, recovery, and data management

- Persist the daily watchlist, candle inputs, range state, collision state, signals, OI observations, alert deliveries, and audit events needed for recovery and replay.
- Restore the current day's state after restart and avoid duplicate alerts.
- Record timestamps and reasons for range formation, collision formation/update, breakout, OI observation, alert attempt, correction, provider status change, and daily reset.
- Detect missing, delayed, stale, corrected, and out-of-order provider data.
- If data cannot be reconstructed reliably, suppress affected signals and show a health warning.
- Support data export and owner-requested deletion. Do not silently delete audit records.

## 6. Quality requirements

- **Correctness:** Rule outcomes must be deterministic for identical candle inputs and configuration.
- **Latency:** Aim for seconds after the provider makes a completed candle available. The exact numeric target is not approved and must be set after provider testing.
- **Availability:** High reliability during 9:15–15:30 IST is required; numeric uptime target is TBD.
- **Data integrity:** No silent default values for missing candles, stale quotes, unknown symbols, or absent OI.
- **Recoverability:** A restart must not lose the day's state or resend an already delivered event.
- **Security:** Keep the dashboard private to the host/trusted LAN; secure local credentials; no secrets in source control or logs.
- **Observability:** Expose feed freshness, Beacon state, instrument mapping, database state, and alert-delivery state to the user.
- **Maintainability:** Keep vendor-specific APIs behind adapters so provider changes do not rewrite strategy rules.

## 7. User-facing safety language

Show a clear notice that signals are informational, are not guaranteed, and are not investment advice. Trading involves risk; the user makes the final decision. Use neutral OI wording such as “OI increased” or “price/OI configuration indicates…” rather than claiming that OI proves a trade direction.

## 8. Definition of a V1 release candidate

A release candidate is ready for owner review only when:

1. The documented strategy tests pass on deterministic examples.
2. Historical replay can explain every alert from source candles and configuration.
3. The chosen data access and TradeFinder method are permitted and the real-time entitlement is verified.
4. Stale/missing/corrected data and provider outages fail safely.
5. The app has completed an owner-approved observation period with no automatic orders.
6. Remaining release gates in `open-decisions.md` have an owner-approved answer.

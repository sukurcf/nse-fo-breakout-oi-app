# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One private user, Chand, who monitors NSE F&O stocks during the trading session and makes trading decisions manually.

## Product Purpose

The app monitors eligible stocks from an approved TradeFinder Breakout Beacon input, applies the defined five-minute 15-minute/30-minute range and collision rules, and presents confirmed breakout events with futures/options OI context. Success means consistent, explainable signals, clear data health, and reliable alerts. The app does not guarantee profit or signal accuracy.

## Positioning

A local, alerts-only monitor that joins an approved Beacon universe, deterministic opening-range/collision rules, and contract-level OI context in one auditable workflow. It preserves the user as the decision-maker; it does not place trades.

## Operating Context

The service runs on an always-on Windows computer during NSE regular market hours, 09:15–15:30 IST, with pre-market initialization. The private dashboard is used from Chrome or Edge on Windows and Android, preferably on the same trusted local network. Price and derivatives data, approved Beacon access, and Telegram require internet access. The user may use manual Beacon-list import if automated access is not permitted.

## Capabilities and Constraints

- V1 includes Beacon intake, daily watchlist, completed five-minute candles, 15-minute and 30-minute opening ranges, collision levels, confirmed signal history, dashboard, health status, audit log, and Telegram/in-app alerts.
- Futures and options OI, volume, option price, expiry, and strike data are contextual scoring information; OI is not proof of buying or selling and does not gate a valid price signal.
- No automatic order placement, modification, or cancellation in V1.
- No partial opening ranges, unfinished candles, wick-only final signals, silent substitution for missing data, or signals from stale price data.
- Live provider entitlements, licences, exact TradeFinder access, data thresholds, local-network authentication, retention, backups, and several release targets are undecided. Do not invent them.
- If approved Beacon automation is unavailable, manual symbol import is the fallback. Never bypass authentication or anti-bot controls.
- The app is private and local by default; no public hosting or public internet exposure.

## Evidence on Hand

The repository contains the owner's completed NSE F&O breakout and OI questionnaire, strategy rules, and implementation requirements in `docs/`. Provider documentation, entitlements, historical test cases, and licensed market data still need to be confirmed or supplied. Do not fabricate performance evidence or real market data.

## Product Principles

1. A price signal is determined by completed candles and explicit, testable rules.
2. Missing, stale, or incomplete data must be visible and must not become a successful-looking signal.
3. OI adds context; it does not prove direction or suppress a valid price signal.
4. Every alert must be traceable to its source candle, level, configuration, and data timestamps.
5. Keep the user in control; V1 reports and never places trades.

## Accessibility & Inclusion

The dashboard must work on Windows and Android browsers. Distinguish bullish and bearish states with text or icons as well as color, keep controls keyboard-accessible, and expose data/connection status in text.

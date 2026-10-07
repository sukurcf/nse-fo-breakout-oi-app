# Implementation guardrails

Read `README.md` and the applicable documents in `docs/` before changing this repository.

- Treat `docs/strategy-rules.md` as the source of truth for signal logic. Add deterministic tests for every rule and boundary before changing it.
- Do not emit a final signal from an unfinished candle, a wick-only crossing, a partial opening range, stale data, or an unapproved fallback source.
- Do not invent OI thresholds, score weights, provider permissions, or market-data entitlements. Keep unapproved values configurable and visibly uncalibrated.
- The OI score is context, not a gate on a valid price signal and not proof of buying or selling.
- Never implement order placement in V1. Any future order-execution work needs a separate, explicitly approved risk and execution specification.
- Use only documented, permitted data access. Do not scrape TradeFinder or bypass authentication, CAPTCHA, bot checks, rate limits, or licensing restrictions.
- Keep credentials out of source control, logs, screenshots, fixtures, and chat. Use a secure local configuration mechanism.
- The deployment is private and local by default. Do not bind a dashboard to public interfaces, configure port forwarding, or add a cloud dependency without explicit approval.
- Preserve original data, state transitions, corrections, and alert attempts in the audit history. Do not silently overwrite or delete evidence.
- Fail visibly and safely: report stale feeds, missing candles, incomplete ranges, provider errors, and alert-delivery failures. Never convert missing data into a successful-looking signal.
- Do not describe backtest results as a guarantee of future performance.

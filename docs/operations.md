# Operations guide

This guide describes intended operation and the limits of the current replay build. Run commands and normalized import instructions are in the repository `README.md`.

## Current implementation boundary

- The dashboard binds to loopback only and denies non-loopback clients. LAN/Android access is not enabled.
- Beacon lists, candles, and OI are manually imported. There is no live feed, automated Beacon collection, Telegram delivery, or native browser push.
- Historical replay is not live monitoring. Provider entitlements, the exchange calendar, OI calibration, stale-data thresholds, retention, and backup policy remain unapproved.
- Order placement, modification, and cancellation are not implemented.

## 1. Before the market session

1. Ensure the Windows host is powered on, awake, internet-connected, and on the expected local network.
2. Start the app early enough to validate local storage, exchange calendar, credentials, provider entitlements, instrument mappings, and Telegram delivery before 09:15 IST. Exact startup lead time is TBD.
3. Check the health view. Resolve or acknowledge stale feeds, authentication failures, missing permissions, unmapped symbols, and database errors before relying on alerts.
4. Confirm the day's Beacon input is using an approved automated method or the manual import fallback.
5. Keep pre-open data disabled.

The owner may choose Windows Task Scheduler or another local startup mechanism after the install design is set. Do not configure a schedule that starts after the required morning health check.

## 2. During the session

- Monitor regular NSE session 09:15–15:30 IST on exchange trading days.
- Poll Beacon every five minutes inside the configured session window; exact first and last poll boundaries remain configurable.
- Keep each valid Beacon symbol on today's monitoring list after it disappears.
- Build the 15-minute range from 09:15–09:30 and the 30-minute range from 09:15–09:45.
- Process only completed candles. Do not treat a missing or stale candle as a valid close.
- Review the main dashboard for confirmed price signals. Use the separate status view for watchlist, provider health, OI freshness, and alert-delivery errors.
- If a provider is unavailable, stop affected calculations and show the outage. Do not infer a signal from old data.
- Use pause/stop controls when the user does not want alerts; preserve all records.

## 3. Phone and network use

- The current build serves the dashboard only on the Windows host through loopback. Requests from Android or other computers are denied.
- Future Android access is only from the same trusted private network after local access security is configured and approved.
- No router port forwarding or public tunnel is allowed by default.
- If LAN HTTPS/authentication is not ready, use the Windows browser. Telegram phone notifications are not implemented in the replay build.
- Telegram uses the internet; local hosting does not mean external API services are unnecessary.

## 4. After the session

- Stop routine signal notifications after 15:30 IST.
- Preserve the complete day's watchlist, ranges, active/replaced collision states, candles, OI observations, signals, audit events, and delivery status.
- Reconcile late provider corrections; keep prior values and record any derived state change.
- Prepare a new daily state before the next exchange session. Do not carry forward range/collision/dedup state.
- Review system health and unresolved alert retries.

## 5. Restart and recovery

After a crash, host reboot, or provider reconnect:

1. Show the outage window and last known good timestamps.
2. Restore the current day's persisted state.
3. Reconnect with bounded backoff and respect provider quotas.
4. Fetch/reconcile recent missing candles and provider corrections if authorized and available.
5. Recompute affected downstream state in timestamp order.
6. Do not emit duplicate events already recorded or delivered.
7. Suppress signals for unrecoverable gaps and explain which symbol/timeframe is affected.

Never mark data current solely because the service restarted successfully.

## 6. Backups and sensitive data

- Use the dashboard's **Download local backup** control to create an owner-initiated consistent SQLite snapshot; save it to a private local storage location selected by the owner.
- Confirm the backup frequency, retention period, and restore procedure before live use.
- Do not sync the database, provider data, or credentials to cloud storage unless a separate licence/security review approves it.
- Store provider and Telegram credentials outside the repository, preferably in Windows Credential Manager or another approved secure local store.
- Do not share credentials in messages or include them in logs.

To restore a downloaded snapshot, stop the service, preserve the existing database as a separate file, place the snapshot at the configured local database path, and start the service. Confirm the dashboard's trading-day history and audit rows after restart. Do not overwrite an existing database without first preserving it.

## 7. Stopping the application

The user must be able to pause alerts or stop the service without deleting data. On stop, finish or persist in-progress notification state safely. On the next start, restore the current session and avoid duplicate alerts.

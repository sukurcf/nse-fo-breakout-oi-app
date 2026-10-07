from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence
from uuid import uuid4

from nse_fo_breakout.importers import (
    IDENTIFIER_PATTERN,
    ImportFormatError,
    normalize_symbol,
    normalize_symbol_list,
    parse_candle_csv,
)
from nse_fo_breakout.oi import parse_oi_csv
from nse_fo_breakout.storage import MappingError, Store
from nse_fo_breakout.strategy import (
    DataIssue,
    Evaluation,
    IST,
    evaluate_session,
)


class ReplayService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def restore_pending_replays(self) -> int:
        pending = self.store.get_pending_replay_targets()
        for trading_date, symbol in pending:
            evaluation = self.evaluate_symbol(
                trading_date=trading_date,
                symbol=symbol,
            )
            self.store.reconcile_evaluation(
                trading_date=trading_date,
                symbol=symbol,
                evaluation=evaluation,
                correlation_id=f"startup-replay:{uuid4()}",
            )
        return len(pending)

    def add_watchlist_mapping(
        self,
        *,
        trading_date: date,
        symbol: str,
        provider_instrument_id: str,
        fno_eligible_confirmed: bool,
    ) -> dict[str, Any]:
        normalized_symbol = normalize_symbol(symbol)
        instrument_id = provider_instrument_id.strip()
        if not IDENTIFIER_PATTERN.fullmatch(instrument_id):
            raise ImportFormatError("provider_instrument_id must be a safe source identifier.")
        mapping_id = self.store.save_mapping_and_watchlist(
            trading_date=trading_date,
            symbol=normalized_symbol,
            provider_instrument_id=instrument_id,
            fno_eligible_confirmed=fno_eligible_confirmed,
        )
        if self.store.get_candles(trading_date=trading_date, symbol=normalized_symbol):
            evaluation = self.evaluate_symbol(
                trading_date=trading_date,
                symbol=normalized_symbol,
            )
            self.store.reconcile_evaluation(
                trading_date=trading_date,
                symbol=normalized_symbol,
                evaluation=evaluation,
                correlation_id=str(uuid4()),
            )
        return {
            "symbol": normalized_symbol,
            "provider_instrument_id": instrument_id,
            "mapping_id": mapping_id,
            "trading_date": trading_date.isoformat(),
            "status": "mapped_and_monitored",
        }

    def import_beacon(
        self,
        *,
        trading_date: date,
        bullish_symbols: Sequence[str],
        bearish_symbols: Sequence[str],
        source_name: str,
        provider_time: datetime | None,
    ) -> dict[str, Any]:
        bullish = normalize_symbol_list(bullish_symbols)
        bearish = normalize_symbol_list(bearish_symbols)
        for symbol in bullish + bearish:
            self._validate_session_date(provider_time, trading_date, field="provider_time")
        return self.store.import_beacon_observations(
            trading_date=trading_date,
            bullish_symbols=bullish,
            bearish_symbols=bearish,
            source_name=source_name,
            provider_time=provider_time,
            correlation_id=str(uuid4()),
        )

    def import_candle_csv(self, *, trading_date: date, csv_text: str) -> dict[str, Any]:
        candles = parse_candle_csv(csv_text, trading_date=trading_date)
        for candle in candles:
            self._validate_session_date(
                candle.provider_time,
                trading_date,
                field="provider_time",
            )
        import_result = self.store.import_candles(
            trading_date=trading_date,
            candles=candles,
            correlation_id=str(uuid4()),
        )
        summaries: list[dict[str, Any]] = []
        for symbol in sorted({candle.symbol for candle in candles}):
            evaluation = self.evaluate_symbol(trading_date=trading_date, symbol=symbol)
            self.store.reconcile_evaluation(
                trading_date=trading_date,
                symbol=symbol,
                evaluation=evaluation,
                correlation_id=str(uuid4()),
            )
            summaries.append(
                {
                    "symbol": symbol,
                    "ranges": {
                        timeframe: {
                            "status": opening.status,
                            "high": self._decimal(opening.high),
                            "low": self._decimal(opening.low),
                            "freeze_at": opening.freeze_at.isoformat(),
                            "missing_intervals": [
                                interval.isoformat()
                                for interval in opening.missing_intervals
                            ],
                        }
                        for timeframe, opening in evaluation.ranges.items()
                    },
                    "active_signals": len(evaluation.signals),
                    "issues": [
                        {
                            "code": issue.code,
                            "message": issue.message,
                            "timeframe": issue.timeframe,
                            "interval_start": (
                                issue.interval_start.isoformat()
                                if issue.interval_start is not None
                                else None
                            ),
                        }
                        for issue in evaluation.issues
                    ],
                }
            )
        return {
            **import_result,
            "symbols_reconciled": summaries,
            "mode": "historical_replay_only",
        }

    def import_oi_csv(self, *, trading_date: date, csv_text: str) -> dict[str, Any]:
        observations = parse_oi_csv(csv_text, trading_date=trading_date)
        result = self.store.import_oi_observations(
            trading_date=trading_date,
            observations=observations,
            correlation_id=str(uuid4()),
        )
        for symbol in sorted({observation.symbol for observation in observations}):
            if not self.store.get_candles(
                trading_date=trading_date,
                symbol=symbol,
            ):
                continue
            evaluation = self.evaluate_symbol(
                trading_date=trading_date,
                symbol=symbol,
            )
            self.store.reconcile_evaluation(
                trading_date=trading_date,
                symbol=symbol,
                evaluation=evaluation,
                correlation_id=str(uuid4()),
            )
        return {
            **result,
            "oi_score": None,
            "oi_status": "raw_only_uncalibrated",
            "note": "OI is contract-level context only; no score or directional claim is produced.",
        }

    def evaluate_symbol(self, *, trading_date: date, symbol: str) -> Evaluation:
        candles = self.store.get_candles(trading_date=trading_date, symbol=symbol)
        watch = self.store.get_watchlist_entry(trading_date=trading_date, symbol=symbol)
        mapping = (
            self.store.get_mapping(mapping_id=watch["mapping_id"])
            if watch is not None
            else None
        )
        failure: DataIssue | None = None
        if watch is None or watch["excluded"]:
            failure = DataIssue(
                code="SYMBOL_NOT_MONITORED",
                message="Symbol is not currently eligible on this trading-day watchlist.",
                symbol=symbol,
            )
        elif mapping is None or mapping["fno_eligible"] != 1:
            failure = DataIssue(
                code="INSTRUMENT_MAPPING_MISSING",
                message="No active, user-confirmed NSE F&O instrument mapping is available.",
                symbol=symbol,
            )
        elif any(
            candle.provider_instrument_id != mapping["provider_instrument_id"]
            for candle in candles
        ):
            failure = DataIssue(
                code="INSTRUMENT_MAPPING_MISMATCH",
                message="Imported candle IDs do not match the active instrument mapping.",
                symbol=symbol,
            )
        if failure is not None:
            result = evaluate_session(
                [],
                trading_date=trading_date,
                symbol=symbol,
            )
            return replace(result, issues=(*result.issues, failure))
        return evaluate_session(
            candles,
            trading_date=trading_date,
            symbol=symbol,
        )

    def get_watchlist(self, *, trading_date: date) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self.store.get_watchlist(trading_date=trading_date):
            symbol = row["symbol"]
            candles = self.store.get_candles(trading_date=trading_date, symbol=symbol)
            evaluation = self.evaluate_symbol(
                trading_date=trading_date,
                symbol=symbol,
            )
            row["candles_loaded"] = len(candles)
            row["opening_ranges"] = [
                {
                    "timeframe": timeframe,
                    "status": opening.status,
                    "high": self._decimal(opening.high),
                    "low": self._decimal(opening.low),
                    "missing_candles": len(opening.missing_intervals),
                    "reason": opening.reason,
                }
                for timeframe, opening in evaluation.ranges.items()
            ]
            row["data_issues"] = [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "timeframe": issue.timeframe,
                    "interval_start": (
                        issue.interval_start.isoformat()
                        if issue.interval_start is not None
                        else None
                    ),
                }
                for issue in evaluation.issues
            ]
            row["is_excluded"] = bool(row["excluded"])
            result.append(row)
        return result

    def get_signals(
        self,
        *,
        trading_date: date,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self.store.get_signals(
            trading_date=trading_date,
            include_superseded=include_superseded,
        )
        enriched: list[dict[str, Any]] = []
        for row in rows:
            signal_time = datetime.fromisoformat(row["candle_end"])
            contexts = self.store.get_oi_context(
                trading_date=trading_date,
                symbol=row["symbol"],
                at_or_before=signal_time,
            )
            row["oi_contexts"] = contexts
            row["oi_context_status"] = row["oi_status"]
            row["oi_status"] = row["oi_context_status"]
            row["oi_score"] = None
            row["label"] = self._signal_label(row)
            enriched.append(row)
        return enriched

    def get_oi_context(
        self,
        *,
        trading_date: date,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        symbols = (
            [normalize_symbol(symbol)]
            if symbol is not None
            else [
                row["symbol"]
                for row in self.store.get_watchlist(trading_date=trading_date)
                if not row["excluded"]
            ]
        )
        observations: list[dict[str, Any]] = []
        for current_symbol in symbols:
            observations.extend(
                self.store.get_oi_context(
                    trading_date=trading_date,
                    symbol=current_symbol,
                    limit=300,
                )
            )
        observations.sort(
            key=lambda item: (item["provider_time"], item["symbol"], item["contract_type"]),
            reverse=True,
        )
        return observations

    def get_history(
        self,
        *,
        trading_date: date,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        signals = self.get_signals(
            trading_date=trading_date,
            include_superseded=True,
        )
        if symbol is None:
            return signals
        normalized = normalize_symbol(symbol)
        return [signal for signal in signals if signal["symbol"] == normalized]

    def get_status(self, *, trading_date: date) -> dict[str, Any]:
        counts = self.store.overview_counts(trading_date=trading_date)
        watchlist = self.get_watchlist(trading_date=trading_date)
        oi_context = self.get_oi_context(trading_date=trading_date)
        ranges_ready = sum(
            1
            for item in watchlist
            if not item["is_excluded"]
            for opening in item["opening_ranges"]
            if opening["status"] == "complete"
        )
        issue_count = sum(
            len(item["data_issues"]) for item in watchlist if not item["is_excluded"]
        )
        return {
            "trading_date": trading_date.isoformat(),
            "mode": "historical_replay_only",
            "live_alerts_enabled": False,
            "alerts_paused": self.store.alerts_paused(),
            "watchlist_count": counts["watchlist_count"],
            "candle_interval_count": counts["candle_interval_count"],
            "active_signal_count": counts["active_signal_count"],
            "in_app_notification_count": counts["in_app_notification_count"],
            "ranges_ready": ranges_ready,
            "data_issue_count": issue_count,
            "last_candle_import_at": counts["last_candle_import_at"],
            "oi_observation_count": counts["oi_observation_count"],
            "integrations": {
                "beacon": "manual_import_only",
                "price_data": "authorized_csv_replay_only",
                "oi_data": (
                    self.oi_context_status(oi_context)
                    if oi_context
                    else "not_configured"
                ),
                "telegram": "disabled_credentials_and_delivery_not_configured",
                "exchange_calendar": "not_configured_special_sessions_unsupported",
            },
            "network": "loopback_only",
            "warnings": [
                "This build replays user-imported data; it does not connect to live feeds.",
                "Historical replay is not a live alert and does not verify provider licensing.",
                "OI is raw contract context only; thresholds, scoring, and directional labels are not calibrated.",
                "No exchange calendar is configured; special sessions and trading-day validity are not verified.",
                "Telegram delivery, LAN access, and order execution are disabled.",
            ],
        }

    def get_audit_events(self, *, trading_date: date, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.get_audit_events(trading_date=trading_date, limit=limit)

    def get_notifications(self, *, trading_date: date, limit: int = 500) -> list[dict[str, Any]]:
        return self.store.get_notifications(trading_date=trading_date, limit=limit)

    def export_database_backup(self) -> bytes:
        return self.store.export_database_backup()

    def set_alerts_paused(self, *, trading_date: date, paused: bool) -> None:
        self.store.set_alerts_paused(paused=paused, trading_date=trading_date)

    @staticmethod
    def oi_context_status(observations: Sequence[dict[str, Any]]) -> str:
        if not observations:
            return "unavailable"
        statuses = {item["status"] for item in observations}
        if "stale" in statuses:
            return "stale"
        if statuses == {"illiquid"}:
            return "illiquid"
        if "complete" in statuses:
            return "raw_only_uncalibrated"
        return "unavailable"

    def exclude_symbol(
        self, *, trading_date: date, symbol: str, reason: str
    ) -> None:
        normalized = normalize_symbol(symbol)
        self.store.exclude_symbol(
            trading_date=trading_date,
            symbol=normalized,
            reason=reason,
        )

    @staticmethod
    def _validate_session_date(
        value: datetime | None,
        trading_date: date,
        *,
        field: str,
    ) -> None:
        if value is None:
            return
        if value.tzinfo is None or value.utcoffset() is None:
            raise ImportFormatError(f"{field} must be timezone-aware.")
        if value.astimezone(IST).date() != trading_date:
            raise ImportFormatError(f"{field} must fall on trading_date.")

    @staticmethod
    def _decimal(value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")

    @staticmethod
    def _signal_label(row: dict[str, Any]) -> str:
        side = "HIGH" if row["signal_type"].endswith("high") else "LOW"
        collision = " COLLISION" if row["signal_type"].startswith("collision_") else ""
        return f"{row['timeframe']}{collision} {side} BREAK"

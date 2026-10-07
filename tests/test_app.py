from __future__ import annotations

import csv
import io
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from nse_fo_breakout.importers import parse_candle_csv
from nse_fo_breakout.oi import parse_oi_csv
from nse_fo_breakout.web import _csv_cell, create_app


IST = ZoneInfo("Asia/Kolkata")
TRADING_DATE = date(2026, 6, 1)
CANDLE_HEADERS = [
    "exchange",
    "symbol",
    "provider_instrument_id",
    "interval_start",
    "interval_end",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "status",
    "provider_time",
    "source",
]


def candle(
    clock: str,
    *,
    high: str = "99",
    low: str = "81",
    close: str = "90",
    status: str = "complete",
) -> dict[str, str]:
    local_start = datetime.combine(TRADING_DATE, time.fromisoformat(clock), IST)
    local_end = local_start + timedelta(minutes=5)
    return {
        "exchange": "NSE",
        "symbol": "TEST",
        "provider_instrument_id": "NSE:TEST",
        "interval_start": local_start.isoformat(),
        "interval_end": local_end.isoformat(),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": "1000",
        "status": status,
        "provider_time": local_end.isoformat(),
        "source": "fixture",
    }


def opening_rows() -> list[dict[str, str]]:
    return [
        candle("09:15", high="100", low="80"),
        candle("09:20", high="99", low="81"),
        candle("09:25", high="98", low="82"),
        candle("09:30", high="110", low="70", close="100"),
        candle("09:35", high="108", low="72", close="100"),
        candle("09:40", high="107", low="73", close="100"),
    ]


def to_csv(rows: list[dict[str, str]], headers: list[str] = CANDLE_HEADERS) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


class AppTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "monitor.sqlite3"
        self.client = TestClient(
            create_app(db_path=self.db_path),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 45123),
        )
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def add_mapping(self) -> None:
        response = self.client.post(
            "/api/mappings",
            json={
                "trading_date": TRADING_DATE.isoformat(),
                "symbol": "TEST",
                "provider_instrument_id": "NSE:TEST",
                "confirm_fno_eligibility": True,
            },
        )
        self.assertEqual(200, response.status_code, response.text)

    def import_candles(self, rows: list[dict[str, str]]):
        return self.client.post(
            f"/api/candles/import?trading_date={TRADING_DATE.isoformat()}",
            content=to_csv(rows),
            headers={"Content-Type": "text/csv"},
        )

    def test_status_discloses_replay_only_and_unconfigured_integrations(self) -> None:
        response = self.client.get(f"/api/status?trading_date={TRADING_DATE.isoformat()}")

        self.assertEqual(200, response.status_code)
        status = response.json()
        self.assertEqual("historical_replay_only", status["mode"])
        self.assertFalse(status["live_alerts_enabled"])
        self.assertEqual("loopback_only", status["network"])
        self.assertEqual(
            "not_configured_special_sessions_unsupported",
            status["integrations"]["exchange_calendar"],
        )
        self.assertEqual(
            "disabled_credentials_and_delivery_not_configured",
            status["integrations"]["telegram"],
        )

    def test_mapping_requires_explicit_fno_confirmation(self) -> None:
        response = self.client.post(
            "/api/mappings",
            json={
                "trading_date": TRADING_DATE.isoformat(),
                "symbol": "TEST",
                "provider_instrument_id": "NSE:TEST",
                "confirm_fno_eligibility": False,
            },
        )

        self.assertEqual(422, response.status_code)
        self.assertIn("eligibility", response.json()["detail"])
        self.assertEqual([], self.client.get("/api/watchlist").json()["symbols"])

    def test_beacon_dual_direction_is_retained_and_flagged(self) -> None:
        self.add_mapping()
        response = self.client.post(
            "/api/beacon/import",
            json={
                "trading_date": TRADING_DATE.isoformat(),
                "bullish_symbols": ["TEST"],
                "bearish_symbols": ["TEST"],
                "source_name": "manual_import",
            },
        )

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(["TEST"], response.json()["conflicts"])
        symbol = self.client.get(
            f"/api/watchlist?trading_date={TRADING_DATE.isoformat()}"
        ).json()["symbols"][0]
        self.assertTrue(symbol["direction_conflict"])
        self.assertTrue(symbol["bullish_seen"])
        self.assertTrue(symbol["bearish_seen"])

    def test_beacon_symbol_without_mapping_is_visible_but_not_monitored(self) -> None:
        response = self.client.post(
            "/api/beacon/import",
            json={
                "trading_date": TRADING_DATE.isoformat(),
                "bullish_symbols": ["UNMAPPED"],
                "bearish_symbols": [],
                "source_name": "manual_import",
            },
        )

        self.assertEqual(200, response.status_code)
        watchlist = self.client.get(
            f"/api/watchlist?trading_date={TRADING_DATE.isoformat()}"
        ).json()
        self.assertEqual([], watchlist["symbols"])
        self.assertEqual(["UNMAPPED"], watchlist["unmapped_symbols"])

    def test_timezone_and_chronological_import_requirements_are_enforced(self) -> None:
        self.add_mapping()
        naive = candle("09:15")
        naive["interval_start"] = "2026-06-01T09:15:00"
        response = self.import_candles([naive])
        self.assertEqual(422, response.status_code)
        self.assertIn("timezone", response.json()["detail"].lower())

        reversed_rows = list(reversed(opening_rows()))
        response = self.import_candles(reversed_rows)
        self.assertEqual(422, response.status_code)
        self.assertIn("chronological", response.json()["detail"].lower())
        status = self.client.get(
            f"/api/status?trading_date={TRADING_DATE.isoformat()}"
        ).json()
        self.assertEqual(0, status["candle_interval_count"])

    def test_mapping_mismatch_rejects_candles_without_partial_writes(self) -> None:
        self.add_mapping()
        rows = opening_rows()
        rows[-1]["provider_instrument_id"] = "NSE:WRONG"

        response = self.import_candles(rows)

        self.assertEqual(422, response.status_code)
        self.assertIn("does not match", response.json()["detail"])
        status = self.client.get(
            f"/api/status?trading_date={TRADING_DATE.isoformat()}"
        ).json()
        self.assertEqual(0, status["candle_interval_count"])

    def test_completed_price_break_survives_without_oi_and_duplicate_replay(self) -> None:
        self.add_mapping()
        rows = opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        first_import = self.import_candles(rows)
        self.assertEqual(200, first_import.status_code, first_import.text)
        self.assertEqual(7, first_import.json()["inserted"])

        first_signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        self.assertEqual(2, len(first_signals))
        self.assertTrue(all(item["oi_context_status"] == "unavailable" for item in first_signals))
        self.assertTrue(all(item["oi_score"] is None for item in first_signals))
        self.assertTrue(all(item["candle_source"] == "fixture" for item in first_signals))
        self.assertTrue(
            all(item["in_app_notification_status"] == "available" for item in first_signals)
        )
        notifications = self.client.get(
            f"/api/notifications?trading_date={TRADING_DATE.isoformat()}"
        ).json()
        self.assertEqual("disabled", notifications["external_delivery"])
        self.assertEqual(2, len(notifications["notifications"]))
        status = self.client.get(
            f"/api/status?trading_date={TRADING_DATE.isoformat()}"
        ).json()
        self.assertEqual(2, status["in_app_notification_count"])

        second_import = self.import_candles(rows)
        self.assertEqual(0, second_import.json()["inserted"])
        self.assertEqual(7, second_import.json()["duplicates"])
        second_signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        self.assertEqual(
            {item["event_id"] for item in first_signals},
            {item["event_id"] for item in second_signals},
        )
        audit = self.client.get(
            f"/api/audit?trading_date={TRADING_DATE.isoformat()}&limit=500"
        ).json()["events"]
        self.assertEqual(
            2,
            sum(event["event_type"] == "SIGNAL_CONFIRMED_BY_REPLAY" for event in audit),
        )

    def test_oi_is_contract_level_raw_context_without_percentages_or_score(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        oi_headers = [
            "exchange",
            "symbol",
            "provider_instrument_id",
            "contract_type",
            "expiry",
            "strike",
            "option_type",
            "open_interest",
            "oi_change",
            "volume",
            "ltp",
            "provider_time",
            "source",
            "status",
        ]
        oi_csv = (
            "exchange,symbol,provider_instrument_id,contract_type,expiry,strike,option_type,"
            "open_interest,oi_change,volume,ltp,provider_time,source,status\n"
            "NSE,TEST,OPT:TEST:100:C,option,2026-06-25,100,CALL,0,125,240,12.5,"
            "2026-06-01T09:45:00+05:30,fixture,complete\n"
        )
        response = self.client.post(
            f"/api/oi/import?trading_date={TRADING_DATE.isoformat()}",
            content=oi_csv,
            headers={"Content-Type": "text/csv"},
        )

        self.assertEqual(200, response.status_code, response.text)
        context = self.client.get(
            f"/api/oi?trading_date={TRADING_DATE.isoformat()}&symbol=TEST"
        ).json()
        self.assertEqual("raw_only_uncalibrated", context["status"])
        self.assertIsNone(context["oi_score"])
        self.assertEqual("0", context["observations"][0]["open_interest"])
        self.assertNotIn("percentage_change", context["observations"][0])
        signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        self.assertTrue(all(item["oi_context_status"] == "raw_only_uncalibrated" for item in signals))

        malformed = oi_csv.replace("2026-06-25,100,CALL", "2026-06-25,,CALL")
        invalid = self.client.post(
            f"/api/oi/import?trading_date={TRADING_DATE.isoformat()}",
            content=malformed,
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(422, invalid.status_code)

    def test_stale_oi_is_labeled_stale_without_blocking_price_events(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        oi_csv = (
            "exchange,symbol,provider_instrument_id,contract_type,expiry,strike,option_type,"
            "open_interest,oi_change,volume,ltp,provider_time,source,status\n"
            "NSE,TEST,FUT:TEST:2026-06-25,future,2026-06-25,,,1000,25,500,101,"
            "2026-06-01T09:45:00+05:30,fixture,stale\n"
        )
        imported = self.client.post(
            f"/api/oi/import?trading_date={TRADING_DATE.isoformat()}",
            content=oi_csv,
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(200, imported.status_code, imported.text)

        signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        status = self.client.get(
            f"/api/status?trading_date={TRADING_DATE.isoformat()}"
        ).json()

        self.assertEqual(2, len(signals))
        self.assertTrue(all(item["oi_context_status"] == "stale" for item in signals))
        self.assertTrue(all(item["oi_status"] == "stale" for item in signals))
        self.assertEqual("stale", status["integrations"]["oi_data"])
        self.assertTrue(all(item["oi_score"] is None for item in signals))
        audit = self.client.get(
            f"/api/audit?trading_date={TRADING_DATE.isoformat()}&limit=500"
        ).json()["events"]
        self.assertIn("SIGNAL_OI_CONTEXT_UPDATED", {event["event_type"] for event in audit})

    def test_future_oi_snapshot_is_not_attached_to_an_earlier_signal(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        oi_csv = (
            "exchange,symbol,provider_instrument_id,contract_type,expiry,strike,option_type,"
            "open_interest,oi_change,volume,ltp,provider_time,source,status\n"
            "NSE,TEST,FUT:TEST:2026-06-25,future,2026-06-25,,,1000,25,500,101,"
            "2026-06-01T10:00:00+05:30,fixture,complete\n"
        )
        imported = self.client.post(
            f"/api/oi/import?trading_date={TRADING_DATE.isoformat()}",
            content=oi_csv,
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(200, imported.status_code, imported.text)

        signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]

        self.assertEqual(2, len(signals))
        self.assertTrue(all(item["oi_status"] == "unavailable" for item in signals))
        self.assertTrue(all(item["oi_contexts"] == [] for item in signals))

    def test_candle_correction_preserves_old_event_and_audits_replay(self) -> None:
        self.add_mapping()
        initial = opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        self.assertEqual(200, self.import_candles(initial).status_code)
        correction = [candle("09:45", high="110", low="90", close="100")]

        response = self.import_candles(correction)

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, response.json()["corrections"])
        self.assertEqual(
            [],
            self.client.get(
                f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
            ).json()["signals"],
        )
        history = self.client.get(
            f"/api/history?trading_date={TRADING_DATE.isoformat()}"
        ).json()["events"]
        self.assertEqual(2, len(history))
        self.assertTrue(all(item["status"] == "superseded" for item in history))
        audit = self.client.get(
            f"/api/audit?trading_date={TRADING_DATE.isoformat()}&limit=500"
        ).json()["events"]
        event_types = {event["event_type"] for event in audit}
        self.assertIn("CANDLE_CORRECTION_RECORDED", event_types)
        self.assertIn("SIGNAL_SUPERSEDED_BY_REPLAY", event_types)

    def test_late_watchlist_addition_reconstructs_complete_ranges_from_history(self) -> None:
        self.add_mapping()
        response = self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )

        self.assertEqual(200, response.status_code)
        summary = response.json()["symbols_reconciled"][0]
        self.assertEqual("complete", summary["ranges"]["15M"]["status"])
        self.assertEqual("complete", summary["ranges"]["30M"]["status"])
        self.assertEqual(2, summary["active_signals"])

    def test_late_symbol_without_opening_history_has_no_signal(self) -> None:
        self.add_mapping()
        response = self.import_candles(
            [
                candle("09:45", high="113", low="90", close="111"),
                candle("09:50", high="114", low="90", close="112"),
            ]
        )

        self.assertEqual(200, response.status_code)
        summary = response.json()["symbols_reconciled"][0]
        self.assertEqual("incomplete", summary["ranges"]["15M"]["status"])
        self.assertEqual("incomplete", summary["ranges"]["30M"]["status"])
        self.assertEqual(0, summary["active_signals"])
        self.assertEqual(
            [],
            self.client.get(
                f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
            ).json()["signals"],
        )

    def test_mapping_changes_are_scoped_to_the_trading_day(self) -> None:
        self.add_mapping()
        initial_rows = opening_rows() + [
            candle("09:45", high="113", low="90", close="111")
        ]
        self.assertEqual(200, self.import_candles(initial_rows).status_code)
        day_one_signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        day_two = TRADING_DATE + timedelta(days=1)
        remap = self.client.post(
            "/api/mappings",
            json={
                "trading_date": day_two.isoformat(),
                "symbol": "TEST",
                "provider_instrument_id": "NSE:TEST:NEW",
                "confirm_fno_eligibility": True,
            },
        )
        self.assertEqual(200, remap.status_code, remap.text)
        self.assertEqual(
            [],
            self.client.app.state.service.store.get_pending_replay_targets(),
        )
        self.client.__exit__(None, None, None)
        self.client = TestClient(
            create_app(db_path=self.db_path),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 45123),
        )
        self.client.__enter__()
        restored_day_one_signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        self.assertEqual(
            {item["event_id"] for item in day_one_signals},
            {item["event_id"] for item in restored_day_one_signals},
        )

        later_day_one_candle = candle(
            "09:50",
            high="114",
            low="90",
            close="112",
        )
        imported = self.import_candles([later_day_one_candle])

        self.assertEqual(200, imported.status_code, imported.text)
        self.assertEqual(2, imported.json()["symbols_reconciled"][0]["active_signals"])
        self.assertTrue(
            all(
                signal["status"] == "active"
                for signal in self.client.get(
                    f"/api/history?trading_date={TRADING_DATE.isoformat()}"
                ).json()["events"]
            )
        )
        self.assertEqual(
            "NSE:TEST:NEW",
            self.client.get(
                f"/api/watchlist?trading_date={day_two.isoformat()}"
            ).json()["symbols"][0]["provider_instrument_id"],
        )
        self.assertEqual(
            {item["event_id"] for item in day_one_signals},
            {
                item["event_id"]
                for item in self.client.get(
                    f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
                ).json()["signals"]
            },
        )

    def test_stale_post_range_data_suppresses_later_events_and_surfaces_gap(self) -> None:
        self.add_mapping()
        rows = opening_rows() + [
            candle("09:45", high="113", low="90", close="111", status="stale"),
            candle("09:50", high="114", low="90", close="112"),
        ]

        response = self.import_candles(rows)

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            [],
            self.client.get(
                f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
            ).json()["signals"],
        )
        watchlist = self.client.get(
            f"/api/watchlist?trading_date={TRADING_DATE.isoformat()}"
        ).json()["symbols"]
        issue_codes = {issue["code"] for issue in watchlist[0]["data_issues"]}
        self.assertIn("CANDLE_STALE", issue_codes)
        self.assertIn("CANDLE_GAP", issue_codes)

    def test_provider_is_not_claimed_connected_and_calendar_is_not_assumed(self) -> None:
        self.add_mapping()
        late_session_data = [
            candle("10:00", high="120", low="80", close="119"),
            candle("10:05", high="121", low="90", close="120"),
        ]
        self.assertEqual(200, self.import_candles(late_session_data).status_code)
        status = self.client.get(
            f"/api/status?trading_date={TRADING_DATE.isoformat()}"
        ).json()

        self.assertEqual("authorized_csv_replay_only", status["integrations"]["price_data"])
        self.assertNotIn("connected", str(status["integrations"]).lower())
        self.assertIn("special_sessions_unsupported", status["integrations"]["exchange_calendar"])
        self.assertEqual(
            [],
            self.client.get(
                f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
            ).json()["signals"],
        )

    def test_restart_restores_state_without_duplicating_events(self) -> None:
        self.add_mapping()
        rows = opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        self.assertEqual(200, self.import_candles(rows).status_code)
        initial = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        self.client.__exit__(None, None, None)
        self.client = TestClient(
            create_app(db_path=self.db_path),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 45123),
        )
        self.client.__enter__()

        replayed = self.client.post(
            f"/api/candles/import?trading_date={TRADING_DATE.isoformat()}",
            content=to_csv(rows),
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(200, replayed.status_code)
        restored = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        self.assertEqual(
            {item["event_id"] for item in initial},
            {item["event_id"] for item in restored},
        )
        audit = self.client.get(
            f"/api/audit?trading_date={TRADING_DATE.isoformat()}&limit=500"
        ).json()["events"]
        self.assertEqual(
            2,
            sum(event["event_type"] == "SIGNAL_CONFIRMED_BY_REPLAY" for event in audit),
        )

    def test_pause_setting_is_audited_and_does_not_delete_signal_history(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        pause = self.client.post(
            "/api/alerts",
            json={"trading_date": TRADING_DATE.isoformat(), "paused": True},
        )
        self.assertEqual(200, pause.status_code)
        status = self.client.get(
            f"/api/status?trading_date={TRADING_DATE.isoformat()}"
        ).json()

        self.assertTrue(status["alerts_paused"])
        self.assertEqual(2, status["active_signal_count"])
        audit = self.client.get(
            f"/api/audit?trading_date={TRADING_DATE.isoformat()}&limit=500"
        ).json()["events"]
        self.assertIn("ALERTING_PAUSED", {event["event_type"] for event in audit})

    def test_exclusion_is_audited_and_blocks_new_candle_imports(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        response = self.client.post(
            "/api/watchlist/exclude",
            json={
                "trading_date": TRADING_DATE.isoformat(),
                "symbol": "TEST",
                "reason": "Owner excluded for replay",
            },
        )

        self.assertEqual(200, response.status_code)
        item = self.client.get(
            f"/api/watchlist?trading_date={TRADING_DATE.isoformat()}"
        ).json()["symbols"][0]
        self.assertTrue(item["is_excluded"])
        self.assertEqual(
            2,
            self.client.get(
                f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
            ).json()["total"],
        )
        blocked = self.import_candles([candle("09:50", high="120", low="90", close="119")])
        self.assertEqual(422, blocked.status_code)
        audit = self.client.get(
            f"/api/audit?trading_date={TRADING_DATE.isoformat()}&limit=500"
        ).json()["events"]
        self.assertIn("WATCHLIST_SYMBOL_EXCLUDED", {event["event_type"] for event in audit})

    def test_startup_reconciles_raw_candles_left_pending_by_a_crash(self) -> None:
        self.add_mapping()
        rows = opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        service = self.client.app.state.service
        parsed = parse_candle_csv(to_csv(rows), trading_date=TRADING_DATE)
        service.store.import_candles(
            trading_date=TRADING_DATE,
            candles=parsed,
            correlation_id="interrupted-import",
        )
        self.assertEqual(
            [(TRADING_DATE, "TEST")],
            service.store.get_pending_replay_targets(),
        )
        self.client.__exit__(None, None, None)
        self.client = TestClient(
            create_app(db_path=self.db_path),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 45123),
        )
        self.client.__enter__()

        status = self.client.get(
            f"/api/status?trading_date={TRADING_DATE.isoformat()}"
        ).json()
        self.assertEqual(2, status["active_signal_count"])
        self.assertEqual([], self.client.app.state.service.store.get_pending_replay_targets())

    def test_v1_database_migration_backfills_local_notification_records(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        self.client.__exit__(None, None, None)
        connection = sqlite3.connect(self.db_path)
        connection.execute("DROP INDEX notification_outbox_pending")
        connection.execute("DROP TABLE notification_outbox")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()
        self.client = TestClient(
            create_app(db_path=self.db_path),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 45123),
        )
        self.client.__enter__()

        notifications = self.client.get(
            f"/api/notifications?trading_date={TRADING_DATE.isoformat()}"
        ).json()["notifications"]
        signal_foreign_keys = self.client.app.state.service.store._connection.execute(
            "PRAGMA foreign_key_list(signal_events)"
        ).fetchall()
        audit = self.client.get(
            f"/api/audit?trading_date={TRADING_DATE.isoformat()}&limit=500"
        ).json()["events"]

        self.assertEqual(2, len(notifications))
        self.assertEqual(2, len({item["notification_id"] for item in notifications}))
        self.assertEqual(
            {"candle_versions", "range_versions", "collision_levels"},
            {row["table"] for row in signal_foreign_keys},
        )
        self.assertEqual(
            [],
            self.client.app.state.service.store._connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall(),
        )
        self.assertEqual(
            2,
            sum(event["event_type"] == "IN_APP_NOTIFICATION_MIGRATED" for event in audit),
        )

    def test_dashboard_is_local_and_serves_self_hosted_assets(self) -> None:
        response = self.client.get("/")

        self.assertEqual(200, response.status_code)
        self.assertIn("Replay workspace", response.text)
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        script = self.client.get("/static/app.js")
        self.assertEqual(200, script.status_code)
        self.assertIn("DOMContentLoaded", script.text)

    def test_untrusted_remote_clients_are_denied(self) -> None:
        remote_client = TestClient(
            create_app(db_path=Path(self.temp_dir.name) / "remote.sqlite3"),
            base_url="http://192.0.2.1",
            client=("192.0.2.1", 45123),
        )
        with remote_client:
            response = remote_client.get("/")
        self.assertEqual(403, response.status_code)

    def test_dns_rebinding_host_and_cross_origin_posts_are_denied(self) -> None:
        rebound_host = self.client.get(
            "/api/export/backup.sqlite",
            headers={"Host": "attacker.example"},
        )
        cross_origin_post = self.client.post(
            "/api/alerts",
            json={"trading_date": TRADING_DATE.isoformat(), "paused": True},
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )

        self.assertEqual(403, rebound_host.status_code)
        self.assertEqual(403, cross_origin_post.status_code)
        self.assertFalse(self.client.get("/api/status").json()["alerts_paused"])

    def test_csv_endpoint_rejects_wrong_content_type(self) -> None:
        response = self.client.post(
            f"/api/candles/import?trading_date={TRADING_DATE.isoformat()}",
            content="not a csv",
            headers={"Content-Type": "text/plain"},
        )

        self.assertEqual(415, response.status_code)
        self.assertIn("text/csv", response.json()["detail"])

    def test_csv_rejects_extra_fields_without_server_error(self) -> None:
        self.add_mapping()
        rows = opening_rows()
        malformed = to_csv(rows[:1]).strip() + ",unexpected\n"
        response = self.client.post(
            f"/api/candles/import?trading_date={TRADING_DATE.isoformat()}",
            content=malformed,
            headers={"Content-Type": "text/csv"},
        )

        self.assertEqual(422, response.status_code)
        self.assertIn("more values", response.json()["detail"])

    def test_export_uses_ist_and_protects_csv_spreadsheet_cells(self) -> None:
        self.add_mapping()
        rows = opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        self.import_candles(rows)

        response = self.client.get(
            f"/api/export/signals.csv?trading_date={TRADING_DATE.isoformat()}"
        )

        self.assertEqual(200, response.status_code)
        self.assertIn("candle_start_ist", response.text)
        self.assertIn("provider_time_ist", response.text)
        self.assertIn("+05:30", response.text)

    def test_local_backup_is_a_consistent_sqlite_snapshot(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )

        response = self.client.get("/api/export/backup.sqlite")

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/vnd.sqlite3", response.headers["content-type"])
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertTrue(response.content.startswith(b"SQLite format 3"))
        restored = sqlite3.connect(":memory:")
        try:
            restored.deserialize(response.content)
            self.assertEqual(
                2,
                restored.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0],
            )
            self.assertEqual(
                2,
                restored.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0],
            )
            self.assertEqual(
                7,
                restored.execute("SELECT COUNT(*) FROM candle_versions").fetchone()[0],
            )
        finally:
            restored.close()

    def test_csv_cell_prefixes_spreadsheet_formula_markers(self) -> None:
        for value in ("=1+1", "+1+1", "-1+1", "@SUM(A1:A2)", "\tformula"):
            self.assertEqual(f"'{value}", _csv_cell(value))
        self.assertEqual("-125.5", _csv_cell("-125.5"))

    def test_history_and_signal_export_return_more_than_one_thousand_events(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        store = self.client.app.state.service.store
        with store.transaction() as connection:
            template = list(
                connection.execute(
                    "SELECT * FROM signal_events LIMIT 1"
                ).fetchone()
            )
            connection.executemany(
                "INSERT INTO signal_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (f"synthetic-history-{index}", *template[1:])
                    for index in range(1001)
                ],
            )

        history = self.client.get(
            f"/api/history?trading_date={TRADING_DATE.isoformat()}&symbol=TEST"
        ).json()
        exported = self.client.get(
            f"/api/export/signals.csv?trading_date={TRADING_DATE.isoformat()}"
        )

        self.assertEqual(1003, history["total"])
        self.assertEqual(1003, len(history["events"]))
        self.assertEqual(200, exported.status_code)
        self.assertEqual(1004, len(exported.text.splitlines()))

    def test_startup_replays_persisted_oi_context_after_an_interrupted_import(self) -> None:
        self.add_mapping()
        self.import_candles(
            opening_rows() + [candle("09:45", high="113", low="90", close="111")]
        )
        oi_csv = (
            "exchange,symbol,provider_instrument_id,contract_type,expiry,strike,option_type,"
            "open_interest,oi_change,volume,ltp,provider_time,source,status\n"
            "NSE,TEST,FUT:TEST:2026-06-25,future,2026-06-25,,,1000,25,500,101,"
            "2026-06-01T09:45:00+05:30,fixture,complete\n"
        )
        observations = parse_oi_csv(oi_csv, trading_date=TRADING_DATE)
        service = self.client.app.state.service
        service.store.import_oi_observations(
            trading_date=TRADING_DATE,
            observations=observations,
            correlation_id="interrupted-oi-import",
        )
        self.assertEqual(
            [(TRADING_DATE, "TEST")],
            service.store.get_pending_replay_targets(),
        )

        self.client.__exit__(None, None, None)
        self.client = TestClient(
            create_app(db_path=self.db_path),
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 45123),
        )
        self.client.__enter__()

        signals = self.client.get(
            f"/api/signals?trading_date={TRADING_DATE.isoformat()}"
        ).json()["signals"]
        service = self.client.app.state.service
        self.assertEqual(2, len(signals))
        self.assertTrue(all(item["oi_status"] == "raw_only_uncalibrated" for item in signals))
        self.assertTrue(all(len(item["oi_contexts"]) == 1 for item in signals))
        self.assertEqual([], service.store.get_pending_replay_targets())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from nse_fo_breakout.strategy import Candle, CandleStatus, evaluate_session


IST = ZoneInfo("Asia/Kolkata")
TRADING_DATE = date(2026, 6, 1)


def candle(
    clock: str,
    *,
    high: str = "99",
    low: str = "81",
    close: str = "90",
    open_: str | None = None,
    status: CandleStatus = CandleStatus.COMPLETE,
    version_id: str | None = None,
) -> Candle:
    parsed_time = time.fromisoformat(clock)
    start = datetime.combine(TRADING_DATE, parsed_time, IST)
    end = start + timedelta(minutes=5)
    return Candle(
        symbol="TEST",
        provider_instrument_id="NSE:TEST",
        interval_start=start,
        interval_end=end,
        open=Decimal(close if open_ is None else open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        provider_time=end,
        received_at=end,
        source="deterministic-fixture",
        status=status,
        version_id=version_id,
    )


def opening_candles() -> list[Candle]:
    return [
        candle("09:15", high="100", low="80"),
        candle("09:20", high="99", low="81"),
        candle("09:25", high="98", low="82"),
        candle("09:30", high="110", low="70", close="100"),
        candle("09:35", high="108", low="72", close="100"),
        candle("09:40", high="107", low="73", close="100"),
    ]


def evaluate(candles: list[Candle]):
    return evaluate_session(
        candles,
        trading_date=TRADING_DATE,
        symbol="TEST",
    )


class StrategyRuleTests(unittest.TestCase):
    def test_str_01_builds_and_freezes_complete_15m_range(self) -> None:
        result = evaluate(opening_candles())
        opening = result.ranges["15M"]

        self.assertEqual("complete", opening.status)
        self.assertEqual(Decimal("100"), opening.high)
        self.assertEqual(Decimal("80"), opening.low)
        self.assertEqual(datetime(2026, 6, 1, 9, 30, tzinfo=IST), opening.freeze_at)

    def test_str_02_builds_and_freezes_complete_30m_range(self) -> None:
        result = evaluate(opening_candles())
        opening = result.ranges["30M"]

        self.assertEqual("complete", opening.status)
        self.assertEqual(Decimal("110"), opening.high)
        self.assertEqual(Decimal("70"), opening.low)
        self.assertEqual(datetime(2026, 6, 1, 9, 45, tzinfo=IST), opening.freeze_at)

    def test_str_03_missing_15m_opening_candle_invalidates_15m_range(self) -> None:
        candles = [item for item in opening_candles() if item.interval_start.minute != 20]
        candles.append(candle("09:45", high="120", low="90", close="115"))

        result = evaluate(candles)

        self.assertEqual("incomplete", result.ranges["15M"].status)
        self.assertFalse(any(signal.timeframe == "15M" for signal in result.signals))

    def test_str_04_missing_30m_opening_candle_leaves_15m_independent(self) -> None:
        candles = [item for item in opening_candles() if item.interval_start.strftime("%H:%M") != "09:40"]
        candles.append(candle("09:45", high="120", low="90", close="115"))

        result = evaluate(candles)

        self.assertEqual("complete", result.ranges["15M"].status)
        self.assertEqual("incomplete", result.ranges["30M"].status)
        self.assertFalse(any(signal.timeframe == "30M" for signal in result.signals))

    def test_str_05_close_strictly_above_range_high_emits_direct_signal(self) -> None:
        candles = opening_candles()[:3] + [
            candle("09:30", high="103", low="90", close="101")
        ]

        result = evaluate(candles)

        self.assertIn(
            ("15M", "direct_high"),
            {(signal.timeframe, signal.signal_type) for signal in result.signals},
        )

    def test_str_06_close_strictly_below_range_low_emits_direct_signal(self) -> None:
        candles = opening_candles()[:3] + [
            candle("09:30", high="90", low="77", close="79")
        ]

        result = evaluate(candles)

        self.assertIn(
            ("15M", "direct_low"),
            {(signal.timeframe, signal.signal_type) for signal in result.signals},
        )

    def test_str_07_close_equal_to_range_edge_is_not_a_breakout(self) -> None:
        result = evaluate(opening_candles())

        self.assertFalse(result.signals)

    def test_str_08_wick_crossing_without_close_beyond_edge_is_not_a_signal(self) -> None:
        candles = opening_candles()[:3] + [
            candle("09:30", high="105", low="90", close="95")
        ]

        result = evaluate(candles)

        self.assertFalse(result.signals)

    def test_str_09_high_side_collision_records_level_without_signal(self) -> None:
        candles = opening_candles()[:3] + [
            candle("09:30", high="105", low="90", close="95")
        ]

        result = evaluate(candles)
        high_collision = result.active_collision("15M", "high")

        self.assertIsNotNone(high_collision)
        self.assertEqual(Decimal("105"), high_collision.level)
        self.assertFalse(result.signals)

    def test_str_10_low_side_collision_records_level_without_signal(self) -> None:
        candles = opening_candles()[:3] + [
            candle("09:30", high="95", low="75", close="90")
        ]

        result = evaluate(candles)
        low_collision = result.active_collision("15M", "low")

        self.assertIsNotNone(low_collision)
        self.assertEqual(Decimal("75"), low_collision.level)
        self.assertFalse(result.signals)

    def test_str_11_close_exactly_on_range_edge_does_not_form_collision(self) -> None:
        candles = opening_candles()[:3] + [
            candle("09:30", high="105", low="90", close="100")
        ]

        result = evaluate(candles)

        self.assertIsNone(result.active_collision("15M", "high"))
        self.assertFalse(result.signals)

    def test_str_12_one_candle_can_form_both_collision_sides(self) -> None:
        candles = opening_candles() + [
            candle("09:45", high="115", low="65", close="100")
        ]

        result = evaluate(candles)

        self.assertEqual(Decimal("115"), result.active_collision("30M", "high").level)
        self.assertEqual(Decimal("65"), result.active_collision("30M", "low").level)
        self.assertFalse(result.signals)

    def test_str_13_later_close_beyond_collision_level_confirms_breakout(self) -> None:
        high_result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="115", low="90", close="100"),
                candle("09:50", high="118", low="99", close="116"),
            ]
        )
        low_result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="105", low="60", close="100"),
                candle("09:50", high="101", low="58", close="59"),
            ]
        )

        self.assertIn(
            ("30M", "collision_high"),
            {(signal.timeframe, signal.signal_type) for signal in high_result.signals},
        )
        self.assertIn(
            ("30M", "collision_low"),
            {(signal.timeframe, signal.signal_type) for signal in low_result.signals},
        )

    def test_str_14_wick_beyond_collision_level_does_not_confirm_collision(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="115", low="90", close="100"),
                candle("09:50", high="117", low="99", close="114"),
            ]
        )

        self.assertFalse(
            any(
                signal.timeframe == "30M" and signal.signal_type == "collision_high"
                for signal in result.signals
            )
        )

    def test_str_15_latest_collision_replaces_active_level_and_keeps_history(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="115", low="90", close="100"),
                candle("09:50", high="117", low="99", close="100"),
            ]
        )
        high_levels = [
            level
            for level in result.collisions
            if level.timeframe == "30M" and level.side == "high"
        ]

        self.assertEqual(2, len(high_levels))
        self.assertEqual(Decimal("117"), result.active_collision("30M", "high").level)
        self.assertEqual(1, sum(level.status == "superseded" for level in high_levels))
        self.assertEqual(1, sum(level.status == "active" for level in high_levels))

    def test_str_16_updating_one_collision_side_preserves_the_other(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="115", low="90", close="100"),
                candle("09:50", high="109", low="65", close="100"),
            ]
        )

        self.assertEqual(Decimal("115"), result.active_collision("30M", "high").level)
        self.assertEqual(Decimal("65"), result.active_collision("30M", "low").level)

    def test_str_17_one_candle_can_confirm_both_timeframes_separately(self) -> None:
        result = evaluate(
            opening_candles() + [candle("09:45", high="113", low="90", close="111")]
        )

        direct_highs = [
            signal
            for signal in result.signals
            if signal.signal_type == "direct_high"
        ]
        self.assertEqual({"15M", "30M"}, {signal.timeframe for signal in direct_highs})

    def test_str_18_one_candle_can_confirm_direct_and_collision_events(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="115", low="90", close="100"),
                candle("09:50", high="120", low="99", close="116"),
            ]
        )
        events = {
            signal.signal_type
            for signal in result.signals
            if signal.timeframe == "30M"
        }

        self.assertEqual({"direct_high", "collision_high"}, events)

    def test_str_19_consecutive_closes_beyond_unchanged_threshold_alert_once(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="113", low="90", close="111"),
                candle("09:50", high="114", low="100", close="112"),
            ]
        )

        for timeframe in ("15M", "30M"):
            matching = [
                signal
                for signal in result.signals
                if signal.timeframe == timeframe and signal.signal_type == "direct_high"
            ]
            self.assertEqual(1, len(matching))

    def test_str_20_close_back_through_threshold_rearms_direct_signal(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="107", low="90", close="105"),
                candle("09:50", high="102", low="90", close="100"),
                candle("09:55", high="103", low="95", close="101"),
            ]
        )
        fifteen_minute_breaks = [
            signal
            for signal in result.signals
            if signal.timeframe == "15M" and signal.signal_type == "direct_high"
        ]

        self.assertEqual(2, len(fifteen_minute_breaks))

    def test_str_21_later_opposite_direction_break_is_allowed(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="113", low="90", close="111"),
                candle("09:50", high="106", low="95", close="100"),
                candle("09:55", high="105", low="65", close="69"),
            ]
        )
        directions = {signal.direction for signal in result.signals}

        self.assertEqual({"bullish", "bearish"}, directions)

    def test_str_22_price_signal_does_not_require_open_interest(self) -> None:
        result = evaluate(
            opening_candles() + [candle("09:45", high="113", low="90", close="111")]
        )

        self.assertTrue(result.signals)

    def test_incomplete_candle_cannot_complete_opening_range(self) -> None:
        candles = opening_candles()
        candles[1] = candle("09:20", high="99", low="81", status=CandleStatus.INCOMPLETE)

        result = evaluate(candles)

        self.assertEqual("incomplete", result.ranges["15M"].status)
        self.assertFalse(result.signals)

    def test_stale_candle_blocks_later_signals_until_gap_is_reconciled(self) -> None:
        result = evaluate(
            opening_candles()
            + [
                candle("09:45", high="113", low="90", close="111", status=CandleStatus.STALE),
                candle("09:50", high="114", low="90", close="112"),
            ]
        )

        self.assertFalse(result.signals)
        self.assertTrue(any(issue.code == "CANDLE_GAP" for issue in result.issues))

    def test_out_of_order_candles_suppress_signal_evaluation(self) -> None:
        candles = opening_candles()
        candles[0], candles[1] = candles[1], candles[0]

        result = evaluate(candles + [candle("09:45", high="113", low="90", close="111")])

        self.assertFalse(result.signals)
        self.assertTrue(any(issue.code == "OUT_OF_ORDER" for issue in result.issues))

    def test_duplicate_interval_is_quarantined(self) -> None:
        candles = opening_candles()
        candles.insert(1, candles[0])

        result = evaluate(candles + [candle("09:45", high="113", low="90", close="111")])

        self.assertFalse(result.signals)
        self.assertTrue(any(issue.code == "DUPLICATE_INTERVAL" for issue in result.issues))

    def test_invalid_ohlc_candle_is_not_used(self) -> None:
        candles = opening_candles()
        candles[1] = candle("09:20", high="90", low="100", close="95")

        result = evaluate(candles)

        self.assertEqual("incomplete", result.ranges["15M"].status)
        self.assertTrue(any(issue.code == "INVALID_OHLC" for issue in result.issues))

    def test_naive_timestamp_is_rejected_instead_of_assuming_ist(self) -> None:
        candles = opening_candles()
        candles[0] = Candle(
            **{
                **candles[0].__dict__,
                "interval_start": candles[0].interval_start.replace(tzinfo=None),
            }
        )

        result = evaluate(candles)

        self.assertFalse(result.signals)
        self.assertTrue(any(issue.code == "TIMEZONE_MISSING" for issue in result.issues))

    def test_wrong_interval_length_is_not_used(self) -> None:
        candles = opening_candles()
        candles[0] = Candle(
            **{
                **candles[0].__dict__,
                "interval_end": candles[0].interval_end + timedelta(minutes=1),
            }
        )

        result = evaluate(candles)

        self.assertEqual("incomplete", result.ranges["15M"].status)
        self.assertTrue(any(issue.code == "INVALID_INTERVAL" for issue in result.issues))


if __name__ == "__main__":
    unittest.main()

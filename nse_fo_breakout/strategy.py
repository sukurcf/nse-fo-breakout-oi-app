from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
STRATEGY_VERSION = "1.0"
FIVE_MINUTES = timedelta(minutes=5)


class CandleStatus(str, Enum):
    COMPLETE = "complete"
    CORRECTED = "corrected"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True)
class Candle:
    symbol: str
    provider_instrument_id: str
    interval_start: datetime
    interval_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    provider_time: datetime | None = None
    received_at: datetime | None = None
    source: str = "manual_import"
    status: CandleStatus = CandleStatus.COMPLETE
    version_id: str | None = None


@dataclass(frozen=True)
class DataIssue:
    code: str
    message: str
    symbol: str
    interval_start: datetime | None = None
    timeframe: str | None = None


@dataclass(frozen=True)
class OpeningRange:
    range_id: str
    timeframe: str
    high: Decimal | None
    low: Decimal | None
    status: str
    freeze_at: datetime
    candle_ids: tuple[str, ...]
    missing_intervals: tuple[datetime, ...]
    reason: str | None = None


@dataclass(frozen=True)
class CollisionLevel:
    collision_id: str
    timeframe: str
    side: str
    level: Decimal
    forming_candle_id: str
    formed_at: datetime
    status: str
    replaced_by: str | None = None


@dataclass(frozen=True)
class Signal:
    event_id: str
    trading_date: date
    symbol: str
    timeframe: str
    signal_type: str
    direction: str
    candle_id: str
    candle_start: datetime
    candle_end: datetime
    close: Decimal
    level: Decimal
    range_id: str
    range_high: Decimal
    range_low: Decimal
    collision_id: str | None
    strategy_version: str

    @property
    def label(self) -> str:
        if self.signal_type.startswith("collision_"):
            side = self.signal_type.removeprefix("collision_").upper()
            return f"{self.timeframe} COLLISION {side} BREAK"
        side = self.signal_type.removeprefix("direct_").upper()
        return f"{self.timeframe} {side} BREAK"


@dataclass(frozen=True)
class Evaluation:
    ranges: Mapping[str, OpeningRange]
    collisions: tuple[CollisionLevel, ...]
    signals: tuple[Signal, ...]
    issues: tuple[DataIssue, ...]

    def active_collision(self, timeframe: str, side: str) -> CollisionLevel | None:
        return next(
            (
                level
                for level in reversed(self.collisions)
                if level.timeframe == timeframe
                and level.side == side
                and level.status == "active"
            ),
            None,
        )


@dataclass(frozen=True)
class _RangeSpec:
    timeframe: str
    first_start: time
    freeze_time: time
    candle_count: int


_RANGE_SPECS = (
    _RangeSpec("15M", time(9, 15), time(9, 30), 3),
    _RangeSpec("30M", time(9, 15), time(9, 45), 6),
)


def _aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def candle_identity(candle: Candle) -> str:
    if candle.version_id:
        return candle.version_id
    fields = (
        candle.symbol,
        candle.provider_instrument_id,
        candle.interval_start.isoformat(),
        candle.interval_end.isoformat(),
        _decimal_text(candle.open),
        _decimal_text(candle.high),
        _decimal_text(candle.low),
        _decimal_text(candle.close),
        "" if candle.volume is None else _decimal_text(candle.volume),
        "" if candle.provider_time is None else candle.provider_time.isoformat(),
        candle.source,
        candle.status.value,
    )
    return _digest(*fields)


def _validate_candle(
    candle: Candle,
    *,
    trading_date: date,
    symbol: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    if not _aware(candle.interval_start) or not _aware(candle.interval_end):
        return ("TIMEZONE_MISSING",)

    local_start = candle.interval_start.astimezone(IST)
    local_end = candle.interval_end.astimezone(IST)
    if candle.symbol != symbol or local_start.date() != trading_date:
        issues.append("IDENTITY_MISMATCH")
    if not candle.provider_instrument_id.strip():
        issues.append("INSTRUMENT_MAPPING_MISSING")
    if local_end - local_start != FIVE_MINUTES:
        issues.append("INVALID_INTERVAL")
    if (
        local_start.second != 0
        or local_start.microsecond != 0
        or local_start.minute % 5 != 0
        or local_start.time() < time(9, 15)
        or local_end.time() > time(15, 30)
        or local_end.date() != local_start.date()
    ):
        issues.append("OUTSIDE_REGULAR_SESSION")
    if candle.status not in (CandleStatus.COMPLETE, CandleStatus.CORRECTED):
        issues.append(f"CANDLE_{candle.status.value.upper()}")

    prices = (candle.open, candle.high, candle.low, candle.close)
    if any(not value.is_finite() or value <= 0 for value in prices):
        issues.append("INVALID_OHLC")
    elif (
        candle.low > candle.high
        or candle.open < candle.low
        or candle.open > candle.high
        or candle.close < candle.low
        or candle.close > candle.high
    ):
        issues.append("INVALID_OHLC")
    if candle.volume is not None and (
        not candle.volume.is_finite() or candle.volume < 0
    ):
        issues.append("INVALID_VOLUME")
    if candle.provider_time is not None and not _aware(candle.provider_time):
        issues.append("TIMEZONE_MISSING")
    if candle.received_at is not None and not _aware(candle.received_at):
        issues.append("TIMEZONE_MISSING")
    return tuple(dict.fromkeys(issues))


def _expected_starts(trading_date: date, spec: _RangeSpec) -> tuple[datetime, ...]:
    first = datetime.combine(trading_date, spec.first_start, IST)
    return tuple(first + FIVE_MINUTES * index for index in range(spec.candle_count))


def _make_range(
    spec: _RangeSpec,
    *,
    trading_date: date,
    symbol: str,
    valid_candles: Mapping[datetime, Candle],
) -> OpeningRange:
    starts = _expected_starts(trading_date, spec)
    opening_candles = tuple(valid_candles[start] for start in starts if start in valid_candles)
    missing = tuple(start for start in starts if start not in valid_candles)
    freeze_at = datetime.combine(trading_date, spec.freeze_time, IST)
    if missing:
        candle_ids = tuple(candle_identity(item) for item in opening_candles)
        range_id = _digest(
            trading_date.isoformat(),
            symbol,
            spec.timeframe,
            "incomplete",
            *(start.isoformat() for start in missing),
            *candle_ids,
        )
        return OpeningRange(
            range_id=range_id,
            timeframe=spec.timeframe,
            high=None,
            low=None,
            status="incomplete",
            freeze_at=freeze_at,
            candle_ids=candle_ids,
            missing_intervals=missing,
            reason="One or more required opening candles are missing or invalid.",
        )

    high = max(item.high for item in opening_candles)
    low = min(item.low for item in opening_candles)
    candle_ids = tuple(candle_identity(item) for item in opening_candles)
    range_id = _digest(
        trading_date.isoformat(),
        symbol,
        spec.timeframe,
        _decimal_text(high),
        _decimal_text(low),
        *candle_ids,
    )
    return OpeningRange(
        range_id=range_id,
        timeframe=spec.timeframe,
        high=high,
        low=low,
        status="complete",
        freeze_at=freeze_at,
        candle_ids=candle_ids,
        missing_intervals=(),
    )


def _threshold_triggered(
    close: Decimal,
    level: Decimal,
    side: str,
    key: tuple[str, str],
    armed: dict[tuple[str, str], bool],
) -> bool:
    if side == "high":
        if close <= level:
            armed[key] = True
            return False
    elif close >= level:
        armed[key] = True
        return False

    if armed.get(key, True):
        armed[key] = False
        return True
    return False


def _signal(
    *,
    trading_date: date,
    symbol: str,
    timeframe: str,
    signal_type: str,
    candle: Candle,
    opening: OpeningRange,
    level: Decimal,
    collision_id: str | None,
) -> Signal:
    candle_id = candle_identity(candle)
    event_id = _digest(
        trading_date.isoformat(),
        symbol,
        timeframe,
        signal_type,
        candle_id,
        opening.range_id,
        _decimal_text(level),
        collision_id or "",
        STRATEGY_VERSION,
    )
    return Signal(
        event_id=event_id,
        trading_date=trading_date,
        symbol=symbol,
        timeframe=timeframe,
        signal_type=signal_type,
        direction="bullish" if signal_type.endswith("high") else "bearish",
        candle_id=candle_id,
        candle_start=candle.interval_start.astimezone(IST),
        candle_end=candle.interval_end.astimezone(IST),
        close=candle.close,
        level=level,
        range_id=opening.range_id,
        range_high=opening.high or Decimal("0"),
        range_low=opening.low or Decimal("0"),
        collision_id=collision_id,
        strategy_version=STRATEGY_VERSION,
    )


def _evaluate_timeframe(
    spec: _RangeSpec,
    opening: OpeningRange,
    *,
    candles: Sequence[Candle],
    valid_by_start: Mapping[datetime, Candle],
    trading_date: date,
    symbol: str,
) -> tuple[list[CollisionLevel], list[Signal], list[DataIssue]]:
    if opening.status != "complete" or opening.high is None or opening.low is None:
        return [], [], []

    history: list[CollisionLevel] = []
    active: dict[str, CollisionLevel] = {}
    signals: list[Signal] = []
    issues: list[DataIssue] = []
    armed: dict[tuple[str, str], bool] = {}
    expected_start = opening.freeze_at
    blocked = False

    def add_collision(side: str, level_value: Decimal, forming: Candle) -> None:
        previous = active.get(side)
        collision_id = _digest(
            trading_date.isoformat(),
            symbol,
            spec.timeframe,
            side,
            _decimal_text(level_value),
            candle_identity(forming),
        )
        if previous is not None:
            for index, item in enumerate(history):
                if item.collision_id == previous.collision_id:
                    history[index] = CollisionLevel(
                        collision_id=item.collision_id,
                        timeframe=item.timeframe,
                        side=item.side,
                        level=item.level,
                        forming_candle_id=item.forming_candle_id,
                        formed_at=item.formed_at,
                        status="superseded",
                        replaced_by=collision_id,
                    )
                    break
        collision = CollisionLevel(
            collision_id=collision_id,
            timeframe=spec.timeframe,
            side=side,
            level=level_value,
            forming_candle_id=candle_identity(forming),
            formed_at=forming.interval_end.astimezone(IST),
            status="active",
        )
        history.append(collision)
        active[side] = collision
        armed[(f"collision_{side}", _decimal_text(level_value))] = True

    for item in candles:
        local_start = item.interval_start.astimezone(IST)
        if local_start < opening.freeze_at:
            continue
        while expected_start < local_start:
            if expected_start not in valid_by_start:
                issues.append(
                    DataIssue(
                        code="CANDLE_GAP",
                        message="A missing, stale, incomplete, or invalid candle prevents reliable replay.",
                        symbol=symbol,
                        interval_start=expected_start,
                        timeframe=spec.timeframe,
                    )
                )
                blocked = True
                break
            expected_start += FIVE_MINUTES
        if blocked:
            break
        if local_start != expected_start or local_start not in valid_by_start:
            continue

        direct_high_key = ("direct_high", _decimal_text(opening.high))
        if _threshold_triggered(item.close, opening.high, "high", direct_high_key, armed):
            signals.append(
                _signal(
                    trading_date=trading_date,
                    symbol=symbol,
                    timeframe=spec.timeframe,
                    signal_type="direct_high",
                    candle=item,
                    opening=opening,
                    level=opening.high,
                    collision_id=None,
                )
            )

        direct_low_key = ("direct_low", _decimal_text(opening.low))
        if _threshold_triggered(item.close, opening.low, "low", direct_low_key, armed):
            signals.append(
                _signal(
                    trading_date=trading_date,
                    symbol=symbol,
                    timeframe=spec.timeframe,
                    signal_type="direct_low",
                    candle=item,
                    opening=opening,
                    level=opening.low,
                    collision_id=None,
                )
            )

        for side in ("high", "low"):
            # Collision breakouts inspect levels from prior candles; new levels form below.
            collision = active.get(side)
            if collision is None:
                continue
            signal_type = f"collision_{side}"
            key = (signal_type, _decimal_text(collision.level))
            if _threshold_triggered(item.close, collision.level, side, key, armed):
                signals.append(
                    _signal(
                        trading_date=trading_date,
                        symbol=symbol,
                        timeframe=spec.timeframe,
                        signal_type=signal_type,
                        candle=item,
                        opening=opening,
                        level=collision.level,
                        collision_id=collision.collision_id,
                    )
                )

        strictly_inside = opening.low < item.close < opening.high
        if strictly_inside and item.high > opening.high:
            add_collision("high", max(opening.high, item.high), item)
        if strictly_inside and item.low < opening.low:
            add_collision("low", min(opening.low, item.low), item)
        expected_start = local_start + FIVE_MINUTES

    return history, signals, issues


def evaluate_session(
    candles: Sequence[Candle],
    *,
    trading_date: date,
    symbol: str,
) -> Evaluation:
    """Replay validated candles; callers must surface issues before trusting the result."""
    issues: list[DataIssue] = []
    normalized: list[tuple[datetime, Candle]] = []
    hard_suppress = False

    for item in candles:
        if not _aware(item.interval_start) or not _aware(item.interval_end):
            issues.append(
                DataIssue(
                    code="TIMEZONE_MISSING",
                    message="Candle timestamps must include a timezone.",
                    symbol=symbol,
                    interval_start=item.interval_start,
                )
            )
            hard_suppress = True
            continue
        local_start = item.interval_start.astimezone(IST)
        item_issues = _validate_candle(item, trading_date=trading_date, symbol=symbol)
        if item_issues:
            for code in item_issues:
                issues.append(
                    DataIssue(
                        code=code,
                        message=f"Candle failed validation: {code.lower().replace('_', ' ')}.",
                        symbol=symbol,
                        interval_start=local_start,
                    )
                )
            if "IDENTITY_MISMATCH" in item_issues:
                hard_suppress = True
            continue
        normalized.append((local_start, item))

    input_starts = [start for start, _ in normalized]
    if any(current < previous for previous, current in zip(input_starts, input_starts[1:])):
        issues.append(
            DataIssue(
                code="OUT_OF_ORDER",
                message="Out-of-order candles are quarantined until reconciliation.",
                symbol=symbol,
            )
        )
        hard_suppress = True

    counts: dict[datetime, int] = {}
    for start in input_starts:
        counts[start] = counts.get(start, 0) + 1
    duplicates = {start for start, count in counts.items() if count > 1}
    if duplicates:
        for start in sorted(duplicates):
            issues.append(
                DataIssue(
                    code="DUPLICATE_INTERVAL",
                    message="Duplicate candle intervals are quarantined until reconciliation.",
                    symbol=symbol,
                    interval_start=start,
                )
            )
        hard_suppress = True

    valid_by_start: dict[datetime, Candle] = {}
    if not hard_suppress:
        for start, item in normalized:
            valid_by_start[start] = item

    ranges = {
        spec.timeframe: _make_range(
            spec,
            trading_date=trading_date,
            symbol=symbol,
            valid_candles=valid_by_start,
        )
        for spec in _RANGE_SPECS
    }
    for opening in ranges.values():
        for missing in opening.missing_intervals:
            issues.append(
                DataIssue(
                    code="MISSING_RANGE_CANDLE",
                    message=f"{opening.timeframe} opening range is incomplete.",
                    symbol=symbol,
                    interval_start=missing,
                    timeframe=opening.timeframe,
                )
            )

    ordered_valid = sorted(valid_by_start.values(), key=lambda item: item.interval_start)
    collisions: list[CollisionLevel] = []
    signals: list[Signal] = []
    for spec in _RANGE_SPECS:
        frame_collisions, frame_signals, frame_issues = _evaluate_timeframe(
            spec,
            ranges[spec.timeframe],
            candles=ordered_valid,
            valid_by_start=valid_by_start,
            trading_date=trading_date,
            symbol=symbol,
        )
        collisions.extend(frame_collisions)
        signals.extend(frame_signals)
        issues.extend(frame_issues)

    signals.sort(key=lambda signal: (signal.candle_start, signal.timeframe, signal.signal_type))
    return Evaluation(
        ranges=ranges,
        collisions=tuple(collisions),
        signals=tuple(signals),
        issues=tuple(issues),
    )


def parse_decimal(value: str, *, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be a valid decimal.") from error
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite.")
    return parsed

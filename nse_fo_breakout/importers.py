from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from nse_fo_breakout.strategy import Candle, CandleStatus, IST, parse_decimal


MAX_IMPORT_ROWS = 50_000
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,31}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_. -]{1,128}$")
CANDLE_HEADERS = {
    "exchange",
    "symbol",
    "provider_instrument_id",
    "interval_start",
    "interval_end",
    "open",
    "high",
    "low",
    "close",
    "status",
    "provider_time",
    "source",
}


class ImportFormatError(ValueError):
    pass


def normalize_symbol(raw_symbol: str) -> str:
    symbol = raw_symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ImportFormatError("Symbol must be a normalized NSE stock symbol.")
    return symbol


def parse_aware_datetime(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ImportFormatError(f"{field} must be an ISO 8601 timestamp.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ImportFormatError(f"{field} must include an explicit timezone offset.")
    return parsed


def _parse_status(value: str, *, row_number: int) -> CandleStatus:
    try:
        return CandleStatus(value.strip().lower())
    except ValueError as error:
        accepted = ", ".join(status.value for status in CandleStatus)
        raise ImportFormatError(
            f"Row {row_number}: status must be one of {accepted}."
        ) from error


def parse_candle_csv(text: str, *, trading_date: date) -> list[Candle]:
    if not text.strip():
        raise ImportFormatError("The candle CSV is empty.")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ImportFormatError("The candle CSV must include a header row.")

    headers = [header.strip().lower() for header in reader.fieldnames]
    if len(set(headers)) != len(headers):
        raise ImportFormatError("The candle CSV contains duplicate column names.")
    missing = sorted(CANDLE_HEADERS - set(headers))
    if missing:
        raise ImportFormatError(f"Missing required CSV columns: {', '.join(missing)}.")

    result: list[Candle] = []
    previous_by_symbol: dict[str, datetime] = {}
    seen_slots: set[tuple[str, datetime]] = set()
    for row_number, raw_row in enumerate(reader, start=2):
        if row_number - 1 > MAX_IMPORT_ROWS:
            raise ImportFormatError(f"Import exceeds the {MAX_IMPORT_ROWS:,}-row limit.")
        if None in raw_row:
            raise ImportFormatError(
                f"Row {row_number}: contains more values than the CSV header."
            )
        row = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in raw_row.items()
        }
        try:
            if row.get("exchange", "").upper() != "NSE":
                raise ImportFormatError("exchange must be NSE.")
            symbol = normalize_symbol(row.get("symbol", ""))
            instrument_id = row.get("provider_instrument_id", "")
            if not IDENTIFIER_PATTERN.fullmatch(instrument_id):
                raise ImportFormatError("provider_instrument_id must be a safe source identifier.")
            interval_start = parse_aware_datetime(
                row.get("interval_start", ""), field="interval_start"
            )
            interval_end = parse_aware_datetime(
                row.get("interval_end", ""), field="interval_end"
            )
            provider_time = parse_aware_datetime(
                row.get("provider_time", ""), field="provider_time"
            )
            if provider_time.astimezone(IST).date() != trading_date:
                raise ImportFormatError("provider_time must fall on trading_date.")
            if interval_start.astimezone(IST).date() != trading_date:
                raise ImportFormatError("Candle date does not match trading_date.")
            if interval_end.astimezone(IST).date() != trading_date:
                raise ImportFormatError("Candle end date does not match trading_date.")
            start_key = interval_start.astimezone(IST)
            previous = previous_by_symbol.get(symbol)
            if previous is not None and start_key <= previous:
                raise ImportFormatError(
                    "Rows must be strictly chronological for each symbol; "
                    "duplicates and out-of-order candles are quarantined."
                )
            if (symbol, start_key) in seen_slots:
                raise ImportFormatError("The CSV contains a duplicate symbol interval.")
            previous_by_symbol[symbol] = start_key
            seen_slots.add((symbol, start_key))

            volume_value = row.get("volume", "")
            volume = (
                parse_decimal(volume_value, field="volume")
                if volume_value
                else None
            )
            source = row.get("source", "")
            if not SOURCE_PATTERN.fullmatch(source):
                raise ImportFormatError("source must be a short, safe provider label.")
            result.append(
                Candle(
                    symbol=symbol,
                    provider_instrument_id=instrument_id,
                    interval_start=interval_start,
                    interval_end=interval_end,
                    open=parse_decimal(row.get("open", ""), field="open"),
                    high=parse_decimal(row.get("high", ""), field="high"),
                    low=parse_decimal(row.get("low", ""), field="low"),
                    close=parse_decimal(row.get("close", ""), field="close"),
                    volume=volume,
                    provider_time=provider_time,
                    source=source,
                    status=_parse_status(row.get("status", ""), row_number=row_number),
                )
            )
        except ImportFormatError as error:
            raise ImportFormatError(f"Row {row_number}: {error}") from error
        except ValueError as error:
            raise ImportFormatError(f"Row {row_number}: {error}") from error

    if not result:
        raise ImportFormatError("The candle CSV has no data rows.")
    return result


def normalize_symbol_list(raw_values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw_symbol in raw_values:
        symbol = normalize_symbol(raw_symbol)
        if symbol not in normalized:
            normalized.append(symbol)
    return normalized

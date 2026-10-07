from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from nse_fo_breakout.importers import (
    IDENTIFIER_PATTERN,
    ImportFormatError,
    MAX_IMPORT_ROWS,
    SOURCE_PATTERN,
    normalize_symbol,
    parse_aware_datetime,
)
from nse_fo_breakout.strategy import IST, parse_decimal


OI_HEADERS = {
    "exchange",
    "symbol",
    "provider_instrument_id",
    "contract_type",
    "expiry",
    "strike",
    "option_type",
    "open_interest",
    "volume",
    "ltp",
    "provider_time",
    "source",
    "status",
}


@dataclass(frozen=True)
class OIObservation:
    symbol: str
    provider_instrument_id: str
    contract_type: str
    expiry: date
    strike: Decimal | None
    option_type: str | None
    open_interest: Decimal
    oi_change: Decimal | None
    volume: Decimal
    ltp: Decimal
    provider_time: datetime
    source: str
    status: str


def parse_oi_csv(text: str, *, trading_date: date) -> list[OIObservation]:
    if not text.strip():
        raise ImportFormatError("The OI CSV is empty.")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ImportFormatError("The OI CSV must include a header row.")
    headers = [header.strip().lower() for header in reader.fieldnames]
    if len(set(headers)) != len(headers):
        raise ImportFormatError("The OI CSV contains duplicate column names.")
    missing = sorted(OI_HEADERS - set(headers))
    if missing:
        raise ImportFormatError(f"Missing required CSV columns: {', '.join(missing)}.")

    result: list[OIObservation] = []
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
            contract_type = row.get("contract_type", "").lower()
            if contract_type not in {"future", "option"}:
                raise ImportFormatError("contract_type must be future or option.")
            try:
                expiry = date.fromisoformat(row.get("expiry", ""))
            except ValueError as error:
                raise ImportFormatError("expiry must be an ISO 8601 date.") from error
            strike = (
                parse_decimal(row["strike"], field="strike")
                if row.get("strike")
                else None
            )
            option_type_value = row.get("option_type", "").upper()
            option_type = option_type_value or None
            if contract_type == "option":
                if strike is None or strike <= 0:
                    raise ImportFormatError("Options require a positive strike.")
                if option_type not in {"CALL", "PUT"}:
                    raise ImportFormatError("Options require option_type CALL or PUT.")
            elif strike is not None or option_type is not None:
                raise ImportFormatError("Futures must not specify strike or option_type.")
            provider_time = parse_aware_datetime(
                row.get("provider_time", ""), field="provider_time"
            )
            if provider_time.astimezone(IST).date() != trading_date:
                raise ImportFormatError("OI observation date does not match trading_date.")
            oi_change = (
                parse_decimal(row["oi_change"], field="oi_change")
                if row.get("oi_change")
                else None
            )
            open_interest = parse_decimal(
                row.get("open_interest", ""), field="open_interest"
            )
            volume = parse_decimal(row.get("volume", ""), field="volume")
            ltp = parse_decimal(row.get("ltp", ""), field="ltp")
            if open_interest < 0 or volume < 0 or ltp < 0:
                raise ImportFormatError("Open interest, volume, and LTP cannot be negative.")
            source = row.get("source", "")
            if not SOURCE_PATTERN.fullmatch(source):
                raise ImportFormatError("source must be a short, safe provider label.")
            status = row.get("status", "").lower()
            if status not in {"complete", "stale", "illiquid", "invalid"}:
                raise ImportFormatError(
                    "status must be complete, stale, illiquid, or invalid."
                )
            result.append(
                OIObservation(
                    symbol=symbol,
                    provider_instrument_id=instrument_id,
                    contract_type=contract_type,
                    expiry=expiry,
                    strike=strike,
                    option_type=option_type,
                    open_interest=open_interest,
                    oi_change=oi_change,
                    volume=volume,
                    ltp=ltp,
                    provider_time=provider_time,
                    source=source,
                    status=status,
                )
            )
        except ImportFormatError as error:
            raise ImportFormatError(f"Row {row_number}: {error}") from error
        except ValueError as error:
            raise ImportFormatError(f"Row {row_number}: {error}") from error
    if not result:
        raise ImportFormatError("The OI CSV has no data rows.")
    return result

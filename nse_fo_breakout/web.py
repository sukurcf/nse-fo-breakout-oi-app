from __future__ import annotations

import csv
import ipaddress
import io
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, AsyncIterator, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from nse_fo_breakout.importers import ImportFormatError, normalize_symbol
from nse_fo_breakout.service import ReplayService
from nse_fo_breakout.storage import MappingError, Store
from nse_fo_breakout.strategy import IST


MAX_CSV_BYTES = 5_000_000
STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MappingRequest(StrictRequest):
    trading_date: date
    symbol: str = Field(min_length=1, max_length=32)
    provider_instrument_id: str = Field(min_length=1, max_length=128)
    confirm_fno_eligibility: StrictBool


class BeaconImportRequest(StrictRequest):
    trading_date: date
    bullish_symbols: list[str] = Field(default_factory=list, max_length=2000)
    bearish_symbols: list[str] = Field(default_factory=list, max_length=2000)
    source_name: str = Field(
        default="manual_import",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_. -]+$",
    )
    provider_time: datetime | None = None


class PauseRequest(StrictRequest):
    trading_date: date
    paused: StrictBool


class ExclusionRequest(StrictRequest):
    trading_date: date
    symbol: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=240)


def _default_db_path() -> Path:
    configured = os.environ.get("NSE_BREAKOUT_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "data" / "nse-breakout.sqlite3"


def _resolve_date(trading_date: date | None) -> date:
    return trading_date or datetime.now(IST).date()


async def _read_limited_csv(request: Request) -> str:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"text/csv", "application/csv"}:
        raise HTTPException(status_code=415, detail="Upload normalized data as text/csv.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV upload exceeds the 5 MB limit.")
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail="CSV must use UTF-8 encoding.",
        ) from error


def _csv_cell(value: object) -> str:
    rendered = "" if value is None else str(value)
    if rendered.startswith(("\t", "\r", "\n")):
        return "'" + rendered
    stripped = rendered.lstrip(" \t\r\n")
    if stripped.startswith(("=", "+", "@")):
        return "'" + rendered
    if stripped.startswith("-"):
        try:
            Decimal(stripped)
        except InvalidOperation:
            return "'" + rendered
    return rendered


def create_app(*, db_path: str | Path | None = None) -> FastAPI:
    resolved_db_path = Path(db_path).expanduser() if db_path is not None else _default_db_path()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        store = Store(resolved_db_path)
        service = ReplayService(store)
        service.restore_pending_replays()
        application.state.service = service
        try:
            yield
        finally:
            store.close()

    application = FastAPI(
        title="NSE F&O Breakout Monitor",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def local_network_boundary(request: Request, call_next):
        def denied() -> JSONResponse:
            return JSONResponse(
                status_code=403,
                content={"detail": "This private dashboard accepts same-origin loopback requests only."},
            )

        client = request.client
        is_loopback = False
        if client is not None:
            try:
                is_loopback = ipaddress.ip_address(client.host).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback:
            return denied()

        host_header = request.headers.get("host", "")
        try:
            host = urlsplit(f"//{host_header}")
            hostname = (host.hostname or "").lower()
            server = request.scope.get("server")
            server_port = server[1] if server is not None else None
            host_port = host.port or (443 if request.url.scheme == "https" else 80)
        except ValueError:
            return denied()
        if (
            host.username is not None
            or host.password is not None
            or hostname not in {"127.0.0.1", "localhost"}
            or (server_port is not None and host_port != server_port)
        ):
            return denied()

        origin = request.headers.get("origin")
        if origin is not None:
            try:
                origin_parts = urlsplit(origin)
                origin_hostname = (origin_parts.hostname or "").lower()
                origin_port = origin_parts.port or (
                    443 if origin_parts.scheme == "https" else 80
                )
            except ValueError:
                return denied()
            if (
                origin_parts.scheme != request.url.scheme
                or origin_hostname != hostname
                or origin_port != host_port
                or origin_parts.username is not None
                or origin_parts.password is not None
                or origin_parts.path
                or origin_parts.query
                or origin_parts.fragment
            ):
                return denied()

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                return denied()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(ImportFormatError)
    async def import_error_handler(
        _request: Request, error: ImportFormatError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @application.exception_handler(MappingError)
    async def mapping_error_handler(
        _request: Request, error: MappingError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @application.get("/", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(INDEX_FILE, media_type="text/html")

    @application.get("/api/status")
    async def status(
        request: Request,
        trading_date: date | None = Query(default=None),
    ) -> dict:
        current_date = _resolve_date(trading_date)
        return request.app.state.service.get_status(trading_date=current_date)

    @application.get("/api/watchlist")
    async def watchlist(
        request: Request,
        trading_date: date | None = Query(default=None),
    ) -> dict:
        current_date = _resolve_date(trading_date)
        service: ReplayService = request.app.state.service
        return {
            "trading_date": current_date.isoformat(),
            "symbols": service.get_watchlist(trading_date=current_date),
            "unmapped_symbols": service.store.get_unmapped_beacon_symbols(
                trading_date=current_date
            ),
        }

    @application.get("/api/signals")
    async def signals(
        request: Request,
        trading_date: date | None = Query(default=None),
        symbol: str | None = Query(default=None, min_length=1, max_length=32),
        direction: Literal["bullish", "bearish"] | None = None,
        timeframe: Literal["15M", "30M"] | None = None,
        signal_type: Literal[
            "direct_high",
            "direct_low",
            "collision_high",
            "collision_low",
        ]
        | None = None,
        oi_status: Literal[
            "unavailable",
            "raw_only_uncalibrated",
            "stale",
            "illiquid",
        ]
        | None = None,
    ) -> dict:
        current_date = _resolve_date(trading_date)
        current_symbol = normalize_symbol(symbol) if symbol is not None else None
        all_signals = request.app.state.service.get_signals(trading_date=current_date)
        filtered = [
            item
            for item in all_signals
            if (current_symbol is None or item["symbol"] == current_symbol)
            and (direction is None or item["direction"] == direction)
            and (timeframe is None or item["timeframe"] == timeframe)
            and (signal_type is None or item["signal_type"] == signal_type)
            and (
                oi_status is None
                or item["oi_context_status"] == oi_status
            )
        ]
        return {
            "trading_date": current_date.isoformat(),
            "total": len(filtered),
            "signals": filtered,
        }

    @application.get("/api/history")
    async def history(
        request: Request,
        trading_date: date | None = Query(default=None),
        symbol: str | None = Query(default=None, min_length=1, max_length=32),
    ) -> dict:
        current_date = _resolve_date(trading_date)
        events = request.app.state.service.get_history(
            trading_date=current_date,
            symbol=symbol,
        )
        return {"trading_date": current_date.isoformat(), "total": len(events), "events": events}

    @application.get("/api/oi")
    async def oi_context(
        request: Request,
        trading_date: date | None = Query(default=None),
        symbol: str | None = Query(default=None, min_length=1, max_length=32),
    ) -> dict:
        current_date = _resolve_date(trading_date)
        observations = request.app.state.service.get_oi_context(
            trading_date=current_date,
            symbol=symbol,
        )
        return {
            "trading_date": current_date.isoformat(),
            "status": (
                request.app.state.service.oi_context_status(observations)
                if observations
                else "not_configured"
            ),
            "oi_score": None,
            "observations": observations,
        }

    @application.get("/api/audit")
    async def audit(
        request: Request,
        trading_date: date | None = Query(default=None),
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict:
        current_date = _resolve_date(trading_date)
        events = request.app.state.service.get_audit_events(
            trading_date=current_date,
            limit=limit,
        )
        return {"trading_date": current_date.isoformat(), "events": events}

    @application.get("/api/notifications")
    async def notifications(
        request: Request,
        trading_date: date | None = Query(default=None),
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict:
        current_date = _resolve_date(trading_date)
        items = request.app.state.service.get_notifications(
            trading_date=current_date,
            limit=limit,
        )
        return {
            "trading_date": current_date.isoformat(),
            "notifications": items,
            "external_delivery": "disabled",
        }

    @application.post("/api/mappings")
    async def add_mapping(payload: MappingRequest, request: Request) -> dict:
        return request.app.state.service.add_watchlist_mapping(
            trading_date=payload.trading_date,
            symbol=payload.symbol,
            provider_instrument_id=payload.provider_instrument_id,
            fno_eligible_confirmed=payload.confirm_fno_eligibility,
        )

    @application.post("/api/watchlist/exclude")
    async def exclude_symbol(payload: ExclusionRequest, request: Request) -> dict:
        request.app.state.service.exclude_symbol(
            trading_date=payload.trading_date,
            symbol=payload.symbol,
            reason=payload.reason,
        )
        return {
            "trading_date": payload.trading_date.isoformat(),
            "symbol": normalize_symbol(payload.symbol),
            "excluded": True,
            "reason": payload.reason.strip(),
        }

    @application.post("/api/beacon/import")
    async def import_beacon(payload: BeaconImportRequest, request: Request) -> dict:
        return request.app.state.service.import_beacon(
            trading_date=payload.trading_date,
            bullish_symbols=payload.bullish_symbols,
            bearish_symbols=payload.bearish_symbols,
            source_name=payload.source_name,
            provider_time=payload.provider_time,
        )

    @application.post("/api/candles/import")
    async def import_candles(
        request: Request,
        trading_date: date = Query(...),
    ) -> dict:
        csv_text = await _read_limited_csv(request)
        return request.app.state.service.import_candle_csv(
            trading_date=trading_date,
            csv_text=csv_text,
        )

    @application.post("/api/oi/import")
    async def import_oi(
        request: Request,
        trading_date: date = Query(...),
    ) -> dict:
        csv_text = await _read_limited_csv(request)
        return request.app.state.service.import_oi_csv(
            trading_date=trading_date,
            csv_text=csv_text,
        )

    @application.post("/api/alerts")
    async def set_alerts(payload: PauseRequest, request: Request) -> dict:
        request.app.state.service.set_alerts_paused(
            trading_date=payload.trading_date,
            paused=payload.paused,
        )
        return {
            "trading_date": payload.trading_date.isoformat(),
            "alerts_paused": payload.paused,
            "delivery_mode": "replay_only_no_external_alerts",
        }

    @application.get("/api/export/signals.csv")
    async def export_signals(
        request: Request,
        trading_date: date | None = Query(default=None),
    ) -> Response:
        current_date = _resolve_date(trading_date)
        events = request.app.state.service.get_history(trading_date=current_date)
        headers = [
            "event_id",
            "trading_date",
            "symbol",
            "timeframe",
            "signal_type",
            "direction",
            "candle_start_ist",
            "candle_end_ist",
            "close",
            "level",
            "range_high",
            "range_low",
            "candle_source",
            "provider_time_ist",
            "import_time_ist",
            "oi_context_status",
            "status",
            "strategy_version",
        ]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        for event in events:
            values = dict(event)
            for source, target in (
                ("candle_start", "candle_start_ist"),
                ("candle_end", "candle_end_ist"),
                ("candle_provider_time", "provider_time_ist"),
                ("candle_received_at", "import_time_ist"),
            ):
                value = values.get(source)
                values[target] = (
                    datetime.fromisoformat(value).astimezone(IST).isoformat()
                    if value
                    else None
                )
            writer.writerow([_csv_cell(values.get(header)) for header in headers])
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="nse-breakout-signals-{current_date.isoformat()}.csv"'
                )
            },
        )

    @application.get("/api/export/backup.sqlite")
    async def export_backup(request: Request) -> Response:
        backup = request.app.state.service.export_database_backup()
        return Response(
            content=backup,
            media_type="application/vnd.sqlite3",
            headers={
                "Content-Disposition": 'attachment; filename="nse-breakout-local-backup.sqlite3"',
                "Cache-Control": "no-store",
            },
        )

    application.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR, check_dir=True),
        name="static",
    )
    return application


app = create_app()

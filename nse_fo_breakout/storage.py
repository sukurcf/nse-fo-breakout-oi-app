from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator, Sequence
from uuid import uuid4

from nse_fo_breakout.oi import OIObservation
from nse_fo_breakout.strategy import (
    Candle,
    CandleStatus,
    CollisionLevel,
    Evaluation,
    OpeningRange,
    Signal,
    STRATEGY_VERSION,
    candle_identity,
)


class MappingError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A timezone-aware timestamp is required.")
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _audit(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    trading_date: date | str | None,
    symbol: str | None,
    details: dict[str, Any],
    correlation_id: str | None = None,
    occurred_at: datetime | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events (
            event_id, occurred_at, trading_date, symbol, event_type,
            details_json, correlation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            _iso(occurred_at or _utc_now()),
            trading_date.isoformat() if isinstance(trading_date, date) else trading_date,
            symbol,
            event_type,
            _canonical_json(details),
            correlation_id,
        ),
    )


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def export_database_backup(self) -> bytes:
        with self._lock:
            with tempfile.TemporaryDirectory(prefix="nse-breakout-backup-") as directory:
                backup_path = Path(directory) / "snapshot.sqlite3"
                destination = sqlite3.connect(backup_path)
                try:
                    self._connection.backup(destination)
                    journal_mode = destination.execute(
                        "PRAGMA journal_mode = DELETE"
                    ).fetchone()[0]
                    if journal_mode != "delete":
                        raise RuntimeError(
                            "Could not create a standalone SQLite backup."
                        )
                finally:
                    destination.close()
                return backup_path.read_bytes()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > 3:
            raise RuntimeError(f"Database schema version {version} is newer than this app.")
        if version == 3:
            return
        if version == 1:
            with self.transaction() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS replay_checkpoints (
                        trading_date TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        last_candle_sequence INTEGER NOT NULL,
                        last_oi_sequence INTEGER NOT NULL DEFAULT -1,
                        mapping_id TEXT,
                        strategy_version TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (trading_date, symbol)
                    )
                    """
                )
                checkpoint_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(replay_checkpoints)"
                    ).fetchall()
                }
                if "last_oi_sequence" not in checkpoint_columns:
                    connection.execute(
                        "ALTER TABLE replay_checkpoints ADD COLUMN "
                        "last_oi_sequence INTEGER NOT NULL DEFAULT -1"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_outbox (
                        notification_id TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL REFERENCES signal_events(event_id),
                        channel TEXT NOT NULL CHECK (channel IN ('in_app', 'telegram')),
                        payload_version INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at TEXT,
                        status TEXT NOT NULL CHECK (
                            status IN ('available', 'queued', 'delivered', 'failed', 'disabled')
                        ),
                        last_error TEXT,
                        provider_message_id TEXT,
                        delivered_at TEXT,
                        UNIQUE (event_id, channel, payload_version)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS notification_outbox_pending
                    ON notification_outbox(channel, status, next_attempt_at)
                    """
                )
                legacy_signals = connection.execute(
                    """
                    SELECT s.event_id, s.trading_date, s.symbol, s.created_at
                    FROM signal_events s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM notification_outbox n
                        WHERE n.event_id = s.event_id AND n.channel = 'in_app'
                    )
                    """
                ).fetchall()
                for signal in legacy_signals:
                    notification_id = hashlib.sha256(
                        f"{signal['event_id']}\x1fin_app\x1f1".encode("utf-8")
                    ).hexdigest()
                    connection.execute(
                        """
                        INSERT INTO notification_outbox (
                            notification_id, event_id, channel, payload_version,
                            created_at, attempt_count, status
                        ) VALUES (?, ?, 'in_app', 1, ?, 0, 'available')
                        """,
                        (notification_id, signal["event_id"], signal["created_at"]),
                    )
                    _audit(
                        connection,
                        event_type="IN_APP_NOTIFICATION_MIGRATED",
                        trading_date=signal["trading_date"],
                        symbol=signal["symbol"],
                        details={
                            "notification_id": notification_id,
                            "event_id": signal["event_id"],
                            "channel": "in_app",
                        },
                        occurred_at=datetime.fromisoformat(signal["created_at"]),
                    )
                connection.execute("PRAGMA user_version = 2")
            version = 2
        if version == 2:
            with self.transaction() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS replay_checkpoints (
                        trading_date TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        last_candle_sequence INTEGER NOT NULL,
                        last_oi_sequence INTEGER NOT NULL DEFAULT -1,
                        mapping_id TEXT,
                        strategy_version TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (trading_date, symbol)
                    )
                    """
                )
                checkpoint_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(replay_checkpoints)"
                    ).fetchall()
                }
                if "last_oi_sequence" not in checkpoint_columns:
                    connection.execute(
                        "ALTER TABLE replay_checkpoints ADD COLUMN "
                        "last_oi_sequence INTEGER NOT NULL DEFAULT -1"
                    )
            self._migrate_signal_references()
            return
        with self.transaction() as connection:
            schema = """
                CREATE TABLE instrument_mappings (
                    mapping_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL CHECK (exchange = 'NSE'),
                    provider_instrument_id TEXT NOT NULL,
                    fno_eligible INTEGER NOT NULL CHECK (fno_eligible IN (0, 1)),
                    mapping_source TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'superseded'))
                );
                CREATE UNIQUE INDEX one_active_mapping_per_symbol
                    ON instrument_mappings(symbol) WHERE status = 'active';

                CREATE TABLE monitored_symbols (
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    mapping_id TEXT NOT NULL REFERENCES instrument_mappings(mapping_id),
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),
                    exclusion_reason TEXT,
                    PRIMARY KEY (trading_date, symbol)
                );

                CREATE TABLE beacon_observations (
                    observation_id TEXT PRIMARY KEY,
                    poll_id TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    raw_symbol TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK (direction IN ('bullish', 'bearish')),
                    source_name TEXT NOT NULL,
                    source_method TEXT NOT NULL,
                    provider_time TEXT,
                    received_at TEXT NOT NULL,
                    mapping_status TEXT NOT NULL,
                    result_status TEXT NOT NULL,
                    details TEXT NOT NULL
                );
                CREATE INDEX beacon_by_date_symbol
                    ON beacon_observations(trading_date, symbol, received_at);

                CREATE TABLE candle_versions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id TEXT NOT NULL UNIQUE,
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    provider_instrument_id TEXT NOT NULL,
                    interval_start TEXT NOT NULL,
                    interval_end TEXT NOT NULL,
                    open TEXT NOT NULL,
                    high TEXT NOT NULL,
                    low TEXT NOT NULL,
                    close TEXT NOT NULL,
                    volume TEXT,
                    provider_time TEXT,
                    received_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX candles_by_date_symbol_interval
                    ON candle_versions(trading_date, symbol, interval_start, sequence);

                CREATE TABLE range_versions (
                    range_id TEXT PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL CHECK (timeframe IN ('15M', '30M')),
                    high TEXT,
                    low TEXT,
                    state TEXT NOT NULL,
                    freeze_at TEXT NOT NULL,
                    source_candle_ids TEXT NOT NULL,
                    missing_intervals TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX ranges_by_date_symbol
                    ON range_versions(trading_date, symbol, timeframe, is_current);

                CREATE TABLE collision_levels (
                    collision_id TEXT PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL CHECK (timeframe IN ('15M', '30M')),
                    side TEXT NOT NULL CHECK (side IN ('high', 'low')),
                    level TEXT NOT NULL,
                    forming_candle_id TEXT NOT NULL,
                    formed_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
                    replaced_by TEXT,
                    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                    strategy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX collisions_by_date_symbol
                    ON collision_levels(trading_date, symbol, timeframe, is_current);

                CREATE TABLE signal_events (
                    event_id TEXT PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL CHECK (timeframe IN ('15M', '30M')),
                    signal_type TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK (direction IN ('bullish', 'bearish')),
                    candle_id TEXT NOT NULL REFERENCES candle_versions(version_id),
                    candle_start TEXT NOT NULL,
                    candle_end TEXT NOT NULL,
                    close TEXT NOT NULL,
                    level TEXT NOT NULL,
                    range_id TEXT NOT NULL REFERENCES range_versions(range_id),
                    range_high TEXT NOT NULL,
                    range_low TEXT NOT NULL,
                    collision_id TEXT REFERENCES collision_levels(collision_id),
                    strategy_version TEXT NOT NULL,
                    oi_status TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
                    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX signals_by_date_status
                    ON signal_events(trading_date, status, is_current, candle_start);

                CREATE TABLE notification_outbox (
                    notification_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES signal_events(event_id),
                    channel TEXT NOT NULL CHECK (channel IN ('in_app', 'telegram')),
                    payload_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN ('available', 'queued', 'delivered', 'failed', 'disabled')
                    ),
                    last_error TEXT,
                    provider_message_id TEXT,
                    delivered_at TEXT,
                    UNIQUE (event_id, channel, payload_version)
                );
                CREATE INDEX notification_outbox_pending
                    ON notification_outbox(channel, status, next_attempt_at);

                CREATE TABLE oi_observations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_id TEXT NOT NULL UNIQUE,
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    provider_instrument_id TEXT NOT NULL,
                    contract_type TEXT NOT NULL CHECK (contract_type IN ('future', 'option')),
                    expiry TEXT NOT NULL,
                    strike TEXT,
                    option_type TEXT CHECK (option_type IN ('CALL', 'PUT') OR option_type IS NULL),
                    open_interest TEXT NOT NULL,
                    oi_change TEXT,
                    volume TEXT NOT NULL,
                    ltp TEXT NOT NULL,
                    provider_time TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX oi_by_date_symbol_time
                    ON oi_observations(trading_date, symbol, provider_time, sequence);

                CREATE TABLE audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    trading_date TEXT,
                    symbol TEXT,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    correlation_id TEXT
                );
                CREATE INDEX audit_by_date_time
                    ON audit_events(trading_date, occurred_at, sequence);

                CREATE TABLE app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE replay_checkpoints (
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    last_candle_sequence INTEGER NOT NULL,
                    last_oi_sequence INTEGER NOT NULL DEFAULT -1,
                    mapping_id TEXT,
                    strategy_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (trading_date, symbol)
                );
                """
            for statement in schema.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute("PRAGMA user_version = 3")

    def _migrate_signal_references(self) -> None:
        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    CREATE TABLE signal_events_v3 (
                        event_id TEXT PRIMARY KEY,
                        trading_date TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL CHECK (timeframe IN ('15M', '30M')),
                        signal_type TEXT NOT NULL,
                        direction TEXT NOT NULL CHECK (direction IN ('bullish', 'bearish')),
                        candle_id TEXT NOT NULL REFERENCES candle_versions(version_id),
                        candle_start TEXT NOT NULL,
                        candle_end TEXT NOT NULL,
                        close TEXT NOT NULL,
                        level TEXT NOT NULL,
                        range_id TEXT NOT NULL REFERENCES range_versions(range_id),
                        range_high TEXT NOT NULL,
                        range_low TEXT NOT NULL,
                        collision_id TEXT REFERENCES collision_levels(collision_id),
                        strategy_version TEXT NOT NULL,
                        oi_status TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
                        is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO signal_events_v3 (
                        event_id, trading_date, symbol, timeframe, signal_type,
                        direction, candle_id, candle_start, candle_end, close,
                        level, range_id, range_high, range_low, collision_id,
                        strategy_version, oi_status, status, is_current, created_at
                    )
                    SELECT event_id, trading_date, symbol, timeframe, signal_type,
                           direction, candle_id, candle_start, candle_end, close,
                           level, range_id, range_high, range_low, collision_id,
                           strategy_version, oi_status, status, is_current, created_at
                    FROM signal_events
                    """
                )
                connection.execute("DROP TABLE signal_events")
                connection.execute(
                    "ALTER TABLE signal_events_v3 RENAME TO signal_events"
                )
                connection.execute(
                    """
                    CREATE INDEX signals_by_date_status
                    ON signal_events(trading_date, status, is_current, candle_start)
                    """
                )
                connection.execute("PRAGMA user_version = 3")
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")

        violations = self._connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                "Database contains signal references that do not match stored candles, "
                "ranges, or collision levels; migration was stopped."
            )

    def save_mapping_and_watchlist(
        self,
        *,
        trading_date: date,
        symbol: str,
        provider_instrument_id: str,
        fno_eligible_confirmed: bool,
        source: str = "manual",
    ) -> str:
        if not fno_eligible_confirmed:
            raise MappingError("Confirm NSE F&O eligibility before adding a symbol.")
        if not symbol or not provider_instrument_id:
            raise MappingError("A normalized symbol and provider instrument ID are required.")
        now = _utc_now()
        now_iso = _iso(now)
        with self.transaction() as connection:
            active = connection.execute(
                "SELECT * FROM instrument_mappings WHERE symbol = ? AND status = 'active'",
                (symbol,),
            ).fetchone()
            if (
                active is not None
                and active["provider_instrument_id"] == provider_instrument_id
                and active["fno_eligible"] == 1
            ):
                mapping_id = active["mapping_id"]
            else:
                previous_mapping_id = None
                if active is not None:
                    previous_mapping_id = active["mapping_id"]
                    connection.execute(
                        "UPDATE instrument_mappings SET status = 'superseded' WHERE mapping_id = ?",
                        (previous_mapping_id,),
                    )
                    _audit(
                        connection,
                        event_type="INSTRUMENT_MAPPING_SUPERSEDED",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={
                            "mapping_id": previous_mapping_id,
                            "replacement_instrument_id": provider_instrument_id,
                        },
                    )
                matching = connection.execute(
                    """
                    SELECT * FROM instrument_mappings
                    WHERE symbol = ? AND provider_instrument_id = ? AND fno_eligible = 1
                    ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, verified_at DESC
                    LIMIT 1
                    """,
                    (symbol, provider_instrument_id),
                ).fetchone()
                if matching is not None:
                    mapping_id = matching["mapping_id"]
                    if matching["status"] != "active":
                        connection.execute(
                            """
                            UPDATE instrument_mappings
                            SET status = 'active', mapping_source = ?, verified_at = ?
                            WHERE mapping_id = ?
                            """,
                            (source, now_iso, mapping_id),
                        )
                        _audit(
                            connection,
                            event_type="INSTRUMENT_MAPPING_REACTIVATED",
                            trading_date=trading_date,
                            symbol=symbol,
                            details={
                                "mapping_id": mapping_id,
                                "provider_instrument_id": provider_instrument_id,
                                "previous_mapping_id": previous_mapping_id,
                                "source": source,
                            },
                        )
                else:
                    mapping_id = str(uuid4())
                    connection.execute(
                        """
                        INSERT INTO instrument_mappings (
                            mapping_id, symbol, exchange, provider_instrument_id,
                            fno_eligible, mapping_source, verified_at, status
                        ) VALUES (?, ?, 'NSE', ?, 1, ?, ?, 'active')
                        """,
                        (mapping_id, symbol, provider_instrument_id, source, now_iso),
                    )
                    _audit(
                        connection,
                        event_type="INSTRUMENT_MAPPING_CONFIRMED",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={
                            "mapping_id": mapping_id,
                            "provider_instrument_id": provider_instrument_id,
                            "previous_mapping_id": previous_mapping_id,
                            "source": source,
                        },
                    )

            existing = connection.execute(
                """
                SELECT mapping_id FROM monitored_symbols
                WHERE trading_date = ? AND symbol = ?
                """,
                (trading_date.isoformat(), symbol),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO monitored_symbols (
                        trading_date, symbol, mapping_id, first_seen_at,
                        last_seen_at, source, excluded, exclusion_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
                    """,
                    (trading_date.isoformat(), symbol, mapping_id, now_iso, now_iso, source),
                )
                _audit(
                    connection,
                    event_type="WATCHLIST_SYMBOL_ADDED",
                    trading_date=trading_date,
                    symbol=symbol,
                    details={"source": source, "mapping_id": mapping_id},
                )
            else:
                connection.execute(
                    """
                    UPDATE monitored_symbols
                    SET mapping_id = ?, last_seen_at = ?, source = ?,
                        excluded = 0, exclusion_reason = NULL
                    WHERE trading_date = ? AND symbol = ?
                    """,
                    (mapping_id, now_iso, source, trading_date.isoformat(), symbol),
                )
                if existing["mapping_id"] != mapping_id:
                    _audit(
                        connection,
                        event_type="WATCHLIST_MAPPING_UPDATED",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={
                            "previous_mapping_id": existing["mapping_id"],
                            "mapping_id": mapping_id,
                        },
                    )
        return mapping_id

    def get_active_mapping(self, *, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM instrument_mappings
                WHERE symbol = ? AND status = 'active'
                """,
                (symbol,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_mapping(self, *, mapping_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM instrument_mappings WHERE mapping_id = ?",
                (mapping_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_watchlist_entry(
        self, *, trading_date: date, symbol: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM monitored_symbols
                WHERE trading_date = ? AND symbol = ?
                """,
                (trading_date.isoformat(), symbol),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_pending_replay_targets(self) -> list[tuple[date, str]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT w.trading_date, w.symbol
                FROM monitored_symbols w
                LEFT JOIN candle_versions c
                  ON c.trading_date = w.trading_date AND c.symbol = w.symbol
                LEFT JOIN oi_observations o
                  ON o.trading_date = w.trading_date AND o.symbol = w.symbol
                LEFT JOIN instrument_mappings m
                  ON m.mapping_id = w.mapping_id
                LEFT JOIN replay_checkpoints r
                  ON r.trading_date = w.trading_date AND r.symbol = w.symbol
                WHERE w.excluded = 0
                GROUP BY w.trading_date, w.symbol
                HAVING MAX(c.sequence) IS NOT NULL
                   AND (
                       MAX(c.sequence) != COALESCE(r.last_candle_sequence, -1)
                       OR COALESCE(MAX(o.sequence), -1) != COALESCE(r.last_oi_sequence, -1)
                       OR COALESCE(m.mapping_id, '') != COALESCE(r.mapping_id, '')
                       OR COALESCE(r.strategy_version, '') != ?
                   )
                ORDER BY w.trading_date, w.symbol
                """,
                (STRATEGY_VERSION,),
            ).fetchall()
        return [(date.fromisoformat(row["trading_date"]), row["symbol"]) for row in rows]

    def exclude_symbol(
        self, *, trading_date: date, symbol: str, reason: str
    ) -> None:
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 240:
            raise MappingError("An exclusion reason of 1–240 characters is required.")
        now = _utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT excluded, exclusion_reason FROM monitored_symbols
                WHERE trading_date = ? AND symbol = ?
                """,
                (trading_date.isoformat(), symbol),
            ).fetchone()
            if row is None:
                raise MappingError(f"{symbol} is not on this trading-day watchlist.")
            if row["excluded"] and row["exclusion_reason"] == normalized_reason:
                return
            connection.execute(
                """
                UPDATE monitored_symbols
                SET excluded = 1, exclusion_reason = ?, last_seen_at = ?
                WHERE trading_date = ? AND symbol = ?
                """,
                (
                    normalized_reason,
                    _iso(now),
                    trading_date.isoformat(),
                    symbol,
                ),
            )
            _audit(
                connection,
                event_type="WATCHLIST_SYMBOL_EXCLUDED",
                trading_date=trading_date,
                symbol=symbol,
                details={
                    "previous_excluded": bool(row["excluded"]),
                    "previous_reason": row["exclusion_reason"],
                    "reason": normalized_reason,
                },
                occurred_at=now,
            )

    def import_beacon_observations(
        self,
        *,
        trading_date: date,
        bullish_symbols: Sequence[str],
        bearish_symbols: Sequence[str],
        source_name: str,
        provider_time: datetime | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        now_iso = _iso(now)
        provider_time_iso = _iso(provider_time) if provider_time is not None else None
        poll_id = str(uuid4())
        observations = [
            (symbol, "bullish") for symbol in bullish_symbols
        ] + [(symbol, "bearish") for symbol in bearish_symbols]
        mapped: set[str] = set()
        unmapped: set[str] = set()
        with self.transaction() as connection:
            for symbol, direction in observations:
                observation_id = hashlib.sha256(
                    f"{poll_id}\x1f{symbol}\x1f{direction}".encode("utf-8")
                ).hexdigest()
                mapping = connection.execute(
                    """
                    SELECT mapping_id, fno_eligible FROM instrument_mappings
                    WHERE symbol = ? AND status = 'active'
                    """,
                    (symbol,),
                ).fetchone()
                mapping_status = (
                    "mapped"
                    if mapping is not None and mapping["fno_eligible"] == 1
                    else "unmapped"
                )
                connection.execute(
                    """
                    INSERT INTO beacon_observations (
                        observation_id, poll_id, trading_date, symbol, raw_symbol,
                        direction, source_name, source_method, provider_time,
                        received_at, mapping_status, result_status, details
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual_import', ?, ?, ?, 'recorded', '{}')
                    """,
                    (
                        observation_id,
                        poll_id,
                        trading_date.isoformat(),
                        symbol,
                        symbol,
                        direction,
                        source_name,
                        provider_time_iso,
                        now_iso,
                        mapping_status,
                    ),
                )
                if mapping_status == "mapped":
                    mapped.add(symbol)
                    self._ensure_watchlist_row(
                        connection,
                        trading_date=trading_date,
                        symbol=symbol,
                        mapping_id=mapping["mapping_id"],
                        source="beacon",
                        now_iso=now_iso,
                    )
                else:
                    unmapped.add(symbol)

            _audit(
                connection,
                event_type="BEACON_MANUAL_IMPORT_RECORDED",
                trading_date=trading_date,
                symbol=None,
                details={
                    "poll_id": poll_id,
                    "source_name": source_name,
                    "bullish_count": len(bullish_symbols),
                    "bearish_count": len(bearish_symbols),
                    "mapped_count": len(mapped),
                    "unmapped_symbols": sorted(unmapped),
                },
                correlation_id=correlation_id,
                occurred_at=now,
            )
        return {
            "poll_id": poll_id,
            "observations": len(observations),
            "mapped_symbols": sorted(mapped),
            "unmapped_symbols": sorted(unmapped),
            "conflicts": sorted(set(bullish_symbols) & set(bearish_symbols)),
        }

    def _ensure_watchlist_row(
        self,
        connection: sqlite3.Connection,
        *,
        trading_date: date,
        symbol: str,
        mapping_id: str,
        source: str,
        now_iso: str,
    ) -> None:
        existing = connection.execute(
            """
            SELECT symbol FROM monitored_symbols
            WHERE trading_date = ? AND symbol = ?
            """,
            (trading_date.isoformat(), symbol),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO monitored_symbols (
                    trading_date, symbol, mapping_id, first_seen_at,
                    last_seen_at, source, excluded, exclusion_reason
                ) VALUES (?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                (
                    trading_date.isoformat(),
                    symbol,
                    mapping_id,
                    now_iso,
                    now_iso,
                    source,
                ),
            )
            _audit(
                connection,
                event_type="WATCHLIST_SYMBOL_ADDED",
                trading_date=trading_date,
                symbol=symbol,
                details={"source": source, "mapping_id": mapping_id},
            )
        else:
            connection.execute(
                """
                UPDATE monitored_symbols SET last_seen_at = ?
                WHERE trading_date = ? AND symbol = ?
                """,
                (now_iso, trading_date.isoformat(), symbol),
            )

    def import_candles(
        self,
        *,
        trading_date: date,
        candles: Sequence[Candle],
        correlation_id: str,
    ) -> dict[str, int]:
        inserted = 0
        duplicates = 0
        corrections = 0
        now = _utc_now()
        now_iso = _iso(now)
        with self.transaction() as connection:
            for candle in candles:
                watch = connection.execute(
                    """
                    SELECT mapping_id, excluded FROM monitored_symbols
                    WHERE trading_date = ? AND symbol = ?
                    """,
                    (trading_date.isoformat(), candle.symbol),
                ).fetchone()
                if watch is None or watch["excluded"]:
                    raise MappingError(
                        f"{candle.symbol} must be on the trading-day watchlist before candle import."
                    )
                mapping = connection.execute(
                    """
                    SELECT m.provider_instrument_id, m.fno_eligible
                    FROM instrument_mappings m
                    WHERE m.mapping_id = ?
                    """,
                    (watch["mapping_id"],),
                ).fetchone()
                if mapping is None or mapping["fno_eligible"] != 1:
                    raise MappingError(
                        f"{candle.symbol} has no confirmed trading-day instrument mapping."
                    )
                if mapping["provider_instrument_id"] != candle.provider_instrument_id:
                    raise MappingError(
                        f"{candle.symbol} provider instrument ID does not match the confirmed mapping."
                    )
                version_id = candle_identity(candle)
                duplicate = connection.execute(
                    "SELECT 1 FROM candle_versions WHERE version_id = ?",
                    (version_id,),
                ).fetchone()
                if duplicate is not None:
                    duplicates += 1
                    continue
                previous = connection.execute(
                    """
                    SELECT version_id FROM candle_versions
                    WHERE trading_date = ? AND symbol = ? AND interval_start = ?
                    ORDER BY sequence DESC LIMIT 1
                    """,
                    (
                        trading_date.isoformat(),
                        candle.symbol,
                        _iso(candle.interval_start),
                    ),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO candle_versions (
                        version_id, trading_date, symbol, exchange,
                        provider_instrument_id, interval_start, interval_end,
                        open, high, low, close, volume, provider_time, received_at,
                        source, status
                    ) VALUES (?, ?, ?, 'NSE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        trading_date.isoformat(),
                        candle.symbol,
                        candle.provider_instrument_id,
                        _iso(candle.interval_start),
                        _iso(candle.interval_end),
                        _decimal(candle.open),
                        _decimal(candle.high),
                        _decimal(candle.low),
                        _decimal(candle.close),
                        _decimal(candle.volume),
                        _iso(candle.provider_time) if candle.provider_time else None,
                        now_iso,
                        candle.source,
                        candle.status.value,
                    ),
                )
                inserted += 1
                if previous is not None:
                    corrections += 1
                _audit(
                    connection,
                    event_type=(
                        "CANDLE_CORRECTION_RECORDED"
                        if previous is not None
                        else "CANDLE_IMPORTED"
                    ),
                    trading_date=trading_date,
                    symbol=candle.symbol,
                    details={
                        "version_id": version_id,
                        "previous_version_id": (
                            previous["version_id"] if previous is not None else None
                        ),
                        "interval_start": _iso(candle.interval_start),
                        "source": candle.source,
                        "status": candle.status.value,
                    },
                    correlation_id=correlation_id,
                    occurred_at=now,
                )
        return {"inserted": inserted, "duplicates": duplicates, "corrections": corrections}

    def get_candles(self, *, trading_date: date, symbol: str) -> list[Candle]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT c.* FROM candle_versions c
                JOIN (
                    SELECT interval_start, MAX(sequence) AS latest_sequence
                    FROM candle_versions
                    WHERE trading_date = ? AND symbol = ?
                    GROUP BY interval_start
                ) latest ON latest.latest_sequence = c.sequence
                ORDER BY c.interval_start
                """,
                (trading_date.isoformat(), symbol),
            ).fetchall()
        return [
            Candle(
                symbol=row["symbol"],
                provider_instrument_id=row["provider_instrument_id"],
                interval_start=datetime.fromisoformat(row["interval_start"]),
                interval_end=datetime.fromisoformat(row["interval_end"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]) if row["volume"] is not None else None,
                provider_time=_parse_datetime(row["provider_time"]),
                received_at=_parse_datetime(row["received_at"]),
                source=row["source"],
                status=CandleStatus(row["status"]),
                version_id=row["version_id"],
            )
            for row in rows
        ]

    @staticmethod
    def _oi_status_at(
        connection: sqlite3.Connection,
        *,
        trading_date: date,
        symbol: str,
        at_or_before: datetime,
    ) -> str:
        rows = connection.execute(
            """
            SELECT status FROM (
                SELECT status,
                       ROW_NUMBER() OVER (
                           PARTITION BY provider_instrument_id, contract_type,
                                        expiry, strike, option_type
                           ORDER BY provider_time DESC, sequence DESC
                       ) AS rank
                FROM oi_observations
                WHERE trading_date = ? AND symbol = ? AND provider_time <= ?
            )
            WHERE rank = 1
            """,
            (trading_date.isoformat(), symbol, _iso(at_or_before)),
        ).fetchall()
        if not rows:
            return "unavailable"
        statuses = {row["status"] for row in rows}
        if "stale" in statuses:
            return "stale"
        if statuses == {"illiquid"}:
            return "illiquid"
        if "complete" in statuses:
            return "raw_only_uncalibrated"
        return "unavailable"

    def reconcile_evaluation(
        self,
        *,
        trading_date: date,
        symbol: str,
        evaluation: Evaluation,
        correlation_id: str,
    ) -> None:
        now = _utc_now()
        now_iso = _iso(now)
        desired_ranges = {opening.range_id: opening for opening in evaluation.ranges.values()}
        desired_collisions = {
            collision.collision_id: collision for collision in evaluation.collisions
        }
        desired_signals = {signal.event_id: signal for signal in evaluation.signals}

        with self.transaction() as connection:
            for opening in desired_ranges.values():
                old_rows = connection.execute(
                    """
                    SELECT range_id FROM range_versions
                    WHERE trading_date = ? AND symbol = ? AND timeframe = ? AND is_current = 1
                    """,
                    (trading_date.isoformat(), symbol, opening.timeframe),
                ).fetchall()
                if any(row["range_id"] != opening.range_id for row in old_rows):
                    connection.execute(
                        """
                        UPDATE range_versions SET is_current = 0
                        WHERE trading_date = ? AND symbol = ? AND timeframe = ? AND is_current = 1
                        """,
                        (trading_date.isoformat(), symbol, opening.timeframe),
                    )
                    for row in old_rows:
                        if row["range_id"] != opening.range_id:
                            _audit(
                                connection,
                                event_type="OPENING_RANGE_SUPERSEDED_BY_REPLAY",
                                trading_date=trading_date,
                                symbol=symbol,
                                details={
                                    "range_id": row["range_id"],
                                    "replacement_range_id": opening.range_id,
                                    "timeframe": opening.timeframe,
                                },
                                correlation_id=correlation_id,
                                occurred_at=now,
                            )
                existing = connection.execute(
                    "SELECT is_current FROM range_versions WHERE range_id = ?",
                    (opening.range_id,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO range_versions (
                            range_id, trading_date, symbol, timeframe, high, low,
                            state, freeze_at, source_candle_ids, missing_intervals,
                            strategy_version, is_current, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1.0', 1, ?)
                        """,
                        (
                            opening.range_id,
                            trading_date.isoformat(),
                            symbol,
                            opening.timeframe,
                            _decimal(opening.high),
                            _decimal(opening.low),
                            opening.status,
                            _iso(opening.freeze_at),
                            _canonical_json(opening.candle_ids),
                            _canonical_json(
                                [_iso(item) for item in opening.missing_intervals]
                            ),
                            now_iso,
                        ),
                    )
                    _audit(
                        connection,
                        event_type="OPENING_RANGE_REBUILT",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={
                            "range_id": opening.range_id,
                            "timeframe": opening.timeframe,
                            "state": opening.status,
                            "high": _decimal(opening.high),
                            "low": _decimal(opening.low),
                            "missing_intervals": [
                                _iso(item) for item in opening.missing_intervals
                            ],
                        },
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )
                elif not existing["is_current"]:
                    connection.execute(
                        "UPDATE range_versions SET is_current = 1 WHERE range_id = ?",
                        (opening.range_id,),
                    )
                    _audit(
                        connection,
                        event_type="OPENING_RANGE_REACTIVATED",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={"range_id": opening.range_id},
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )

            current_collision_rows = connection.execute(
                """
                SELECT collision_id FROM collision_levels
                WHERE trading_date = ? AND symbol = ? AND is_current = 1
                """,
                (trading_date.isoformat(), symbol),
            ).fetchall()
            desired_collision_ids = set(desired_collisions)
            for row in current_collision_rows:
                if row["collision_id"] not in desired_collision_ids:
                    connection.execute(
                        """
                        UPDATE collision_levels
                        SET is_current = 0, status = 'superseded'
                        WHERE collision_id = ?
                        """,
                        (row["collision_id"],),
                    )
                    _audit(
                        connection,
                        event_type="COLLISION_RECOMPUTED",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={"collision_id": row["collision_id"]},
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )
            for collision in desired_collisions.values():
                existing = connection.execute(
                    "SELECT * FROM collision_levels WHERE collision_id = ?",
                    (collision.collision_id,),
                ).fetchone()
                current_flag = 1
                replacement_changed = False
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO collision_levels (
                            collision_id, trading_date, symbol, timeframe, side,
                            level, forming_candle_id, formed_at, status, replaced_by,
                            is_current, strategy_version, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1.0', ?)
                        """,
                        (
                            collision.collision_id,
                            trading_date.isoformat(),
                            symbol,
                            collision.timeframe,
                            collision.side,
                            _decimal(collision.level),
                            collision.forming_candle_id,
                            _iso(collision.formed_at),
                            collision.status,
                            collision.replaced_by,
                            current_flag,
                            now_iso,
                        ),
                    )
                    _audit(
                        connection,
                        event_type="COLLISION_LEVEL_FORMED",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={
                            "collision_id": collision.collision_id,
                            "timeframe": collision.timeframe,
                            "side": collision.side,
                            "level": _decimal(collision.level),
                            "forming_candle_id": collision.forming_candle_id,
                        },
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )
                else:
                    replacement_changed = (
                        existing["status"] != collision.status
                        or existing["replaced_by"] != collision.replaced_by
                    )
                    connection.execute(
                        """
                        UPDATE collision_levels SET status = ?, replaced_by = ?, is_current = 1
                        WHERE collision_id = ?
                        """,
                        (collision.status, collision.replaced_by, collision.collision_id),
                    )
                if collision.replaced_by and (
                    existing is None or replacement_changed
                ):
                    _audit(
                        connection,
                        event_type="COLLISION_LEVEL_REPLACED",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={
                            "collision_id": collision.collision_id,
                            "replaced_by": collision.replaced_by,
                        },
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )

            current_signal_rows = connection.execute(
                """
                SELECT event_id FROM signal_events
                WHERE trading_date = ? AND symbol = ? AND is_current = 1 AND status = 'active'
                """,
                (trading_date.isoformat(), symbol),
            ).fetchall()
            desired_signal_ids = set(desired_signals)
            for row in current_signal_rows:
                if row["event_id"] not in desired_signal_ids:
                    connection.execute(
                        """
                        UPDATE signal_events SET status = 'superseded', is_current = 0
                        WHERE event_id = ?
                        """,
                        (row["event_id"],),
                    )
                    _audit(
                        connection,
                        event_type="SIGNAL_SUPERSEDED_BY_REPLAY",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={"event_id": row["event_id"]},
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )
            for signal in desired_signals.values():
                oi_status = self._oi_status_at(
                    connection,
                    trading_date=trading_date,
                    symbol=symbol,
                    at_or_before=signal.candle_end,
                )
                existing = connection.execute(
                    """
                    SELECT status, is_current, oi_status
                    FROM signal_events WHERE event_id = ?
                    """,
                    (signal.event_id,),
                ).fetchone()
                if existing is None:
                    self._insert_signal(
                        connection,
                        signal=signal,
                        trading_date=trading_date,
                        oi_status=oi_status,
                        created_at=now_iso,
                    )
                    _audit(
                        connection,
                        event_type="SIGNAL_CONFIRMED_BY_REPLAY",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={
                            "event_id": signal.event_id,
                            "timeframe": signal.timeframe,
                            "signal_type": signal.signal_type,
                            "candle_id": signal.candle_id,
                            "level": _decimal(signal.level),
                        },
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )
                else:
                    if existing["oi_status"] != oi_status:
                        connection.execute(
                            "UPDATE signal_events SET oi_status = ? WHERE event_id = ?",
                            (oi_status, signal.event_id),
                        )
                        _audit(
                            connection,
                            event_type="SIGNAL_OI_CONTEXT_UPDATED",
                            trading_date=trading_date,
                            symbol=symbol,
                            details={
                                "event_id": signal.event_id,
                                "previous_status": existing["oi_status"],
                                "new_status": oi_status,
                            },
                            correlation_id=correlation_id,
                            occurred_at=now,
                        )
                if existing is not None and (
                    existing["status"] != "active" or not existing["is_current"]
                ):
                    connection.execute(
                        """
                        UPDATE signal_events SET status = 'active', is_current = 1
                        WHERE event_id = ?
                        """,
                        (signal.event_id,),
                    )
                    _audit(
                        connection,
                        event_type="SIGNAL_REACTIVATED_BY_REPLAY",
                        trading_date=trading_date,
                        symbol=symbol,
                        details={"event_id": signal.event_id},
                        correlation_id=correlation_id,
                        occurred_at=now,
                    )

            latest_candle = connection.execute(
                """
                SELECT MAX(sequence) AS latest_sequence FROM candle_versions
                WHERE trading_date = ? AND symbol = ?
                """,
                (trading_date.isoformat(), symbol),
            ).fetchone()
            latest_oi = connection.execute(
                """
                SELECT MAX(sequence) AS latest_sequence FROM oi_observations
                WHERE trading_date = ? AND symbol = ?
                """,
                (trading_date.isoformat(), symbol),
            ).fetchone()
            mapping = connection.execute(
                """
                SELECT mapping_id FROM instrument_mappings
                WHERE symbol = ? AND status = 'active'
                """,
                (symbol,),
            ).fetchone()
            latest_sequence = (
                latest_candle["latest_sequence"]
                if latest_candle["latest_sequence"] is not None
                else -1
            )
            latest_oi_sequence = (
                latest_oi["latest_sequence"]
                if latest_oi["latest_sequence"] is not None
                else -1
            )
            mapping_id = mapping["mapping_id"] if mapping is not None else None
            checkpoint = connection.execute(
                """
                SELECT * FROM replay_checkpoints
                WHERE trading_date = ? AND symbol = ?
                """,
                (trading_date.isoformat(), symbol),
            ).fetchone()
            checkpoint_changed = (
                checkpoint is None
                or checkpoint["last_candle_sequence"] != latest_sequence
                or checkpoint["last_oi_sequence"] != latest_oi_sequence
                or checkpoint["mapping_id"] != mapping_id
                or checkpoint["strategy_version"] != STRATEGY_VERSION
            )
            connection.execute(
                """
                INSERT INTO replay_checkpoints (
                    trading_date, symbol, last_candle_sequence, last_oi_sequence, mapping_id,
                    strategy_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trading_date, symbol) DO UPDATE SET
                    last_candle_sequence = excluded.last_candle_sequence,
                    last_oi_sequence = excluded.last_oi_sequence,
                    mapping_id = excluded.mapping_id,
                    strategy_version = excluded.strategy_version,
                    updated_at = excluded.updated_at
                """,
                (
                    trading_date.isoformat(),
                    symbol,
                    latest_sequence,
                    latest_oi_sequence,
                    mapping_id,
                    STRATEGY_VERSION,
                    now_iso,
                ),
            )
            if checkpoint_changed:
                _audit(
                    connection,
                    event_type="REPLAY_CHECKPOINT_UPDATED",
                    trading_date=trading_date,
                    symbol=symbol,
                    details={
                        "last_candle_sequence": latest_sequence,
                        "last_oi_sequence": latest_oi_sequence,
                        "mapping_id": mapping_id,
                        "strategy_version": STRATEGY_VERSION,
                    },
                    correlation_id=correlation_id,
                    occurred_at=now,
                )

    @staticmethod
    def _insert_signal(
        connection: sqlite3.Connection,
        *,
        signal: Signal,
        trading_date: date,
        oi_status: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO signal_events (
                event_id, trading_date, symbol, timeframe, signal_type, direction,
                candle_id, candle_start, candle_end, close, level, range_id,
                range_high, range_low, collision_id, strategy_version, oi_status,
                status, is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'active', 1, ?)
            """,
            (
                signal.event_id,
                trading_date.isoformat(),
                signal.symbol,
                signal.timeframe,
                signal.signal_type,
                signal.direction,
                signal.candle_id,
                _iso(signal.candle_start),
                _iso(signal.candle_end),
                _decimal(signal.close),
                _decimal(signal.level),
                signal.range_id,
                _decimal(signal.range_high),
                _decimal(signal.range_low),
                signal.collision_id,
                signal.strategy_version,
                oi_status,
                created_at,
            ),
        )
        notification_id = hashlib.sha256(
            f"{signal.event_id}\x1fin_app\x1f1".encode("utf-8")
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO notification_outbox (
                notification_id, event_id, channel, payload_version,
                created_at, attempt_count, status
            ) VALUES (?, ?, 'in_app', 1, ?, 0, 'available')
            """,
            (notification_id, signal.event_id, created_at),
        )
        _audit(
            connection,
            event_type="IN_APP_NOTIFICATION_AVAILABLE",
            trading_date=trading_date,
            symbol=signal.symbol,
            details={
                "notification_id": notification_id,
                "event_id": signal.event_id,
                "channel": "in_app",
                "status": "available",
                "external_delivery": "disabled",
            },
            occurred_at=datetime.fromisoformat(created_at),
        )

    def import_oi_observations(
        self,
        *,
        trading_date: date,
        observations: Sequence[OIObservation],
        correlation_id: str,
    ) -> dict[str, int]:
        inserted = 0
        duplicates = 0
        now = _utc_now()
        now_iso = _iso(now)
        with self.transaction() as connection:
            for observation in observations:
                watch = connection.execute(
                    """
                    SELECT mapping_id, excluded FROM monitored_symbols
                    WHERE trading_date = ? AND symbol = ?
                    """,
                    (trading_date.isoformat(), observation.symbol),
                ).fetchone()
                if watch is None or watch["excluded"]:
                    raise MappingError(
                        f"{observation.symbol} must be on the trading-day watchlist before OI import."
                    )
                mapping = connection.execute(
                    """
                    SELECT fno_eligible FROM instrument_mappings
                    WHERE mapping_id = ?
                    """,
                    (watch["mapping_id"],),
                ).fetchone()
                if mapping is None or mapping["fno_eligible"] != 1:
                    raise MappingError(
                        f"{observation.symbol} has no confirmed trading-day instrument mapping."
                    )
                fields = (
                    trading_date.isoformat(),
                    observation.symbol,
                    observation.provider_instrument_id,
                    observation.contract_type,
                    observation.expiry.isoformat(),
                    _decimal(observation.strike),
                    observation.option_type,
                    _decimal(observation.open_interest),
                    _decimal(observation.oi_change),
                    _decimal(observation.volume),
                    _decimal(observation.ltp),
                    _iso(observation.provider_time),
                    observation.source,
                    observation.status,
                )
                observation_id = hashlib.sha256(
                    "\x1f".join("" if item is None else str(item) for item in fields).encode(
                        "utf-8"
                    )
                ).hexdigest()
                exists = connection.execute(
                    "SELECT 1 FROM oi_observations WHERE observation_id = ?",
                    (observation_id,),
                ).fetchone()
                if exists is not None:
                    duplicates += 1
                    continue
                connection.execute(
                    """
                    INSERT INTO oi_observations (
                        observation_id, trading_date, symbol, provider_instrument_id,
                        contract_type, expiry, strike, option_type, open_interest,
                        oi_change, volume, ltp, provider_time, received_at, source, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (observation_id, *fields[:11], fields[11], now_iso, *fields[12:]),
                )
                inserted += 1
                _audit(
                    connection,
                    event_type="RAW_OI_OBSERVATION_IMPORTED",
                    trading_date=trading_date,
                    symbol=observation.symbol,
                    details={
                        "observation_id": observation_id,
                        "contract_type": observation.contract_type,
                        "expiry": observation.expiry.isoformat(),
                        "strike": _decimal(observation.strike),
                        "option_type": observation.option_type,
                        "provider_time": _iso(observation.provider_time),
                        "status": observation.status,
                    },
                    correlation_id=correlation_id,
                    occurred_at=now,
                )
        return {"inserted": inserted, "duplicates": duplicates}

    def get_oi_context(
        self,
        *,
        trading_date: date,
        symbol: str,
        at_or_before: datetime | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        conditions = ["trading_date = ?", "symbol = ?"]
        parameters: list[Any] = [trading_date.isoformat(), symbol]
        if at_or_before is not None:
            conditions.append("provider_time <= ?")
            parameters.append(_iso(at_or_before))
        parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM oi_observations
                WHERE {' AND '.join(conditions)}
                ORDER BY provider_time DESC, sequence DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        latest: dict[tuple[Any, ...], sqlite3.Row] = {}
        for row in rows:
            contract = (
                row["provider_instrument_id"],
                row["contract_type"],
                row["expiry"],
                row["strike"],
                row["option_type"],
            )
            latest.setdefault(contract, row)
        return [dict(row) for row in latest.values()]

    def get_watchlist(self, *, trading_date: date) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT w.*, m.provider_instrument_id, m.fno_eligible,
                       m.mapping_source, m.status AS mapping_status
                FROM monitored_symbols w
                LEFT JOIN instrument_mappings m
                  ON m.mapping_id = w.mapping_id
                WHERE w.trading_date = ?
                ORDER BY w.symbol
                """,
                (trading_date.isoformat(),),
            ).fetchall()
            directions = self._connection.execute(
                """
                SELECT symbol,
                       MAX(CASE WHEN direction = 'bullish' THEN 1 ELSE 0 END) AS bullish_seen,
                       MAX(CASE WHEN direction = 'bearish' THEN 1 ELSE 0 END) AS bearish_seen
                FROM beacon_observations WHERE trading_date = ?
                GROUP BY symbol
                """,
                (trading_date.isoformat(),),
            ).fetchall()
        direction_by_symbol = {row["symbol"]: dict(row) for row in directions}
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            observed = direction_by_symbol.get(row["symbol"], {})
            item["bullish_seen"] = bool(observed.get("bullish_seen", False))
            item["bearish_seen"] = bool(observed.get("bearish_seen", False))
            item["direction_conflict"] = (
                item["bullish_seen"] and item["bearish_seen"]
            )
            result.append(item)
        return result

    def get_unmapped_beacon_symbols(self, *, trading_date: date) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT DISTINCT b.symbol FROM beacon_observations b
                WHERE b.trading_date = ? AND b.mapping_status = 'unmapped'
                  AND NOT EXISTS (
                    SELECT 1 FROM monitored_symbols w
                    JOIN instrument_mappings m ON m.mapping_id = w.mapping_id
                    WHERE w.trading_date = b.trading_date
                      AND w.symbol = b.symbol AND w.excluded = 0
                      AND m.fno_eligible = 1
                  )
                ORDER BY b.symbol
                """,
                (trading_date.isoformat(),),
            ).fetchall()
        return [row["symbol"] for row in rows]

    def get_signals(
        self,
        *,
        trading_date: date,
        include_superseded: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if limit is not None and limit < 1:
            raise ValueError("Signal query limit must be positive or None.")
        condition = "s.trading_date = ?"
        if not include_superseded:
            condition += " AND s.is_current = 1 AND s.status = 'active'"
        with self._lock:
            query = f"""
                SELECT s.*, c.source AS candle_source,
                       c.provider_time AS candle_provider_time,
                       c.received_at AS candle_received_at,
                       n.notification_id AS in_app_notification_id,
                       n.status AS in_app_notification_status
                FROM signal_events s
                LEFT JOIN candle_versions c ON c.version_id = s.candle_id
                LEFT JOIN notification_outbox n
                  ON n.event_id = s.event_id AND n.channel = 'in_app'
                WHERE {condition}
                ORDER BY s.candle_start DESC, s.symbol, s.timeframe
            """
            parameters: tuple[Any, ...] = (trading_date.isoformat(),)
            if limit is not None:
                query += " LIMIT ?"
                parameters = (*parameters, limit)
            rows = self._connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def get_notifications(
        self,
        *,
        trading_date: date,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT n.*, s.symbol, s.timeframe, s.signal_type, s.direction,
                       s.candle_start, s.candle_end, s.status AS signal_status
                FROM notification_outbox n
                JOIN signal_events s ON s.event_id = n.event_id
                WHERE s.trading_date = ?
                ORDER BY n.created_at DESC
                LIMIT ?
                """,
                (trading_date.isoformat(), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_notification(self, *, notification_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM notification_outbox WHERE notification_id = ?
                """,
                (notification_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_ranges(self, *, trading_date: date, symbol: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM range_versions
                WHERE trading_date = ? AND symbol = ? AND is_current = 1
                ORDER BY timeframe
                """,
                (trading_date.isoformat(), symbol),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_collisions(self, *, trading_date: date, symbol: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM collision_levels
                WHERE trading_date = ? AND symbol = ? AND is_current = 1
                ORDER BY formed_at, timeframe, side
                """,
                (trading_date.isoformat(), symbol),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_audit_events(
        self, *, trading_date: date, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM audit_events WHERE trading_date = ?
                ORDER BY sequence DESC LIMIT ?
                """,
                (trading_date.isoformat(), limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def set_alerts_paused(self, *, paused: bool, trading_date: date) -> None:
        value = "true" if paused else "false"
        now = _utc_now()
        with self.transaction() as connection:
            previous = connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = 'alerts_paused'"
            ).fetchone()
            if previous is not None and previous["setting_value"] == value:
                return
            connection.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES ('alerts_paused', ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (value, _iso(now)),
            )
            _audit(
                connection,
                event_type="ALERTING_PAUSED" if paused else "ALERTING_RESUMED",
                trading_date=trading_date,
                symbol=None,
                details={
                    "previous_value": (
                        previous["setting_value"] if previous is not None else "false"
                    ),
                    "new_value": value,
                    "delivery_mode": "replay_only",
                },
                occurred_at=now,
            )

    def alerts_paused(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = 'alerts_paused'"
            ).fetchone()
        return row is not None and row["setting_value"] == "true"

    def overview_counts(self, *, trading_date: date) -> dict[str, Any]:
        with self._lock:
            watchlist_count = self._connection.execute(
                "SELECT COUNT(*) FROM monitored_symbols WHERE trading_date = ? AND excluded = 0",
                (trading_date.isoformat(),),
            ).fetchone()[0]
            candle_count = self._connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT symbol, interval_start FROM candle_versions
                    WHERE trading_date = ? GROUP BY symbol, interval_start
                )
                """,
                (trading_date.isoformat(),),
            ).fetchone()[0]
            active_signal_count = self._connection.execute(
                """
                SELECT COUNT(*) FROM signal_events
                WHERE trading_date = ? AND is_current = 1 AND status = 'active'
                """,
                (trading_date.isoformat(),),
            ).fetchone()[0]
            in_app_notification_count = self._connection.execute(
                """
                SELECT COUNT(*) FROM notification_outbox n
                JOIN signal_events s ON s.event_id = n.event_id
                WHERE s.trading_date = ? AND n.channel = 'in_app'
                """,
                (trading_date.isoformat(),),
            ).fetchone()[0]
            last_candle = self._connection.execute(
                "SELECT MAX(received_at) FROM candle_versions WHERE trading_date = ?",
                (trading_date.isoformat(),),
            ).fetchone()[0]
            oi_count = self._connection.execute(
                "SELECT COUNT(*) FROM oi_observations WHERE trading_date = ?",
                (trading_date.isoformat(),),
            ).fetchone()[0]
        return {
            "watchlist_count": watchlist_count,
            "candle_interval_count": candle_count,
            "active_signal_count": active_signal_count,
            "in_app_notification_count": in_app_notification_count,
            "last_candle_import_at": last_candle,
            "oi_observation_count": oi_count,
        }

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    dataset_path: str
    dataset_name: str
    where_text: str
    executed_at: str


def format_history_timestamp(
    timestamp: str,
    local_timezone: tzinfo | None = None,
    *,
    include_timezone: bool = False,
) -> str:
    """Format a stored UTC/offset timestamp in the current system timezone."""

    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return timestamp
    if parsed.tzinfo is None:
        # Filter History has always stored UTC. Treat a legacy naive value as
        # UTC instead of guessing that it was written in the current timezone.
        parsed = parsed.replace(tzinfo=UTC)
    localized = (
        parsed.astimezone(local_timezone)
        if local_timezone is not None
        else parsed.astimezone()
    )
    display = localized.strftime("%Y-%m-%d %H:%M:%S")
    if not include_timezone:
        return display
    offset = localized.utcoffset()
    if offset is None:
        return display
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    absolute_minutes = abs(total_minutes)
    offset_text = (
        f"UTC{sign}{absolute_minutes // 60:02d}:{absolute_minutes % 60:02d}"
    )
    zone_name = localized.tzname()
    zone_text = f" {zone_name}" if zone_name else ""
    return f"{display}{zone_text} ({offset_text})"


class FilterHistory:
    def __init__(self, database_path: Path, limit: int = 500) -> None:
        self.database_path = database_path
        self.limit = limit
        self._lock = threading.RLock()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS filter_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset_path TEXT NOT NULL,
                    dataset_name TEXT NOT NULL,
                    where_text TEXT NOT NULL,
                    executed_at TEXT NOT NULL
                )"""
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(
        self, dataset_path: Path, where_text: str, when: datetime | None = None
    ) -> int | None:
        normalized = where_text.strip()
        if not normalized:
            return None
        canonical_path = str(dataset_path.resolve())
        timestamp = (when or datetime.now(UTC)).isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            latest = connection.execute(
                "SELECT dataset_path, where_text FROM filter_history ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if latest == (canonical_path, normalized):
                return None
            cursor = connection.execute(
                "INSERT INTO filter_history(dataset_path, dataset_name, where_text, executed_at) VALUES (?, ?, ?, ?)",
                (canonical_path, dataset_path.name, normalized, timestamp),
            )
            connection.execute(
                "DELETE FROM filter_history WHERE id NOT IN (SELECT id FROM filter_history ORDER BY id DESC LIMIT ?)",
                (self.limit,),
            )
            return int(cursor.lastrowid)

    def list(self, dataset_path: Path | None = None) -> list[HistoryEntry]:
        with self._lock, self._connect() as connection:
            if dataset_path is None:
                rows = connection.execute(
                    "SELECT id, dataset_path, dataset_name, where_text, executed_at FROM filter_history ORDER BY id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id, dataset_path, dataset_name, where_text, executed_at FROM filter_history "
                    "WHERE dataset_path = ? ORDER BY id DESC",
                    (str(dataset_path.resolve()),),
                ).fetchall()
        return [HistoryEntry(*row) for row in rows]

    def delete(self, entry_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM filter_history WHERE id = ?", (entry_id,))

    def clear(self, dataset_path: Path | None = None) -> None:
        with self._lock, self._connect() as connection:
            if dataset_path is None:
                connection.execute("DELETE FROM filter_history")
            else:
                connection.execute(
                    "DELETE FROM filter_history WHERE dataset_path = ?",
                    (str(dataset_path.resolve()),),
                )

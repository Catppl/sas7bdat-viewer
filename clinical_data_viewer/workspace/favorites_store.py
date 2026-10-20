from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    WorkspaceFavorite,
    normalize_favorite_label,
    normalize_workspace_path,
    workspace_path_key,
)


class WorkspaceFavoritesStore:
    """Transaction-safe, row-level persistence for favorite workspaces."""

    def __init__(self, database_path: Path, *, limit: int = 50) -> None:
        self.database_path = database_path
        self.limit = max(1, int(limit))
        self._lock = threading.RLock()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS workspace_favorites (
                    path_key TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    label TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.commit()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            yield connection
        finally:
            connection.close()

    def list_favorites(self) -> tuple[WorkspaceFavorite, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT path_key, path, label, updated_at FROM workspace_favorites"
            ).fetchall()
        favorites: list[WorkspaceFavorite] = []
        for path_key, path, label, updated_at in rows:
            if not all(
                isinstance(value, str) for value in (path_key, path, label, updated_at)
            ):
                continue
            favorites.append(WorkspaceFavorite(path_key, Path(path), label, updated_at))
        favorites.sort(
            key=lambda item: (item.label.casefold(), str(item.path).casefold())
        )
        return tuple(favorites)

    def upsert_favorite(self, path: Path, label: str = "") -> WorkspaceFavorite:
        resolved = normalize_workspace_path(path)
        key = workspace_path_key(resolved)
        normalized_label = normalize_favorite_label(label, resolved)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM workspace_favorites WHERE path_key = ?", (key,)
            ).fetchone()
            if exists is None:
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM workspace_favorites"
                    ).fetchone()[0]
                )
                if count >= self.limit:
                    connection.rollback()
                    raise ValueError(
                        f"A maximum of {self.limit} favorite workspaces is supported."
                    )
            connection.execute(
                """INSERT INTO workspace_favorites(path_key, path, label, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path_key) DO UPDATE SET
                    path = excluded.path,
                    label = excluded.label,
                    updated_at = excluded.updated_at""",
                (key, str(resolved), normalized_label, timestamp),
            )
            connection.commit()
        return WorkspaceFavorite(key, resolved, normalized_label, timestamp)

    def rename_favorite(self, path_key: str, label: str) -> None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT path FROM workspace_favorites WHERE path_key = ?", (path_key,)
            ).fetchone()
            if row is None:
                raise KeyError("Favorite workspace no longer exists.")
            normalized = normalize_favorite_label(label, Path(row[0]))
            timestamp = datetime.now(UTC).isoformat(timespec="seconds")
            with connection:
                connection.execute(
                    "UPDATE workspace_favorites SET label = ?, updated_at = ? WHERE path_key = ?",
                    (normalized, timestamp, path_key),
                )

    def remove_favorite(self, path_key: str) -> None:
        with self._lock, self._connect() as connection, connection:
            connection.execute(
                "DELETE FROM workspace_favorites WHERE path_key = ?", (path_key,)
            )

    def favorite_for_path(self, path: Path) -> WorkspaceFavorite | None:
        key = workspace_path_key(path)
        return next(
            (item for item in self.list_favorites() if item.path_key == key), None
        )

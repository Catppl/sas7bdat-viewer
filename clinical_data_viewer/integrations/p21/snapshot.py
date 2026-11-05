from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ...domain import DatasetHandle, VariableMetadata
from ...filter_engine import quote_identifier
from ...temp_manager import TempManager

SNAPSHOT_SCHEMA_VERSION = 1
STABLE_LOCATION_VARIABLES = ("STUDYID", "USUBJID")


@dataclass(frozen=True, slots=True)
class BuiltSnapshot:
    snapshot_id: str
    root: Path
    manifest_path: Path
    database_path: Path
    source_fingerprint: str
    variables: tuple[str, ...]
    domain: str


class SnapshotCancelled(RuntimeError):
    pass


class SnapshotBuilder:
    """Export a versioned, read-only-neutral subset of a Viewer dataset cache."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def build(
        self,
        source: DatasetHandle,
        domain: str,
        requested_variables: tuple[str, ...] | list[str],
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> BuiltSnapshot:
        variables = self.select_variables(source, requested_variables)
        snapshot_id = uuid.uuid4().hex
        root = self.temp_manager.session_directory / "p21-snapshots" / snapshot_id
        root.mkdir(parents=True, mode=0o700)
        database_path = root / "records.sqlite"
        manifest_path = root / "manifest.json"
        fingerprint = self.source_fingerprint(source)
        try:
            row_count = self._copy_records(
                source,
                variables,
                database_path,
                cancelled=cancelled,
                progress=progress,
            )
            manifest = {
                "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "domain": domain.upper(),
                "source_fingerprint": fingerprint,
                "source_row_count": row_count,
                "table_name": "records",
                "source_row_field": "_source_row",
                "variables": [
                    {
                        "name": variable.name,
                        "sas_type": variable.kind,
                        "sqlite_type": self._sqlite_type(variable),
                        "nullable": True,
                    }
                    for variable in variables
                ],
            }
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except BaseException:
            self.temp_manager.remove_dataset(root)
            raise
        return BuiltSnapshot(
            snapshot_id,
            root,
            manifest_path,
            database_path,
            fingerprint,
            tuple(item.name for item in variables),
            domain.upper(),
        )

    @staticmethod
    def source_fingerprint(source: DatasetHandle) -> str:
        stat = source.database_path.stat()
        value = "|".join(
            (
                str(source.source_path.resolve()),
                str(source.database_path.resolve()),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                str(source.metadata.row_count),
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def select_variables(
        source: DatasetHandle, requested: tuple[str, ...] | list[str]
    ) -> tuple[VariableMetadata, ...]:
        by_name = {item.name.casefold(): item for item in source.metadata.variables}
        names = [*requested, *STABLE_LOCATION_VARIABLES]
        selected: list[VariableMetadata] = []
        seen: set[str] = set()
        for name in names:
            variable = by_name.get(str(name).strip().casefold())
            if variable is not None and variable.name.casefold() not in seen:
                selected.append(variable)
                seen.add(variable.name.casefold())
        if not selected and source.metadata.variables:
            selected.append(source.metadata.variables[0])
        if not selected:
            raise ValueError("The selected dataset has no variables to snapshot.")
        return tuple(selected)

    @staticmethod
    def _sqlite_type(variable: VariableMetadata) -> str:
        return "REAL" if variable.kind == "numeric" else "TEXT"

    def _copy_records(
        self,
        source: DatasetHandle,
        variables: tuple[VariableMetadata, ...],
        database_path: Path,
        *,
        cancelled: Callable[[], bool] | None,
        progress: Callable[[int], None] | None,
    ) -> int:
        definitions = ", ".join(
            f"{quote_identifier(item.name)} {self._sqlite_type(item)}"
            for item in variables
        )
        columns = ", ".join(quote_identifier(item.name) for item in variables)
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database_path)
            target.execute(
                "CREATE TABLE records (_source_row INTEGER NOT NULL, "
                + definitions
                + ")"
            )
            source_uri = source.database_path.resolve().as_uri() + "?mode=ro"
            row_count = 0
            insert = (
                "INSERT INTO records VALUES ("
                + ", ".join("?" for _ in range(len(variables) + 1))
                + ")"
            )
            with closing(sqlite3.connect(source_uri, uri=True)) as input_connection:
                cursor = input_connection.execute(
                    f"SELECT _source_row, {columns} FROM dataset ORDER BY _source_row"
                )
                while rows := cursor.fetchmany(2_000):
                    if cancelled is not None and cancelled():
                        raise SnapshotCancelled()
                    target.executemany(insert, rows)
                    row_count += len(rows)
                    if progress is not None:
                        progress(row_count)
            target.execute(
                "CREATE UNIQUE INDEX records_source_row_uq ON records (_source_row)"
            )
            for position, variable in enumerate(variables, start=1):
                if cancelled is not None and cancelled():
                    raise SnapshotCancelled()
                target.execute(
                    f"CREATE INDEX records_variable_{position} ON records "
                    f"({quote_identifier(variable.name)})"
                )
            target.commit()
            target.close()
            return row_count
        except BaseException:
            if target is not None:
                target.close()
            raise


@dataclass(slots=True)
class _SnapshotEntry:
    snapshot: BuiltSnapshot
    leases: int = 1
    stale: bool = False
    last_used: int = 0


class SnapshotRegistry:
    def __init__(self, temp_manager: TempManager, *, max_cached: int = 4) -> None:
        self.temp_manager = temp_manager
        self.max_cached = max(1, int(max_cached))
        self._snapshots: dict[str, _SnapshotEntry] = {}
        self._clock = 0

    def add(self, snapshot: BuiltSnapshot) -> None:
        self._clock += 1
        self._snapshots[snapshot.snapshot_id] = _SnapshotEntry(
            snapshot, last_used=self._clock
        )
        self._evict_idle()

    def acquire(
        self,
        domain: str,
        source_fingerprint: str,
        required_variables: tuple[str, ...],
    ) -> BuiltSnapshot | None:
        required = {item.casefold() for item in required_variables}
        candidates = [
            entry
            for entry in self._snapshots.values()
            if not entry.stale
            and entry.snapshot.domain.casefold() == domain.casefold()
            and entry.snapshot.source_fingerprint == source_fingerprint
            and required.issubset(
                {item.casefold() for item in entry.snapshot.variables}
            )
        ]
        if not candidates:
            return None
        entry = min(candidates, key=lambda item: len(item.snapshot.variables))
        self._clock += 1
        entry.leases += 1
        entry.last_used = self._clock
        return entry.snapshot

    def invalidate_changed_source(self, domain: str, source_fingerprint: str) -> None:
        for snapshot_id, entry in tuple(self._snapshots.items()):
            if (
                entry.snapshot.domain.casefold() == domain.casefold()
                and entry.snapshot.source_fingerprint != source_fingerprint
            ):
                if entry.leases:
                    entry.stale = True
                else:
                    self._remove(snapshot_id)

    def release(self, snapshot_id: str) -> None:
        entry = self._snapshots.get(snapshot_id)
        if entry is None:
            return
        entry.leases = max(0, entry.leases - 1)
        if entry.stale:
            self._remove(snapshot_id)
        else:
            self._evict_idle()

    def clear(self) -> None:
        for snapshot_id in tuple(self._snapshots):
            self._remove(snapshot_id)

    def _evict_idle(self) -> None:
        while len(self._snapshots) > self.max_cached:
            idle = [
                (snapshot_id, entry)
                for snapshot_id, entry in self._snapshots.items()
                if entry.leases == 0
            ]
            if not idle:
                return
            snapshot_id, _entry = min(idle, key=lambda item: item[1].last_used)
            self._remove(snapshot_id)

    def _remove(self, snapshot_id: str) -> None:
        entry = self._snapshots.pop(snapshot_id, None)
        if entry is not None:
            self.temp_manager.remove_dataset(entry.snapshot.root)

    def __len__(self) -> int:
        return len(self._snapshots)

    def lease_count(self, snapshot_id: str) -> int | None:
        entry = self._snapshots.get(snapshot_id)
        return None if entry is None else entry.leases

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
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


class SnapshotBuilder:
    """Export a versioned, read-only-neutral subset of a Viewer dataset cache."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def build(
        self,
        source: DatasetHandle,
        domain: str,
        requested_variables: tuple[str, ...] | list[str],
    ) -> BuiltSnapshot:
        variables = self._select_variables(source, requested_variables)
        snapshot_id = uuid.uuid4().hex
        root = self.temp_manager.session_directory / "p21-snapshots" / snapshot_id
        root.mkdir(parents=True, mode=0o700)
        database_path = root / "records.sqlite"
        manifest_path = root / "manifest.json"
        fingerprint = self.source_fingerprint(source)
        try:
            row_count = self._copy_records(source, variables, database_path)
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
    def _select_variables(
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
                    target.executemany(insert, rows)
                    row_count += len(rows)
            target.execute(
                "CREATE UNIQUE INDEX records_source_row_uq ON records (_source_row)"
            )
            target.commit()
            target.close()
            return row_count
        except BaseException:
            if target is not None:
                target.close()
            raise


class SnapshotRegistry:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager
        self._snapshots: dict[str, BuiltSnapshot] = {}

    def add(self, snapshot: BuiltSnapshot) -> None:
        self._snapshots[snapshot.snapshot_id] = snapshot

    def release(self, snapshot_id: str) -> None:
        snapshot = self._snapshots.pop(snapshot_id, None)
        if snapshot is not None:
            self.temp_manager.remove_dataset(snapshot.root)

    def clear(self) -> None:
        for snapshot_id in tuple(self._snapshots):
            self.release(snapshot_id)

    def __len__(self) -> int:
        return len(self._snapshots)

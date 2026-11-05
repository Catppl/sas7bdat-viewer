from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA_VERSION = 1
VALID_SAS_TYPES = frozenset({"character", "numeric"})


class SnapshotContractError(ValueError):
    """The snapshot does not satisfy the versioned host/side-car contract."""


@dataclass(frozen=True, slots=True)
class SnapshotVariable:
    name: str
    sas_type: str
    sqlite_type: str
    nullable: bool

    @classmethod
    def from_dict(cls, value: Any) -> SnapshotVariable:
        if not isinstance(value, dict):
            raise SnapshotContractError("Each snapshot variable must be an object.")
        name = _required_text(value, "name")
        sas_type = _required_text(value, "sas_type").casefold()
        sqlite_type = _required_text(value, "sqlite_type").upper()
        nullable = value.get("nullable")
        if sas_type not in VALID_SAS_TYPES:
            raise SnapshotContractError(
                f"Variable {name!r} has unsupported SAS type {sas_type!r}."
            )
        if not isinstance(nullable, bool):
            raise SnapshotContractError(
                f"Variable {name!r} must declare boolean nullable."
            )
        return cls(
            name=name, sas_type=sas_type, sqlite_type=sqlite_type, nullable=nullable
        )


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    snapshot_schema_version: int
    snapshot_id: str
    created_at: str
    domain: str
    source_fingerprint: str
    source_row_count: int
    table_name: str
    source_row_field: str
    variables: tuple[SnapshotVariable, ...]

    @classmethod
    def read(cls, path: Path) -> SnapshotManifest:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise SnapshotContractError(
                f"Snapshot manifest not found: {path}"
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise SnapshotContractError(
                f"Cannot read snapshot manifest: {error}"
            ) from error
        if not isinstance(value, dict):
            raise SnapshotContractError("Snapshot manifest root must be an object.")
        version = value.get("snapshot_schema_version")
        if version != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotContractError(
                "Unsupported snapshot_schema_version "
                f"{version!r}; expected {SNAPSHOT_SCHEMA_VERSION}."
            )
        source_row_count = value.get("source_row_count")
        if not isinstance(source_row_count, int) or source_row_count < 0:
            raise SnapshotContractError(
                "source_row_count must be a non-negative integer."
            )
        raw_variables = value.get("variables")
        if not isinstance(raw_variables, list) or not raw_variables:
            raise SnapshotContractError("variables must be a non-empty array.")
        variables = tuple(SnapshotVariable.from_dict(item) for item in raw_variables)
        folded_names = [item.name.casefold() for item in variables]
        if len(folded_names) != len(set(folded_names)):
            raise SnapshotContractError("Snapshot variable names must be unique.")
        source_row_field = _required_text(value, "source_row_field")
        if source_row_field.casefold() in set(folded_names):
            raise SnapshotContractError(
                "source_row_field must not also appear in the variables array."
            )
        return cls(
            snapshot_schema_version=version,
            snapshot_id=_required_text(value, "snapshot_id"),
            created_at=_required_text(value, "created_at"),
            domain=_required_text(value, "domain").upper(),
            source_fingerprint=_required_text(value, "source_fingerprint"),
            source_row_count=source_row_count,
            table_name=_required_text(value, "table_name"),
            source_row_field=source_row_field,
            variables=variables,
        )

    def variable(self, name: str) -> SnapshotVariable | None:
        folded = name.casefold()
        return next(
            (item for item in self.variables if item.name.casefold() == folded), None
        )


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise SnapshotContractError(f"{key} must be a non-empty string.")
    return result.strip()

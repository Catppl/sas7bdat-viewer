from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VariableMetadata:
    name: str
    label: str = ""
    kind: str = "character"
    length: int | None = None
    format: str = ""


@dataclass(frozen=True, slots=True)
class DatasetMetadata:
    name: str
    row_count: int
    variables: tuple[VariableMetadata, ...]


@dataclass(frozen=True, slots=True)
class DatasetHandle:
    source_path: Path
    temporary_path: Path
    database_path: Path
    metadata: DatasetMetadata


@dataclass(frozen=True, slots=True)
class SortSpec:
    variable: str
    ascending: bool = True


@dataclass(frozen=True, slots=True)
class PageResult:
    rows: tuple[tuple[object, ...], ...]
    filtered_count: int

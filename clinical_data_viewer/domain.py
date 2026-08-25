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
    pair_id_column: str | None = None
    side_order_column: str | None = None
    diff_columns_column: str | None = None
    row_warning_column: str | None = None
    advanced_columns: tuple[str, ...] = ()
    export_excluded_columns: tuple[str, ...] = ()
    warning_columns: tuple[str, ...] = ()
    warning_column_messages: tuple[tuple[str, str], ...] = ()
    compare_side_column: str | None = None
    source_obs_column: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetHandle:
    source_path: Path
    temporary_path: Path
    database_path: Path
    metadata: DatasetMetadata
    cached_row_count: int = 0
    cache_complete: bool = True
    kind: str = "sas"
    display_source: str = ""


@dataclass(frozen=True, slots=True)
class SortSpec:
    variable: str
    ascending: bool = True


@dataclass(frozen=True, slots=True)
class PageResult:
    rows: tuple[tuple[object, ...], ...]
    filtered_count: int
    cell_highlights: tuple[frozenset[str], ...] = ()
    row_warnings: tuple[bool, ...] = ()


@dataclass(frozen=True, slots=True)
class CacheProgress:
    cached_rows: int
    total_rows: int
    complete: bool = False


@dataclass(frozen=True, slots=True)
class FindResult:
    row_index: int
    column_name: str


@dataclass(frozen=True, slots=True)
class DistinctValuesResult:
    values: tuple[object, ...]
    has_missing: bool
    total_distinct: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ComparedRow:
    view_row: int
    values: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class RowComparisonResult:
    rows: tuple[ComparedRow, ...]
    differing_variables: tuple[str, ...]

"""Metadata-only Excel export for every dataset in a workspace snapshot."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import xlsxwriter

from .domain import DatasetMetadata
from .excel_exporter import _write_metadata_sheet

MetadataReader = Callable[[Path], DatasetMetadata]
ProgressCallback = Callable[[str], None]

_INVALID_SHEET_CHARACTERS = re.compile(r"[\[\]:*?/\\]")


@dataclass(frozen=True, slots=True)
class WorkspaceMetadataFailure:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class WorkspaceMetadataExportResult:
    dataset_count: int
    variable_count: int
    failures: tuple[WorkspaceMetadataFailure, ...] = ()


def _safe_sheet_name(path: Path, used: set[str]) -> str:
    """Return a readable, deterministic Excel worksheet name."""

    base = _INVALID_SHEET_CHARACTERS.sub("_", path.stem).strip().strip("'")
    base = base or "Dataset"
    if base.casefold() == "history":
        base = "History_"
    candidate = base[:31]
    sequence = 2
    while candidate.casefold() in used:
        suffix = f"_{sequence}"
        candidate = f"{base[: 31 - len(suffix)]}{suffix}"
        sequence += 1
    used.add(candidate.casefold())
    return candidate


def _write_failure_sheet(
    book: xlsxwriter.Workbook,
    sheet_name: str,
    path: Path,
    message: str,
    title,
) -> None:
    sheet = book.add_worksheet(sheet_name)
    for row, (label, value) in enumerate(
        (("File", path.name), ("Status", "Metadata read failed"), ("Error", message))
    ):
        sheet.write_string(row, 0, label, title)
        sheet.write_string(row, 1, value)
    sheet.set_column(0, 0, 18)
    sheet.set_column(1, 1, 80)


class WorkspaceMetadataExporter:
    """Read source metadata sequentially and write one worksheet per file."""

    def __init__(self, metadata_reader: MetadataReader) -> None:
        self.metadata_reader = metadata_reader

    def export(
        self,
        paths: Sequence[Path],
        destination: Path,
        progress: ProgressCallback | None = None,
    ) -> WorkspaceMetadataExportResult:
        frozen_paths = tuple(Path(path) for path in paths)
        if not frozen_paths:
            raise ValueError("The workspace does not contain any SAS datasets.")
        notify = progress or (lambda _message: None)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-",
            suffix=".part.xlsx",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        failures: list[WorkspaceMetadataFailure] = []
        variable_count = 0
        used_names: set[str] = set()
        try:
            with xlsxwriter.Workbook(
                str(temporary),
                {
                    "constant_memory": True,
                    "strings_to_formulas": False,
                    "strings_to_urls": False,
                },
            ) as book:
                title = book.add_format({"bold": True, "bg_color": "#DCEAF6"})
                total = len(frozen_paths)
                for index, path in enumerate(frozen_paths, start=1):
                    notify(f"Reading metadata {index:,}/{total:,}: {path.name}")
                    sheet_name = _safe_sheet_name(path, used_names)
                    try:
                        metadata = self.metadata_reader(path)
                    except Exception as error:  # noqa: BLE001 - continue batch export
                        message = str(error) or type(error).__name__
                        failures.append(WorkspaceMetadataFailure(path, message))
                        _write_failure_sheet(book, sheet_name, path, message, title)
                        continue
                    selected = [variable.name for variable in metadata.variables]
                    _write_metadata_sheet(
                        book,
                        metadata,
                        selected,
                        title,
                        sheet_name=sheet_name,
                    )
                    variable_count += len(metadata.variables)
            os.replace(temporary, destination)
            return WorkspaceMetadataExportResult(
                dataset_count=len(frozen_paths),
                variable_count=variable_count,
                failures=tuple(failures),
            )
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

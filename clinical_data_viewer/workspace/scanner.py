from __future__ import annotations

import os
from pathlib import Path

from .models import WorkspaceEntry, WorkspaceSnapshot, normalize_workspace_path

ALLOWED_DATASET_SUFFIXES = frozenset({".sas7bdat", ".xpt"})


def scan_workspace(
    root: Path,
    allowed_suffixes: frozenset[str] = ALLOWED_DATASET_SUFFIXES,
) -> WorkspaceSnapshot:
    """Discover first-level SAS datasets without opening their contents."""

    directory = normalize_workspace_path(root)
    if not directory.exists():
        raise FileNotFoundError(f"Workspace folder does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Workspace path is not a folder: {directory}")

    suffixes = frozenset(value.casefold() for value in allowed_suffixes)
    entries: list[WorkspaceEntry] = []
    warnings: list[str] = []
    try:
        iterator = os.scandir(directory)
    except PermissionError as error:
        raise PermissionError(f"Workspace folder is not readable: {directory}") from error

    with iterator:
        for item in iterator:
            try:
                if not item.is_file(follow_symlinks=True):
                    continue
                path = Path(item.path)
                if path.suffix.casefold() not in suffixes:
                    continue
                entries.append(WorkspaceEntry(path=path, name=item.name))
            except OSError as error:
                warnings.append(f"Skipped {item.name}: {error}")

    entries.sort(key=lambda entry: (entry.name.casefold(), entry.name))
    return WorkspaceSnapshot(directory, tuple(entries), tuple(warnings))

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def normalize_workspace_path(path: Path | str) -> Path:
    """Return a stable absolute path without requiring the target to exist."""

    return Path(path).expanduser().resolve(strict=False)


def workspace_path_key(path: Path | str) -> str:
    """Return the platform-aware identity key used for open tabs and favorites."""

    value = str(normalize_workspace_path(path))
    return value.casefold() if os.name == "nt" else value


def default_workspace_label(path: Path | str) -> str:
    resolved = normalize_workspace_path(path)
    return resolved.name or str(resolved)


def normalize_favorite_label(label: str, path: Path | str) -> str:
    normalized = label.strip()[:80]
    return normalized or default_workspace_label(path)


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    path: Path
    name: str


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    root: Path
    entries: tuple[WorkspaceEntry, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceFavorite:
    path_key: str
    path: Path
    label: str
    updated_at: str

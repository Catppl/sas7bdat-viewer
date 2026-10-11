"""Workspace folder discovery and favorite persistence."""

from .favorites_store import WorkspaceFavoritesStore
from .models import WorkspaceEntry, WorkspaceFavorite, WorkspaceSnapshot
from .scanner import ALLOWED_DATASET_SUFFIXES, scan_workspace

__all__ = [
    "ALLOWED_DATASET_SUFFIXES",
    "WorkspaceEntry",
    "WorkspaceFavorite",
    "WorkspaceFavoritesStore",
    "WorkspaceSnapshot",
    "scan_workspace",
]

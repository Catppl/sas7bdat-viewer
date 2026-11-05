from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .snapshot_reader import DatasetSnapshot, SnapshotReader


class SnapshotProvider(Protocol):
    """P3 boundary that P4 can replace with the Viewer IPC client."""

    def open_snapshot(self, domain: str) -> DatasetSnapshot:
        """Open a fresh read-only snapshot for one domain."""


class MappingSnapshotProvider:
    """Standalone/fake-host provider backed by explicit domain paths."""

    def __init__(
        self,
        snapshots: Mapping[str, str | Path],
        *,
        reader: SnapshotReader | None = None,
    ) -> None:
        self._snapshots = {
            domain.upper(): Path(path).expanduser().resolve()
            for domain, path in snapshots.items()
        }
        self._reader = reader or SnapshotReader()

    @property
    def domains(self) -> tuple[str, ...]:
        return tuple(sorted(self._snapshots))

    def open_snapshot(self, domain: str) -> DatasetSnapshot:
        try:
            path = self._snapshots[domain.upper()]
        except KeyError as error:
            raise FileNotFoundError(
                f"Current data snapshot is unavailable for domain {domain}."
            ) from error
        snapshot = self._reader.open(path)
        if snapshot.manifest.domain.casefold() != domain.casefold():
            snapshot.close()
            raise ValueError(
                f"Snapshot domain {snapshot.manifest.domain!r} does not match "
                f"requested domain {domain!r}."
            )
        return snapshot

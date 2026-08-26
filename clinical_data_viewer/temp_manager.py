from __future__ import annotations

import ctypes
import os
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path


class TempManager:
    """Owns one application session directory and its dataset copies."""

    PREFIX = "cde-"

    def __init__(self, root: Path, *, now: float | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
        self.session_directory = (
            self.root / f"{self.PREFIX}{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self.session_directory.mkdir(mode=0o700)

    def cleanup_stale(
        self, max_age_seconds: float = 24 * 60 * 60, *, now: float | None = None
    ) -> list[Path]:
        cutoff = (now or time.time()) - max_age_seconds
        removed: list[Path] = []
        for candidate in self.root.glob(f"{self.PREFIX}*"):
            if candidate == self.session_directory or not candidate.is_dir():
                continue
            try:
                if candidate.stat().st_mtime >= cutoff:
                    continue
                process_id = self._session_process_id(candidate.name)
                if process_id is not None and self._process_is_running(process_id):
                    continue
                self._safe_remove(candidate)
                removed.append(candidate)
            except OSError:
                continue
        return removed

    @classmethod
    def _session_process_id(cls, directory_name: str) -> int | None:
        # cde-YYYYMMDD-HHMMSS-PID-ID
        parts = directory_name.split("-")
        if len(parts) != 5 or f"{parts[0]}-" != cls.PREFIX:
            return None
        try:
            return int(parts[3])
        except ValueError:
            return None

    @staticmethod
    def _process_is_running(process_id: int) -> bool:
        if process_id <= 0:
            return False
        if os.name == "nt":
            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, process_id
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        try:
            os.kill(process_id, 0)
            return True
        except PermissionError:
            return True
        except ProcessLookupError:
            return False

    def copy_dataset(
        self, source: Path, progress: Callable[[int, int], None] | None = None
    ) -> tuple[Path, Path]:
        source = source.resolve(strict=True)
        if source.suffix.lower() not in {".sas7bdat", ".xpt"} or not source.is_file():
            raise ValueError("Please select a valid .sas7bdat or .xpt file.")
        dataset_directory = self.session_directory / uuid.uuid4().hex
        dataset_directory.mkdir(mode=0o700)
        destination = dataset_directory / source.name
        partial = destination.with_suffix(destination.suffix + ".part")
        total = source.stat().st_size
        copied = 0
        try:
            # The source handle exists only inside this block. All later work uses destination.
            with source.open("rb") as reader, partial.open("xb") as writer:
                while True:
                    block = reader.read(4 * 1024 * 1024)
                    if not block:
                        break
                    writer.write(block)
                    copied += len(block)
                    if progress:
                        progress(copied, total)
                writer.flush()
                os.fsync(writer.fileno())
            partial.replace(destination)
            return destination, dataset_directory
        except BaseException:
            shutil.rmtree(dataset_directory, ignore_errors=True)
            raise

    def create_dataset_directory(self) -> Path:
        """Create an owned directory for a generated, session-only dataset."""
        dataset_directory = self.session_directory / uuid.uuid4().hex
        dataset_directory.mkdir(mode=0o700)
        return dataset_directory

    def remove_dataset(self, dataset_directory: Path) -> None:
        self._safe_remove(dataset_directory)

    def cleanup(self) -> None:
        self._safe_remove(self.session_directory)

    def _safe_remove(self, target: Path) -> None:
        try:
            resolved = target.resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            raise ValueError(f"Refusing to remove path outside temp root: {target}")
        if resolved == self.root:
            raise ValueError("Refusing to remove the temp root itself.")
        if not resolved.exists():
            return
        shutil.rmtree(resolved, ignore_errors=False)

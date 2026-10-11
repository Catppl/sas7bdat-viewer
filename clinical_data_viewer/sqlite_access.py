from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_ATTACH_ALIAS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def database_uri(path: Path, *, mode: str) -> str:
    """Return a percent-encoded SQLite file URI for a local database."""

    if mode not in {"ro", "rw", "rwc"}:
        raise ValueError(f'Unsupported SQLite URI mode "{mode}".')
    return f"{path.resolve().as_uri()}?mode={mode}"


def connect_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database without permitting writes."""

    _require_database_file(path, stage="open_read_only_database")
    return sqlite3.connect(database_uri(path, mode="ro"), uri=True)


def connect_result_database(path: Path) -> sqlite3.Connection:
    """Create or open a result database with SQLite URI handling enabled."""

    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(
            "create_result_database failed: the result directory does not exist."
        )
    try:
        return sqlite3.connect(database_uri(path, mode="rwc"), uri=True)
    except sqlite3.Error as error:
        raise sqlite3.OperationalError(
            f"create_result_database failed: {error}"
        ) from error


def connect_attachment_workspace() -> sqlite3.Connection:
    """Create a URI-enabled in-memory connection suitable for ATTACH."""

    return sqlite3.connect("file::memory:?cache=private", uri=True)


def attach_read_only(
    connection: sqlite3.Connection,
    alias: str,
    path: Path,
    *,
    stage: str,
) -> None:
    """Attach an existing cache read-only to a URI-enabled connection."""

    if not _ATTACH_ALIAS.fullmatch(alias):
        raise ValueError(f'Invalid SQLite database alias "{alias}".')
    _require_database_file(path, stage=stage)
    try:
        connection.execute(
            f"ATTACH DATABASE ? AS {alias}",
            (database_uri(path, mode="ro"),),
        )
    except sqlite3.Error as error:
        raise sqlite3.OperationalError(f"{stage} failed: {error}") from error


def _require_database_file(path: Path, *, stage: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(
            f"{stage} failed: the dataset cache is missing. "
            "Reopen or reload the source dataset and try again."
        )

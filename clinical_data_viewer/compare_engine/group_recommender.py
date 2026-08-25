from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier


def _normalized(value: object, kind: str) -> object:
    if value is None or (kind == "character" and value == ""):
        return None
    return value


def _frequency(
    connection: sqlite3.Connection, columns: tuple[tuple[str, str], ...]
) -> Counter[tuple[object, ...]]:
    select = ", ".join(quote_identifier(name) for name, _kind in columns)
    rows = connection.execute(f"SELECT {select} FROM dataset")
    return Counter(
        tuple(
            _normalized(value, kind)
            for value, (_name, kind) in zip(row, columns, strict=True)
        )
        for row in rows
    )


def recommend_group_variables(
    main: DatasetHandle, qc: DatasetHandle, *, limit: int = 3
) -> tuple[str, ...]:
    """Return up to ``limit`` exact value-frequency-compatible variables."""
    if limit < 1 or not main.cache_complete or not qc.cache_complete:
        return ()
    qc_by_fold = {
        variable.name.casefold(): variable for variable in qc.metadata.variables
    }
    candidates: list[tuple[VariableMetadata, VariableMetadata, int, int, int]] = []
    main_uri = main.database_path.resolve().as_uri() + "?mode=ro"
    qc_uri = qc.database_path.resolve().as_uri() + "?mode=ro"
    with (
        closing(sqlite3.connect(main_uri, uri=True)) as main_connection,
        closing(sqlite3.connect(qc_uri, uri=True)) as qc_connection,
    ):
        for position, variable in enumerate(main.metadata.variables):
            qc_variable = qc_by_fold.get(variable.name.casefold())
            if qc_variable is None or variable.kind != qc_variable.kind:
                continue
            main_freq = _frequency(main_connection, ((variable.name, variable.kind),))
            qc_freq = _frequency(qc_connection, ((qc_variable.name, variable.kind),))
            if main_freq != qc_freq:
                continue
            candidates.append(
                (
                    variable,
                    qc_variable,
                    len(main_freq),
                    max(main_freq.values(), default=0),
                    position,
                )
            )

        # Prefer variables that split observations into smaller groups. Constants
        # remain valid and can still fill an available recommendation slot.
        candidates.sort(key=lambda item: (item[2] <= 1, -item[2], item[3], item[4]))
        selected: list[tuple[VariableMetadata, VariableMetadata]] = []
        for variable, qc_variable, _distinct, _largest, _position in candidates:
            proposed = (*selected, (variable, qc_variable))
            main_columns = tuple(
                (main_variable.name, main_variable.kind)
                for main_variable, _qc_variable in proposed
            )
            qc_columns = tuple(
                (other.name, main_variable.kind) for main_variable, other in proposed
            )
            if _frequency(main_connection, main_columns) != _frequency(
                qc_connection, qc_columns
            ):
                continue
            selected.append((variable, qc_variable))
            if len(selected) >= limit:
                break
    return tuple(variable.name for variable, _qc_variable in selected)

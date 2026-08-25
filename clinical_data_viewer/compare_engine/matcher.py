from __future__ import annotations

from itertools import permutations

from .models import GroupMatchResult, MatchDecision, MatchVariable, SourceRecord
from .normalize import values_equal


def pair_cost(
    main: SourceRecord,
    qc: SourceRecord,
    variables: tuple[MatchVariable, ...],
) -> float:
    total_weight = sum(variable.weight for variable in variables)
    differences = sum(
        variable.weight
        for variable in variables
        if not values_equal(
            main.values.get(variable.name),
            qc.values.get(variable.name),
            variable.kind,
            variable.tolerance,
        )
    )
    return differences / total_weight


def _candidate_margin(costs, threshold: float) -> float | None:
    valid = sorted(float(value) for value in costs if value <= threshold)
    return valid[1] - valid[0] if len(valid) >= 2 else None


def _linear_sum_assignment(matrix: list[list[float]]):
    try:
        from scipy.optimize import linear_sum_assignment
    except ImportError:
        # Keep pure-core tests runnable in lightweight development environments.
        # Windows builds install SciPy and always use its Hungarian algorithm.
        if len(matrix) > 9:
            raise RuntimeError(
                "SciPy is required to compare this group. Install project dependencies."
            ) from None
        rows = tuple(range(len(matrix)))
        columns = min(
            permutations(rows),
            key=lambda candidate: sum(
                matrix[row][column] for row, column in zip(rows, candidate, strict=True)
            ),
        )
        return rows, columns
    return linear_sum_assignment(matrix)


def match_group(
    main_records: list[SourceRecord],
    qc_records: list[SourceRecord],
    variables: tuple[MatchVariable, ...],
    threshold: float,
    ambiguity_margin: float,
) -> GroupMatchResult:
    if not main_records or not qc_records:
        return GroupMatchResult(
            (), tuple(range(len(main_records))), tuple(range(len(qc_records)))
        )
    real = [
        [pair_cost(main, qc, variables) for qc in qc_records] for main in main_records
    ]
    main_count, qc_count = len(main_records), len(qc_records)
    size = main_count + qc_count
    impossible = 1_000_000.0
    matrix = [[impossible for _column in range(size)] for _row in range(size)]
    for row in range(main_count):
        for column in range(qc_count):
            matrix[row][column] = real[row][column]
    # A real pair at exactly the threshold must still beat two unmatched slots,
    # including the exact-match/zero-threshold case.
    unmatched_penalty = (threshold + 1e-9) / 2
    for index in range(main_count):
        matrix[index][qc_count + index] = unmatched_penalty
    for index in range(qc_count):
        matrix[main_count + index][index] = unmatched_penalty
    for row in range(main_count, size):
        for column in range(qc_count, size):
            matrix[row][column] = 0.0

    assigned_rows, assigned_columns = _linear_sum_assignment(matrix)
    decisions: list[MatchDecision] = []
    matched_main: set[int] = set()
    matched_qc: set[int] = set()
    for row, column in zip(assigned_rows, assigned_columns, strict=True):
        if row >= main_count or column >= qc_count:
            continue
        cost = float(real[row][column])
        if cost > threshold:
            continue
        row_margin = _candidate_margin(real[row], threshold)
        column_margin = _candidate_margin(
            [real[index][column] for index in range(main_count)], threshold
        )
        margins = [value for value in (row_margin, column_margin) if value is not None]
        margin = min(margins) if margins else None
        ambiguous = margin is not None and margin <= ambiguity_margin
        decisions.append(MatchDecision(row, column, cost, margin, ambiguous))
        matched_main.add(row)
        matched_qc.add(column)

    decisions.sort(key=lambda item: (item.main_index, item.qc_index))
    return GroupMatchResult(
        tuple(decisions),
        tuple(index for index in range(main_count) if index not in matched_main),
        tuple(index for index in range(qc_count) if index not in matched_qc),
    )

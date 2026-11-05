from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from p21_issue_explorer.integration import DatasetSnapshot
from p21_issue_explorer.models import ComparisonResult

from .normalization import compare_value, numeric_token_is_valid

MAX_ALIGNMENT_CANDIDATES = 4096


class AlignmentError(ValueError):
    pass


class VariableKind(StrEnum):
    LOCAL = "local_matchable"
    SEQUENCE = "sequence_ignored"
    CROSS_DOMAIN = "cross_domain_ignored"


@dataclass(frozen=True, slots=True)
class VariableSpec:
    name: str
    kind: VariableKind
    sas_type: str | None


@dataclass(frozen=True, slots=True)
class ValueMapping:
    index: int
    values: tuple[str, ...]
    score: int


@dataclass(frozen=True, slots=True)
class ClassifiedVariables:
    variables: tuple[VariableSpec, ...]
    missing_local_variables: tuple[str, ...]


def classify_variables(
    raw_variables: str, snapshot: DatasetSnapshot
) -> ClassifiedVariables:
    names = tuple(item.strip() for item in raw_variables.split(","))
    if not names or any(not name for name in names):
        raise AlignmentError("Variables contains an empty variable name.")
    variables: list[VariableSpec] = []
    missing: list[str] = []
    for name in names:
        if ":" in name:
            variables.append(VariableSpec(name, VariableKind.CROSS_DOMAIN, None))
            continue
        metadata = snapshot.variable(name)
        if name.casefold().endswith("seq"):
            variables.append(
                VariableSpec(
                    metadata.name if metadata else name,
                    VariableKind.SEQUENCE,
                    metadata.sas_type if metadata else None,
                )
            )
            continue
        if metadata is None:
            missing.append(name)
            continue
        variables.append(
            VariableSpec(metadata.name, VariableKind.LOCAL, metadata.sas_type)
        )
    return ClassifiedVariables(tuple(variables), tuple(missing))


def align_values(
    raw_values: str,
    variables: tuple[VariableSpec, ...],
    snapshot: DatasetSnapshot,
) -> tuple[ValueMapping, ...]:
    if not variables:
        raise AlignmentError("No variables are available for Values alignment.")
    raw_tokens = tuple(raw_values.split(","))
    tokens = tuple(item.strip() for item in raw_tokens)
    distinct_values = {
        variable.name: snapshot.distinct_values(variable.name)
        for variable in variables
        if variable.kind is VariableKind.LOCAL
    }
    if len(tokens) < len(variables):
        raise AlignmentError(
            f"Values has {len(tokens)} fragments for {len(variables)} variables."
        )
    if len(tokens) == len(variables) and all(
        _segment_is_valid(variable, token)
        for variable, token in zip(variables, tokens, strict=True)
    ):
        return (
            ValueMapping(
                0,
                tokens,
                _mapping_score(variables, tokens, distinct_values),
            ),
        )

    candidates: list[tuple[tuple[str, ...], int]] = []
    overflow = False

    def visit(
        variable_index: int,
        token_index: int,
        mapped: tuple[str, ...],
    ) -> None:
        nonlocal overflow
        if overflow:
            return
        remaining_variables = len(variables) - variable_index
        remaining_tokens = len(tokens) - token_index
        if remaining_tokens < remaining_variables:
            return
        if variable_index == len(variables):
            if token_index == len(tokens):
                if len(candidates) >= MAX_ALIGNMENT_CANDIDATES:
                    overflow = True
                    return
                score = _mapping_score(variables, mapped, distinct_values)
                candidates.append((mapped, score))
            return
        variable = variables[variable_index]
        maximum = remaining_tokens - (remaining_variables - 1)
        lengths = (1,) if _must_consume_one(variable) else range(1, maximum + 1)
        for length in lengths:
            if length > maximum:
                continue
            segment = ",".join(raw_tokens[token_index : token_index + length]).strip()
            if _segment_is_valid(variable, segment):
                visit(variable_index + 1, token_index + length, (*mapped, segment))

    visit(0, 0, ())
    if overflow:
        raise AlignmentError(
            "Values alignment exceeded the safe candidate limit; evaluation was stopped "
            "without truncating mappings."
        )
    if not candidates:
        raise AlignmentError(
            "Values cannot be aligned to Variables using current metadata."
        )
    scored_values = sorted(
        {(mapping, score) for mapping, score in candidates},
        key=lambda item: (-item[1], item[0]),
    )
    return tuple(
        ValueMapping(index, values, score)
        for index, (values, score) in enumerate(scored_values)
    )


def _must_consume_one(variable: VariableSpec) -> bool:
    return variable.kind is VariableKind.SEQUENCE or (
        variable.kind is VariableKind.LOCAL and variable.sas_type == "numeric"
    )


def _segment_is_valid(variable: VariableSpec, value: str) -> bool:
    if variable.kind is VariableKind.LOCAL and variable.sas_type == "numeric":
        return numeric_token_is_valid(value)
    if variable.kind is VariableKind.SEQUENCE and variable.sas_type == "numeric":
        return numeric_token_is_valid(value)
    return True


def _mapping_score(
    variables: tuple[VariableSpec, ...],
    values: tuple[str, ...],
    distinct_values: dict[str, tuple[object, ...]],
) -> int:
    score = 0
    for variable, expected in zip(variables, values, strict=True):
        if variable.kind is not VariableKind.LOCAL or variable.sas_type is None:
            continue
        best = 0
        for actual in distinct_values[variable.name]:
            result, _reason = compare_value(expected, actual, variable.sas_type)
            if result is ComparisonResult.STRICT:
                best = 2
                break
            if result is ComparisonResult.LENIENT:
                best = max(best, 1)
        score += best
    return score

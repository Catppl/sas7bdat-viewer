from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MatchVariable:
    name: str
    kind: str
    weight: float = 1.0
    tolerance: float = 0.0


@dataclass(frozen=True, slots=True)
class CompareConfig:
    group_variables: tuple[str, ...]
    match_variables: tuple[MatchVariable, ...]
    key_variables: tuple[str, ...] = ()
    threshold: float = 0.5
    ambiguity_margin: float = 0.05
    max_group_pairs: int = 1_000_000
    max_group_records: int = 2_000

    def validate(self) -> None:
        if not self.group_variables:
            raise ValueError("Select at least one Group Variable.")
        if not self.match_variables:
            raise ValueError("Select at least one Match Variable.")
        if not 0 <= self.threshold <= 1:
            raise ValueError("Match threshold must be between 0 and 1.")
        if not 0 <= self.ambiguity_margin <= 1:
            raise ValueError("Ambiguity margin must be between 0 and 1.")
        if self.max_group_pairs < 1:
            raise ValueError("Maximum group combinations must be positive.")
        if self.max_group_records < 2:
            raise ValueError("Maximum group records must be at least two.")
        if any(variable.weight <= 0 for variable in self.match_variables):
            raise ValueError("Match variable weights must be greater than zero.")
        if any(variable.tolerance < 0 for variable in self.match_variables):
            raise ValueError("Numeric tolerances cannot be negative.")


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_row: int
    values: dict[str, object]


@dataclass(frozen=True, slots=True)
class MatchDecision:
    main_index: int
    qc_index: int
    cost: float
    margin: float | None
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class GroupMatchResult:
    decisions: tuple[MatchDecision, ...]
    unmatched_main: tuple[int, ...]
    unmatched_qc: tuple[int, ...]

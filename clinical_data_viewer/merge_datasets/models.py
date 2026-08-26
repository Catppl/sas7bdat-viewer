from __future__ import annotations

from dataclasses import dataclass

from ..domain import DatasetHandle

JOIN_TYPES = ("left", "right", "inner", "full")
SORT_DIRECTIONS = ("ASC", "DESC")


@dataclass(frozen=True, slots=True)
class MergeSortItem:
    variable: str
    direction: str = "ASC"

    def validate(self) -> None:
        if not self.variable.strip():
            raise ValueError("Sort variable cannot be empty.")
        if self.direction not in SORT_DIRECTIONS:
            raise ValueError("Sort direction must be ASC or DESC.")


@dataclass(frozen=True, slots=True)
class MergeDatasetsConfig:
    """Engine-only merge configuration; it intentionally contains no widgets."""

    by_variables: tuple[str, ...]
    join_type: str = "left"
    sort_by: tuple[MergeSortItem, ...] = ()

    def validate(self) -> None:
        if not self.by_variables:
            raise ValueError("Select at least one BY variable.")
        if self.join_type not in JOIN_TYPES:
            raise ValueError(f"Unsupported join type: {self.join_type}")
        seen: set[str] = set()
        for item in self.sort_by:
            if not isinstance(item, MergeSortItem):
                raise TypeError("Sort configuration contains an invalid item.")
            item.validate()
            key = item.variable.casefold()
            if key in seen:
                raise ValueError(f'Duplicate sort variable: "{item.variable}".')
            seen.add(key)


@dataclass(frozen=True, slots=True)
class MergeSummary:
    left_rows: int
    right_rows: int
    left_duplicate_keys: int = 0
    right_duplicate_keys: int = 0
    many_to_many_keys: int = 0
    merged_rows: int = 0
    matched_rows: int = 0
    left_only_rows: int = 0
    right_only_rows: int = 0

    @property
    def many_to_many(self) -> bool:
        return self.many_to_many_keys > 0


@dataclass(frozen=True, slots=True)
class MergeResult:
    handle: DatasetHandle
    summary: MergeSummary

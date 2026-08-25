from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import DatasetMetadata
from ..filter_engine import CompiledFilter
from ..settings import PROC_MEANS_STATISTICS

STATISTIC_COLUMN_NAMES = {
    "subjects": "SUBJECT_N",
    "n": "N",
    "nmiss": "NMISS",
    "mean": "MEAN",
    "std": "SD",
    "stderr": "SE",
    "median": "MEDIAN",
    "q1": "Q1",
    "q3": "Q3",
    "min": "MIN",
    "max": "MAX",
    "lclm": "LCLM",
    "uclm": "UCLM",
}
COUNT_STATISTICS = {"subjects", "n", "nmiss"}


@dataclass(frozen=True, slots=True)
class ProcMeansConfig:
    analysis_variables: tuple[str, ...]
    by_variables: tuple[str, ...] = ()
    class_variables: tuple[str, ...] = ()
    statistics: tuple[str, ...] = ("n", "mean", "std", "median", "min", "max")
    compiled_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    filter_text: str = ""
    decimal_group_variables: tuple[str, ...] = ()
    decimal_offsets: tuple[tuple[str, int], ...] = ()
    confidence: float = 0.95

    @property
    def group_variables(self) -> tuple[str, ...]:
        return (*self.by_variables, *self.class_variables)

    def validate(self, metadata: DatasetMetadata) -> None:
        if not self.analysis_variables:
            raise ValueError("Select at least one Analysis Variable.")
        if not self.statistics:
            raise ValueError("Select at least one statistic.")
        by_fold = [name.casefold() for name in self.by_variables]
        class_fold = [name.casefold() for name in self.class_variables]
        analysis_fold = [name.casefold() for name in self.analysis_variables]
        if len(set(by_fold)) != len(by_fold):
            raise ValueError("BY Variables contain duplicates.")
        if len(set(class_fold)) != len(class_fold):
            raise ValueError("CLASS Variables contain duplicates.")
        if len(set(analysis_fold)) != len(analysis_fold):
            raise ValueError("Analysis Variables contain duplicates.")
        if set(by_fold) & set(class_fold):
            raise ValueError("A variable cannot be both a BY and CLASS Variable.")
        if set(analysis_fold) & (set(by_fold) | set(class_fold)):
            raise ValueError(
                "A variable cannot be both an Analysis and grouping variable."
            )
        known = {variable.name.casefold(): variable for variable in metadata.variables}
        requested = (*self.analysis_variables, *self.group_variables)
        unknown = [name for name in requested if name.casefold() not in known]
        if unknown:
            raise ValueError("Unknown variables: " + ", ".join(unknown))
        nonnumeric = [
            known[name.casefold()].name
            for name in self.analysis_variables
            if known[name.casefold()].kind != "numeric"
        ]
        if nonnumeric:
            raise ValueError(
                "Analysis Variables must be numeric: " + ", ".join(nonnumeric)
            )
        allowed_statistics = {key for key, _label in PROC_MEANS_STATISTICS}
        invalid_statistics = [
            statistic
            for statistic in self.statistics
            if statistic not in allowed_statistics
        ]
        if invalid_statistics:
            raise ValueError("Unknown statistics: " + ", ".join(invalid_statistics))
        decimal_fold = [name.casefold() for name in self.decimal_group_variables]
        if len(set(decimal_fold)) != len(decimal_fold):
            raise ValueError("Decimal Group Variables contain duplicates.")
        invalid_decimal_groups = [
            name
            for name in self.decimal_group_variables
            if name.casefold()
            not in {group.casefold() for group in self.group_variables}
        ]
        if invalid_decimal_groups:
            raise ValueError(
                "Decimal Group Variables must also be selected as BY or CLASS "
                "Variables: " + ", ".join(invalid_decimal_groups)
            )
        if not 0 < self.confidence < 1:
            raise ValueError("Confidence level must be between 0 and 1.")
        offsets = dict(self.decimal_offsets)
        if any(value < 0 or value > 4 for value in offsets.values()):
            raise ValueError("Decimal offsets must be between +0 and +4.")

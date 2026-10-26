from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import DatasetMetadata
from ..filter_engine import CompiledFilter

_STATISTICS = ("n", "mean", "std", "median", "q1", "q3", "min", "max")
DECIMAL_STATISTICS = ("mean", "std", "median", "q1", "q3", "min", "max")
DEFAULT_STATISTIC_DECIMALS = (
    ("mean", 1),
    ("std", 2),
    ("median", 1),
    ("q1", 1),
    ("q3", 1),
    ("min", 0),
    ("max", 0),
)


@dataclass(frozen=True, slots=True)
class ChangeFromBaselineConfig:
    """Editable configuration for the existing-CHG horizontal table."""

    change_variable: str
    item_variable: str
    row_variables: tuple[str, ...] = ()
    treatment_variable: str = ""
    row_sort: tuple[tuple[str, bool], ...] = ()
    statistics: tuple[str, ...] = _STATISTICS
    data_filter: CompiledFilter = field(default_factory=lambda: CompiledFilter("", ()))
    data_filter_text: str = ""
    postbaseline_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    postbaseline_filter_text: str = ""
    item_labels: tuple[tuple[str, str], ...] = ()
    include_total: bool = True
    statistic_decimals: tuple[tuple[str, int], ...] = DEFAULT_STATISTIC_DECIMALS

    def validate(self, metadata: DatasetMetadata) -> None:
        if not self.change_variable:
            raise ValueError("Select a Change Variable.")
        if not self.item_variable:
            raise ValueError("Select an Item Variable.")
        known = {variable.name.casefold(): variable for variable in metadata.variables}

        def require(name: str, role: str) -> None:
            if not name or name.casefold() not in known:
                raise ValueError(f'{role} "{name}" does not exist in the dataset.')

        require(self.change_variable, "Change Variable")
        require(self.item_variable, "Item Variable")
        change = known[self.change_variable.casefold()]
        if change.kind != "numeric":
            raise ValueError("Change Variable must be numeric.")
        require("USUBJID", "Subject ID variable")
        if self.treatment_variable:
            require(self.treatment_variable, "Treatment variable")
        row_fold = [name.casefold() for name in self.row_variables]
        if len(row_fold) != len(set(row_fold)):
            raise ValueError("Row Variables contain duplicates.")
        for name in self.row_variables:
            require(name, "Row Variable")
        if self.treatment_variable.casefold() in row_fold:
            # This is permitted because users may intentionally show treatment
            # on the left; the controller will not also create treatment columns.
            pass
        sort_names = {name.casefold() for name in self.row_variables}
        seen_sort: set[str] = set()
        for name, ascending in self.row_sort:
            if name.casefold() not in sort_names:
                raise ValueError("Row sort variables must be selected Row Variables.")
            if name.casefold() in seen_sort:
                raise ValueError("Row sort variables contain duplicates.")
            if not isinstance(ascending, bool):
                raise TypeError("Row sort direction must be ascending or descending.")
            seen_sort.add(name.casefold())
        unknown_stats = [key for key in self.statistics if key not in _STATISTICS]
        if unknown_stats:
            raise ValueError(
                "Unknown Change from Baseline statistics: " + ", ".join(unknown_stats)
            )
        if not self.statistics:
            raise ValueError("Select at least one statistic.")
        decimal_names = [key.casefold() for key, _decimals in self.statistic_decimals]
        if len(decimal_names) != len(set(decimal_names)):
            raise ValueError("Statistic decimal settings contain duplicates.")
        invalid_decimal_statistics = [
            key
            for key, _decimals in self.statistic_decimals
            if key.casefold() not in DECIMAL_STATISTICS
        ]
        if invalid_decimal_statistics:
            raise ValueError(
                "Unknown statistic decimal settings: "
                + ", ".join(invalid_decimal_statistics)
            )
        if any(
            isinstance(decimals, bool)
            or not isinstance(decimals, int)
            or not 0 <= decimals <= 4
            for _key, decimals in self.statistic_decimals
        ):
            raise ValueError("Statistic decimal places must be between 0 and 4.")
        label_keys = [key.casefold() for key, _label in self.item_labels]
        if len(label_keys) != len(set(label_keys)):
            raise ValueError("Item labels contain duplicate level values.")


__all__ = [
    "DECIMAL_STATISTICS",
    "DEFAULT_STATISTIC_DECIMALS",
    "ChangeFromBaselineConfig",
]

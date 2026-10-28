from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import DatasetMetadata
from ..filter_engine import CompiledFilter
from ..settings import PROC_MEANS_STATISTICS


@dataclass(frozen=True, slots=True)
class ProcMeansVerticalConfig:
    analysis_variables: tuple[str, ...]
    group_variables: tuple[str, ...] = ()
    treatment_variable: str = ""
    population_treatment_variable: str = ""
    statistics: tuple[str, ...] = (
        "n",
        "mean",
        "std",
        "median",
        "q1",
        "q3",
        "min",
        "max",
    )
    compiled_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    filter_text: str = ""
    population_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    population_filter_text: str = ""
    item_labels: tuple[tuple[str, str], ...] = ()
    include_total: bool = True
    statistic_decimals: tuple[tuple[str, int], ...] = ()
    confidence: float = 0.95
    base_decimals: tuple[tuple[str, int | None], ...] = ()
    output_layout: str = "columns"
    row_group_variables: tuple[str, ...] = ()

    def validate(
        self, metadata: DatasetMetadata, population: DatasetMetadata | None = None
    ) -> None:
        if not self.analysis_variables:
            raise ValueError("Select at least one Analysis Variable.")
        if not self.statistics:
            raise ValueError("Select at least one statistic.")
        if self.output_layout not in {"columns", "rows"}:
            raise ValueError(
                "Output layout must be Groups as Columns or Groups as Rows."
            )
        if not 0 < self.confidence < 1:
            raise ValueError("Confidence level must be between 0 and 1.")
        known = {variable.name.casefold(): variable for variable in metadata.variables}
        if "usubjid" not in known:
            raise ValueError(
                "USUBJID is required for PROC MEANS Table Builder (Vertical)."
            )
        requested = (
            *self.analysis_variables,
            *self.group_variables,
            *self.row_group_variables,
        )
        unknown = [name for name in requested if name.casefold() not in known]
        if unknown:
            raise ValueError("Unknown variables: " + ", ".join(unknown))
        duplicate_names = [name.casefold() for name in requested]
        if len(duplicate_names) != len(set(duplicate_names)):
            raise ValueError(
                "Analysis, column-group, and row-group variables contain duplicates."
            )
        if self.output_layout == "rows" and not self.row_group_variables:
            raise ValueError(
                "Select at least one Row Group Variable for Groups as Rows."
            )
        if self.output_layout == "columns" and self.row_group_variables:
            raise ValueError("Row Group Variables require the Groups as Rows layout.")
        analysis_names = {name.casefold() for name in self.analysis_variables}
        unknown_labels = [
            name
            for name, label in self.item_labels
            if name.casefold() not in analysis_names or not label.strip()
        ]
        if unknown_labels:
            raise ValueError(
                "Item labels must reference selected Analysis Variables: "
                + ", ".join(unknown_labels)
            )
        nonnumeric = [
            known[name.casefold()].name
            for name in self.analysis_variables
            if known[name.casefold()].kind != "numeric"
        ]
        if nonnumeric:
            raise ValueError(
                "Analysis Variables must be numeric: " + ", ".join(nonnumeric)
            )
        if self.treatment_variable:
            if self.treatment_variable.casefold() not in known:
                raise ValueError(
                    f'Treatment Variable "{self.treatment_variable}" does not exist.'
                )
            if self.treatment_variable.casefold() not in {
                name.casefold() for name in self.group_variables
            }:
                raise ValueError(
                    "Treatment Variable must be selected as a Group Variable."
                )
        allowed = {key for key, _label in PROC_MEANS_STATISTICS}
        invalid = [key for key in self.statistics if key not in allowed]
        if invalid:
            raise ValueError("Unknown statistics: " + ", ".join(invalid))
        decimal = {key.casefold(): value for key, value in self.statistic_decimals}
        if any(key not in allowed for key in decimal):
            raise ValueError("Unknown statistic decimal setting.")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4
            for value in decimal.values()
        ):
            raise ValueError("Statistic decimal places must be between 0 and 4.")
        base = {key.casefold(): value for key, value in self.base_decimals}
        if len(base) != len(self.base_decimals):
            raise ValueError(
                "Base decimal settings must contain each Analysis Variable only once."
            )
        if any(key not in analysis_names for key in base):
            raise ValueError(
                "Base decimal settings must reference selected Analysis Variables."
            )
        if any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 4
            )
            for value in base.values()
        ):
            raise ValueError("Base decimal places must be Auto or between 0 and 4.")
        if population is not None and self.population_treatment_variable:
            population_known = {
                variable.name.casefold() for variable in population.variables
            }
            if self.population_treatment_variable.casefold() not in population_known:
                raise ValueError(
                    "Population Treatment Variable does not exist in the Population Dataset."
                )
        if (
            population is not None
            and self.treatment_variable
            and not self.population_treatment_variable
        ):
            raise ValueError(
                "Select a Population Treatment Variable when a treatment group is used."
            )

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from ..domain import DatasetMetadata, VariableMetadata
from ..filter_engine import CompiledFilter


@dataclass(frozen=True, slots=True)
class OrderedLevel:
    raw_value: object
    display_label: str
    order: int


def _typed_key(value: object, variable: VariableMetadata) -> object:
    if value is None or (variable.kind == "character" and value == ""):
        raise ValueError(f"{variable.name} levels cannot contain missing values.")
    if variable.kind == "numeric":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{variable.name} levels must use numeric raw values.")
        numeric = float(value)
        if not isfinite(numeric):
            raise ValueError(f"{variable.name} levels must be finite.")
        return numeric
    if not isinstance(value, str):
        raise TypeError(f"{variable.name} levels must use character raw values.")
    return value.casefold()


def _validate_levels(
    label: str,
    levels: tuple[OrderedLevel, ...],
    variable: VariableMetadata,
) -> None:
    if not levels:
        raise ValueError(f"Select at least one {label}.")
    raw_keys = [_typed_key(level.raw_value, variable) for level in levels]
    if len(raw_keys) != len(set(raw_keys)):
        raise ValueError(f"{label} raw values contain duplicates.")
    orders = [level.order for level in levels]
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in orders
    ):
        raise ValueError(f"{label} orders must be positive integers.")
    if len(orders) != len(set(orders)):
        raise ValueError(f"{label} orders contain duplicates.")
    if any(not level.display_label.strip() for level in levels):
        raise ValueError(f"{label} display labels cannot be empty.")


@dataclass(frozen=True, slots=True)
class ShiftTableConfig:
    subject_variable: str
    parameter_variable: str
    visit_variable: str
    treatment_variable: str
    baseline_grade_variable: str
    post_grade_variable: str
    analysis_flag_variable: str
    analysis_flag_value: object
    parameters: tuple[OrderedLevel, ...]
    visits: tuple[OrderedLevel, ...]
    treatments: tuple[OrderedLevel, ...]
    grade_levels: tuple[OrderedLevel, ...]
    compiled_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    filter_text: str = ""
    include_overall_total: bool = False
    percent_digits: int = 1
    population_subject_variable: str = "USUBJID"
    population_treatment_variable: str = ""
    population_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    population_filter_text: str = ""
    allow_duplicate_records: bool = False

    def validate(
        self,
        metadata: DatasetMetadata,
        population_metadata: DatasetMetadata | None = None,
    ) -> None:
        fields = {variable.name.casefold(): variable for variable in metadata.variables}
        role_names = {
            "Subject Variable": self.subject_variable,
            "Parameter Variable": self.parameter_variable,
            "Visit Variable": self.visit_variable,
            "Treatment Variable": self.treatment_variable,
            "Baseline Grade Variable": self.baseline_grade_variable,
            "Post-baseline Grade Variable": self.post_grade_variable,
            "Analysis Flag Variable": self.analysis_flag_variable,
        }
        resolved: dict[str, VariableMetadata] = {}
        for label, name in role_names.items():
            variable = fields.get(name.strip().casefold())
            if variable is None:
                raise ValueError(f'{label} "{name}" does not exist.')
            resolved[label] = variable
        if len({variable.name.casefold() for variable in resolved.values()}) != len(
            resolved
        ):
            raise ValueError("Shift Table variable roles must use different variables.")

        flag = resolved["Analysis Flag Variable"]
        _typed_key(self.analysis_flag_value, flag)
        parameter = resolved["Parameter Variable"]
        visit = resolved["Visit Variable"]
        treatment = resolved["Treatment Variable"]
        baseline = resolved["Baseline Grade Variable"]
        post = resolved["Post-baseline Grade Variable"]
        if baseline.kind != post.kind:
            raise ValueError(
                "Baseline Grade and Post-baseline Grade variables must have the same storage type."
            )
        _validate_levels("Parameter levels", self.parameters, parameter)
        _validate_levels("Visit levels", self.visits, visit)
        _validate_levels("Treatment levels", self.treatments, treatment)
        _validate_levels("Grade levels", self.grade_levels, baseline)
        for level in self.grade_levels:
            _typed_key(level.raw_value, post)
        if not 0 <= self.percent_digits <= 4:
            raise ValueError("Percent decimal places must be between 0 and 4.")

        population = population_metadata or metadata
        population_fields = {
            variable.name.casefold(): variable for variable in population.variables
        }
        population_subject = population_fields.get(
            self.population_subject_variable.casefold()
        )
        if population_subject is None:
            raise ValueError(
                f'Population Subject Variable "{self.population_subject_variable}" does not exist.'
            )
        population_treatment_name = (
            self.population_treatment_variable or self.treatment_variable
        )
        population_treatment = population_fields.get(
            population_treatment_name.casefold()
        )
        if population_treatment is None:
            raise ValueError(
                f'Population Treatment Variable "{population_treatment_name}" does not exist.'
            )
        for level in self.treatments:
            _typed_key(level.raw_value, population_treatment)


__all__ = ["OrderedLevel", "ShiftTableConfig"]

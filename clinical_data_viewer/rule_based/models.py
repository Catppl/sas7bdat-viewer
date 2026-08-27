from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import DatasetMetadata
from ..filter_engine import CompiledFilter


@dataclass(frozen=True, slots=True)
class RuleBasedRow:
    """One independently filtered row in a rule-based table."""

    row_id: str
    item: str
    row_filter: CompiledFilter = field(default_factory=lambda: CompiledFilter("", ()))
    row_filter_text: str = ""
    indent: int = 0


@dataclass(frozen=True, slots=True)
class RuleBasedDenominator:
    """Table-level denominator; its filter is independent of row filters."""

    type: str = "same_universe"  # population | nonmissing | same_universe
    population_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    population_filter_text: str = ""
    analysis_value_variable: str = ""
    # Population N may use a differently named treatment variable in ADSL
    # (for example, TRT01AN versus TRTAN in an AE source dataset).
    population_treatment_variable: str = ""


@dataclass(frozen=True, slots=True)
class RuleBasedConfig:
    rows: tuple[RuleBasedRow, ...]
    treatment_variable: str
    subject_id_variable: str = "USUBJID"
    dataset_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    dataset_filter_text: str = ""
    denominator: RuleBasedDenominator = field(default_factory=RuleBasedDenominator)
    include_total: bool = True
    percent_digits: int = 1

    def validate(
        self,
        source: DatasetMetadata,
        population: DatasetMetadata | None = None,
    ) -> None:
        if not self.rows:
            raise ValueError("Add at least one Rule-based Table row.")
        if self.denominator.type not in {"population", "nonmissing", "same_universe"}:
            raise ValueError("Select a valid Rule-based denominator.")
        if self.percent_digits < 0 or self.percent_digits > 4:
            raise ValueError("Percent decimal digits must be between 0 and 4.")
        fields = {variable.name.casefold(): variable for variable in source.variables}

        def require_source(name: str, label: str) -> None:
            if not name or name.casefold() not in fields:
                raise ValueError(f'{label} "{name}" does not exist in the source dataset.')

        require_source(self.treatment_variable, "Treatment variable")
        require_source(self.subject_id_variable, "Subject ID variable")
        seen: set[str] = set()
        for row in self.rows:
            if not row.item.strip():
                raise ValueError("Every Rule-based Table row needs an Item.")
            if row.row_id.casefold() in seen:
                raise ValueError(f'Duplicate Rule-based row ID "{row.row_id}".')
            seen.add(row.row_id.casefold())
            if row.indent < 0 or row.indent > 8:
                raise ValueError("Indent must be between 0 and 8.")
            # The compiled filter is created by the UI against the current
            # source metadata; validating its text separately would duplicate
            # the existing WHERE parser.
        if self.denominator.type == "nonmissing":
            require_source(self.denominator.analysis_value_variable, "Analysis value variable")
        if self.denominator.type == "population":
            if population is None:
                raise ValueError("Open or browse an ADSL dataset for Population N.")
            population_fields = {
                variable.name.casefold(): variable for variable in population.variables
            }
            population_treatment = (
                self.denominator.population_treatment_variable
                or self.treatment_variable
            )
            for name, label in (
                (population_treatment, "Population treatment variable"),
                (self.subject_id_variable, "Subject ID variable"),
            ):
                if name.casefold() not in population_fields:
                    raise ValueError(f'Population N requires {label} "{name}" in ADSL.')
                population_variable = population_fields[name.casefold()]
                source_variable = (
                    fields[self.treatment_variable.casefold()]
                    if label == "Population treatment variable"
                    else fields[name.casefold()]
                )
                if source_variable.kind != population_variable.kind:
                    raise ValueError(
                        f'Population N requires {label} "{name}" to have the same type in source and ADSL.'
                    )

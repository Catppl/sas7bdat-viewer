from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import DatasetMetadata
from ..filter_engine import CompiledFilter


@dataclass(frozen=True, slots=True)
class CategoricalItem:
    """One categorical variable and its optional analysis context."""

    variable: str
    label: str = ""
    context_variables: tuple[str, ...] = ()
    include_missing_level: bool = False
    level_order: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class DenominatorConfig:
    """The denominator is deliberately independent of numerator counting."""

    type: str = "nonmissing"  # population | nonmissing | baseline_postbaseline
    analysis_value_variable: str = ""
    population_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    population_filter_text: str = ""
    baseline_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    baseline_filter_text: str = ""
    postbaseline_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    postbaseline_filter_text: str = ""


@dataclass(frozen=True, slots=True)
class CategoricalConfig:
    items: tuple[CategoricalItem, ...]
    treatment_variable: str
    subject_id_variable: str
    count_type: str = "distinct_subject"  # distinct_subject | record
    source_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    source_filter_text: str = ""
    denominator: DenominatorConfig = field(default_factory=DenominatorConfig)
    include_total: bool = True
    percent_digits: int = 1

    def validate(
        self,
        source: DatasetMetadata,
        population: DatasetMetadata | None = None,
    ) -> None:
        if not self.items:
            raise ValueError("Select at least one categorical Item.")
        if self.count_type not in {"distinct_subject", "record"}:
            raise ValueError("Count type must be Distinct subjects or Records.")
        if self.denominator.type not in {
            "population",
            "nonmissing",
            "baseline_postbaseline",
        }:
            raise ValueError("Select a valid denominator type.")
        if (
            self.denominator.type == "baseline_postbaseline"
            and self.count_type != "record"
        ):
            raise ValueError(
                "Baseline + Postbaseline n1 uses record count; distinct-subject counting is not available for this denominator."
            )
        if self.percent_digits < 0 or self.percent_digits > 4:
            raise ValueError("Percent decimal digits must be between 0 and 4.")
        known = {variable.name.casefold(): variable for variable in source.variables}

        def source_field(name: str, role: str) -> None:
            if not name or name.casefold() not in known:
                raise ValueError(f'{role} "{name}" does not exist in the source dataset.')

        source_field(self.treatment_variable, "Treatment variable")
        if self.count_type == "distinct_subject" or self.denominator.type == "baseline_postbaseline":
            source_field(self.subject_id_variable, "Subject ID variable")
        item_names: set[str] = set()
        for item in self.items:
            source_field(item.variable, "Item variable")
            folded = item.variable.casefold()
            if folded in item_names:
                raise ValueError(f'Item variable "{item.variable}" is selected twice.')
            item_names.add(folded)
            contexts = [name.casefold() for name in item.context_variables]
            if len(contexts) != len(set(contexts)):
                raise ValueError(f'Item "{item.variable}" has duplicate context variables.')
            for context in item.context_variables:
                source_field(context, "Context variable")
        denom = self.denominator
        if denom.type == "population":
            if population is None:
                raise ValueError("Open or browse an ADSL dataset for Population N.")
            population_known = {
                variable.name.casefold(): variable for variable in population.variables
            }
            required = [self.treatment_variable]
            if self.count_type == "distinct_subject":
                required.append(self.subject_id_variable)
            required.extend(
                context for item in self.items for context in item.context_variables
            )
            unknown = [
                name for name in required if name.casefold() not in population_known
            ]
            if unknown:
                raise ValueError(
                    "Population N requires these variables in ADSL: "
                    + ", ".join(dict.fromkeys(unknown))
                )
        elif denom.type == "nonmissing":
            source_field(denom.analysis_value_variable, "Non-missing analysis value")
        else:
            source_field(denom.analysis_value_variable, "n1 analysis value")
            if not denom.baseline_filter.sql or not denom.postbaseline_filter.sql:
                raise ValueError(
                    "Baseline + Postbaseline n1 requires both baseline and postbaseline WHERE conditions."
                )

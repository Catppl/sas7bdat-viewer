from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import DatasetMetadata
from ..filter_engine import CompiledFilter


@dataclass(frozen=True, slots=True)
class AeTableDenominator:
    type: str = "same_universe"  # same_universe | population
    population_filter: CompiledFilter = field(default_factory=lambda: CompiledFilter("", ()))
    population_filter_text: str = ""


@dataclass(frozen=True, slots=True)
class AeTableConfig:
    soc_variable: str
    pt_variable: str
    treatment_variable: str
    subject_id_variable: str = "USUBJID"
    dataset_filter: CompiledFilter = field(default_factory=lambda: CompiledFilter("", ()))
    dataset_filter_text: str = ""
    denominator: AeTableDenominator = field(default_factory=AeTableDenominator)
    include_any_ae: bool = True
    any_ae_label: str = "Any AE"
    include_total: bool = True
    percent_digits: int = 1

    def validate(self, source: DatasetMetadata, population: DatasetMetadata | None = None) -> None:
        if self.denominator.type not in {"same_universe", "population"}:
            raise ValueError("Select a valid AE Table denominator.")
        if not 0 <= self.percent_digits <= 4:
            raise ValueError("Percent decimal digits must be between 0 and 4.")
        fields = {v.name.casefold(): v for v in source.variables}
        for name, label in ((self.soc_variable, "SOC variable"), (self.pt_variable, "PT variable"),
                            (self.treatment_variable, "Treatment variable"),
                            (self.subject_id_variable, "Subject ID variable")):
            if not name or name.casefold() not in fields:
                raise ValueError(f'{label} "{name}" does not exist in the source dataset.')
        if self.denominator.type == "population":
            if population is None:
                raise ValueError("Open or browse an ADSL dataset for Population N.")
            pfields = {v.name.casefold(): v for v in population.variables}
            for name, label in ((self.treatment_variable, "Treatment variable"),
                                (self.subject_id_variable, "Subject ID variable")):
                if name.casefold() not in pfields:
                    raise ValueError(f'Population N requires {label} "{name}" in ADSL.')
                if fields[name.casefold()].kind != pfields[name.casefold()].kind:
                    raise ValueError(f'Population N requires {label} "{name}" to have the same type in source and ADSL.')

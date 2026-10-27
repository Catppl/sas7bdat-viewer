from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import DatasetMetadata, VariableMetadata
from ..filter_engine import CompiledFilter

CONTINUOUS_STATISTICS = (
    "n",
    "mean",
    "std",
    "median",
    "q1",
    "q3",
    "min",
    "max",
)


@dataclass(frozen=True, slots=True)
class BaselineCategoricalItem:
    variable: str
    label: str = ""
    level_sort_method: str = "runtime_value_ascending"
    level_order: tuple[object, ...] = ()
    percent_digits: int = 1


@dataclass(frozen=True, slots=True)
class BaselineContinuousItem:
    variable: str
    label: str = ""
    unit: str = ""
    statistic_decimals: tuple[tuple[str, int | None], ...] = ()


BaselineItem = BaselineCategoricalItem | BaselineContinuousItem


def _missing(value: object, variable: VariableMetadata) -> bool:
    return value is None or (variable.kind == "character" and value == "")


def _level_key(value: object, variable: VariableMetadata) -> object:
    if variable.kind == "numeric":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f'Custom level values for numeric variable "{variable.name}" must be numeric.'
            )
        return float(value)
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
            f'Custom level values for character variable "{variable.name}" must be text.'
        )
    return value.casefold()


@dataclass(frozen=True, slots=True)
class BaselineCharacteristicsConfig:
    items: tuple[BaselineItem, ...]
    compiled_filter: CompiledFilter = field(
        default_factory=lambda: CompiledFilter("", ())
    )
    filter_text: str = ""
    header_label: str = "Overall"
    subject_id_variable: str = "USUBJID"

    def validate(self, metadata: DatasetMetadata) -> None:
        if not self.items:
            raise ValueError("Add at least one Categorical or Continuous Item.")
        if not self.header_label.strip():
            raise ValueError("Summary Column Header cannot be empty.")
        known = {variable.name.casefold(): variable for variable in metadata.variables}
        subject = known.get(self.subject_id_variable.casefold())
        if subject is None:
            raise ValueError(f"{self.subject_id_variable} is required.")

        seen: set[str] = set()
        for item in self.items:
            folded = item.variable.casefold()
            variable = known.get(folded)
            if variable is None:
                raise ValueError(f'Variable "{item.variable}" does not exist.')
            if folded == subject.name.casefold():
                raise ValueError("USUBJID cannot also be selected as an analysis Item.")
            if folded in seen:
                raise ValueError(
                    f'Variable "{item.variable}" is selected more than once.'
                )
            seen.add(folded)

            if isinstance(item, BaselineCategoricalItem):
                if item.level_sort_method not in {
                    "runtime_value_ascending",
                    "custom",
                }:
                    raise ValueError(f'Unsupported level order for "{item.variable}".')
                if item.level_sort_method == "custom" and not item.level_order:
                    raise ValueError(
                        f'Custom level order for "{item.variable}" cannot be empty.'
                    )
                if item.level_sort_method != "custom" and item.level_order:
                    raise ValueError(
                        f'Custom levels for "{item.variable}" require Custom level order.'
                    )
                keys = [_level_key(value, variable) for value in item.level_order]
                if len(keys) != len(set(keys)):
                    raise ValueError(
                        f'Custom level order for "{item.variable}" contains duplicates.'
                    )
                if not 0 <= item.percent_digits <= 4:
                    raise ValueError("Percent decimal places must be between 0 and 4.")
                continue

            if variable.kind != "numeric":
                raise ValueError(
                    f'Continuous Item "{item.variable}" must use a numeric variable.'
                )
            decimals = dict(item.statistic_decimals)
            if len(decimals) != len(item.statistic_decimals):
                raise ValueError(
                    f'Decimal settings for "{item.variable}" contain duplicates.'
                )
            unknown = set(decimals) - set(CONTINUOUS_STATISTICS)
            if unknown:
                raise ValueError(
                    "Unknown statistic decimal settings: " + ", ".join(sorted(unknown))
                )
            if any(
                value is not None
                and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= 4
                )
                for value in decimals.values()
            ):
                raise ValueError("Statistic decimals must be Auto or between 0 and 4.")


__all__ = [
    "CONTINUOUS_STATISTICS",
    "BaselineCategoricalItem",
    "BaselineCharacteristicsConfig",
    "BaselineContinuousItem",
    "BaselineItem",
]

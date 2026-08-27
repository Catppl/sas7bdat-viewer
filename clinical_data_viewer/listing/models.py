from __future__ import annotations

from dataclasses import dataclass, field

from ..filter_engine import CompiledFilter


@dataclass(frozen=True, slots=True)
class ListingColumn:
    expression_text: str
    output_name: str
    label: str = ""
    format: str = ""
    sort_order: int | None = None
    sort_direction: str = "ASC"
    report_type: str = "DISPLAY"
    include_in_report: bool = True
    division_by_zero_missing: bool = False


@dataclass(frozen=True, slots=True)
class ListingMergeAdsl:
    enabled: bool = False
    by_variable: str = "USUBJID"
    keep: tuple[str, ...] = ()
    drop: tuple[str, ...] = ()
    duplicate_policy: str = "ignore"  # ignore | rename
    rename_map: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ListingConfig:
    columns: tuple[ListingColumn, ...]
    data_filter: CompiledFilter = field(default_factory=lambda: CompiledFilter("", ()))
    data_filter_text: str = ""
    merge_adsl: ListingMergeAdsl = field(default_factory=ListingMergeAdsl)
    line_size: int = 132

    def validate_basic(self) -> None:
        if not self.columns:
            raise ValueError("Add at least one Listing column.")
        if not any(column.include_in_report for column in self.columns):
            raise ValueError("Include at least one column in PROC REPORT.")
        names: set[str] = set()
        sort_orders: set[int] = set()
        for column in self.columns:
            if not column.expression_text.strip():
                raise ValueError("Listing column expression cannot be empty.")
            if not column.output_name.strip():
                raise ValueError("Listing column Output Name cannot be empty.")
            if column.output_name.casefold().startswith("_lst_"):
                raise ValueError(
                    'Listing Output Name cannot start with reserved prefix "_lst_".'
                )
            key = column.output_name.casefold()
            if key in names:
                raise ValueError(
                    f'Duplicate Listing Output Name: "{column.output_name}".'
                )
            names.add(key)
            if column.sort_direction not in {"ASC", "DESC"}:
                raise ValueError("Listing Sort Direction must be ASC or DESC.")
            if column.report_type not in {"DISPLAY", "ORDER", "GROUP"}:
                raise ValueError(
                    "Listing Report Type must be DISPLAY, ORDER, or GROUP."
                )
            if column.sort_order is not None:
                if column.sort_order <= 0:
                    raise ValueError("Listing Sort Order must be a positive integer.")
                if column.sort_order in sort_orders:
                    raise ValueError(
                        f"Duplicate Listing Sort Order: {column.sort_order}."
                    )
                sort_orders.add(column.sort_order)
        merge = self.merge_adsl
        if merge.keep and merge.drop:
            raise ValueError("Use either Keep ADSL Vars or Drop ADSL Vars, not both.")
        if merge.duplicate_policy not in {"ignore", "rename"}:
            raise ValueError("ADSL duplicate policy must be Ignore or Rename.")
        if self.line_size < 40:
            raise ValueError("Listing report line size must be at least 40.")

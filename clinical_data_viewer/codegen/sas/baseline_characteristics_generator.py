from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...resources import resource_path
from .filter_renderer import sas_filter_expression, sas_name, sas_string

_SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
            f"Baseline Characteristics {label} must be an object."
        )
    return value


def _output_reference(value: object, label: str) -> str:
    reference = str(value or "")
    parts = reference.split(".")
    if len(parts) != 2 or not all(_SAS_NAME.fullmatch(part) for part in parts):
        raise ValueError(f"{label} must be a SAS library.member reference.")
    if len(parts[0]) > 8:
        raise ValueError(f"{label} library must not exceed 8 characters.")
    return reference


def _literal(value: object, kind: str, label: str) -> str:
    if kind == "character":
        if value is None:
            return sas_string("")
        if not isinstance(value, str):
            raise ValueError(f"{label} must be character.")
        return sas_string(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
            f"{label} must be numeric."
        )
    return repr(value)


class SasBaselineCharacteristicsGenerator:
    """Render SAS from the Baseline Characteristics JSON v1 contract."""

    def __init__(self, template_directory: Path | None = None) -> None:
        directory = template_directory or resource_path(
            "clinical_data_viewer/codegen/sas/templates"
        )
        self.environment = Environment(
            loader=FileSystemLoader(str(directory)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=False,
            lstrip_blocks=False,
        )
        self.environment.filters["sas_name"] = sas_name
        self.environment.filters["sas_string"] = sas_string

    def _prepare(self, configuration: Mapping[str, object]) -> dict[str, object]:
        if configuration.get("type") != "baseline_characteristics":
            raise ValueError(
                "The configuration is not a Baseline Characteristics configuration."
            )
        if configuration.get("version") != 1:
            raise ValueError(
                "SAS generation requires Baseline Characteristics configuration v1."
            )
        for name in (
            "input",
            "variable_metadata",
            "dataset_filter",
            "analysis_universe",
            "items",
            "calculation",
            "display",
            "targets",
        ):
            if name not in configuration:
                raise ValueError(
                    f"Baseline Characteristics configuration is missing: {name}"
                )

        source = _mapping(configuration["input"], "input")
        if source.get("kind") == "merge":
            raise ValueError(
                "SAS code generation for merged Baseline Characteristics sources is not available yet."
            )
        if source.get("kind") != "sas":
            raise ValueError("input.kind must be 'sas'.")
        source_format = str(source.get("format") or "").casefold()
        if source_format not in {"sas7bdat", "xpt"}:
            raise ValueError(f"Unsupported input format: {source_format}.")
        if not source.get("source_path"):
            raise ValueError("input.source_path is required.")
        if source_format == "sas7bdat" and not source.get("source_directory"):
            raise ValueError("input.source_directory is required for SAS7BDAT input.")

        variable_metadata = _mapping(
            configuration["variable_metadata"], "variable_metadata"
        )
        variable_types: dict[str, str] = {}
        variable_names: dict[str, str] = {}
        for name, metadata in variable_metadata.items():
            block = _mapping(metadata, f"variable_metadata.{name}")
            kind = block.get("type")
            if kind not in {"character", "numeric"}:
                raise ValueError(f"Unsupported type for variable {name!r}: {kind!r}.")
            variable_types[str(name).casefold()] = str(kind)
            variable_names[str(name).casefold()] = str(name)

        universe = _mapping(configuration["analysis_universe"], "analysis_universe")
        subject_requested = str(universe.get("subject_variable") or "")
        subject = variable_names.get(subject_requested.casefold())
        if subject is None:
            raise ValueError(f"Subject variable does not exist: {subject_requested}.")
        treatment_requested = str(universe.get("treatment_variable") or "")
        if treatment_requested:
            raise ValueError(
                "SAS code generation with treatment columns is not available yet. "
                "Run the Python Baseline Characteristics engine instead."
            )
        if (
            universe.get("subject_missing") != "error"
            or universe.get("duplicate_subject_rows") != "error"
        ):
            raise ValueError(
                "Baseline Characteristics requires missing and duplicate subject rows to be errors."
            )

        filter_block = _mapping(configuration["dataset_filter"], "dataset_filter")
        if filter_block.get("language") != "sas_like" or "ast" not in filter_block:
            raise ValueError("dataset_filter must contain a sas_like AST.")
        try:
            where_expression = sas_filter_expression(
                filter_block.get("ast"), compact_and=True
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Unsupported dataset_filter AST: {error}") from error

        raw_items = configuration["items"]
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError(
                "Baseline Characteristics items must be a non-empty array."
            )
        items: list[dict[str, object]] = []
        for position, raw_item in enumerate(raw_items, start=1):
            item = _mapping(raw_item, f"items[{position}]")
            requested = str(item.get("variable") or "")
            name = variable_names.get(requested.casefold())
            if name is None:
                raise ValueError(f"Item variable does not exist: {requested}.")
            kind = variable_types[name.casefold()]
            if item.get("storage_type") != kind:
                raise ValueError(
                    f"Storage type for {name} does not match variable metadata."
                )
            role = item.get("role")
            if role not in {"categorical", "continuous"}:
                raise ValueError(f"Unsupported item role for {name}: {role!r}.")
            if role == "continuous" and kind != "numeric":
                raise ValueError(
                    f'Continuous Item "{name}" must use a numeric variable.'
                )
            prepared = {
                "order": position,
                "role": role,
                "variable": sas_name(name),
                "label": str(item.get("label") or ""),
                "unit": str(item.get("unit") or ""),
                "storage_type": kind,
                "percent_digits": item.get("percent_digits", 1),
                "statistic_decimals": item.get("statistic_decimals", {}),
                "include_missing": False,
            }
            if role == "categorical":
                if (
                    item.get("count") != "distinct_subjects"
                    or item.get("denominator") != "header_n"
                ):
                    raise ValueError(
                        "Categorical items must use distinct subjects and header N."
                    )
                level_order = _mapping(
                    item.get("level_order"), f"items[{position}].level_order"
                )
                method = level_order.get("method")
                if method not in {"runtime_value_ascending", "custom"}:
                    raise ValueError(f"Unsupported level order for {name}.")
                prepared["level_order"] = method
                custom_levels = level_order.get("values", [])
                if not isinstance(custom_levels, list):
                    raise ValueError(f"Custom levels for {name} must be an array.")
                raw_labels = level_order.get("labels", [])
                labels_by_value = {
                    json_item.get("value"): json_item.get("label")
                    for json_item in raw_labels
                    if isinstance(json_item, Mapping)
                }
                prepared["custom_levels"] = [
                    {
                        "rank": rank,
                        "display": str(labels_by_value.get(value) or ("Missing" if value is None else value)),
                        "literal": _literal(
                            value, kind, f"items[{position}].level_order.values[{rank}]"
                        ),
                    }
                    for rank, value in enumerate(custom_levels, start=1)
                ]
                prepared["include_missing"] = any(value is None for value in custom_levels)
                percent_digits = item.get("percent_digits")
                if (
                    isinstance(percent_digits, bool)
                    or not isinstance(percent_digits, int)
                    or not 0 <= percent_digits <= 4
                ):
                    raise ValueError(
                        f"Percent decimals for {name} must be between 0 and 4."
                    )
            else:
                raw_statistics = item.get("statistics", [
                    "n", "mean", "std", "median", "q1", "q3", "min", "max"
                ])
                if not isinstance(raw_statistics, list) or not raw_statistics:
                    raise ValueError(f"Statistics for {name} must be a non-empty array.")
                if any(
                    statistic
                    not in {"n", "mean", "std", "median", "q1", "q3", "min", "max"}
                    for statistic in raw_statistics
                ):
                    raise ValueError(f"Unsupported statistic for {name}.")
                if ("q1" in raw_statistics) != ("q3" in raw_statistics):
                    raise ValueError("Q1 and Q3 must be selected together.")
                if ("min" in raw_statistics) != ("max" in raw_statistics):
                    raise ValueError("Min and Max must be selected together.")
                prepared["statistics"] = raw_statistics
                raw_decimals = item.get("statistic_decimals", {})
                if not isinstance(raw_decimals, Mapping):
                    raise ValueError(
                        f"Statistic decimals for {name} must be an object."
                    )
                decimals: dict[str, int | None] = {}
                for statistic in ("mean", "std", "median", "q1", "q3", "min", "max"):
                    value = raw_decimals.get(statistic, 0)
                    if value is None or (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or not 0 <= value <= 4
                    ):
                        raise ValueError(f"Statistic decimals for {name} must be 0-4.")
                    decimals[statistic] = value
                prepared["statistic_decimals"] = decimals
            items.append(prepared)

        calculation = _mapping(configuration["calculation"], "calculation")
        if (
            calculation.get("standard_deviation") != "sample_n_minus_1"
            or calculation.get("quantiles") != "qntldef5"
        ):
            raise ValueError(
                "Baseline Characteristics SAS requires sample SD and QNTLDEF=5."
            )
        display = _mapping(configuration["display"], "display")
        if display.get("rounding") != "half_up" or display.get("maximum_decimals") != 4:
            raise ValueError(
                "Baseline Characteristics display requires half-up rounding with maximum 4 decimals."
            )
        if display.get("footnote") is not False:
            raise ValueError(
                "Baseline Characteristics shell must not include a footnote."
            )

        targets = _mapping(configuration["targets"], "targets")
        sas_target = _mapping(targets.get("sas"), "targets.sas")
        output = _output_reference(sas_target.get("output_dataset"), "output_dataset")
        long_output = _output_reference(
            sas_target.get("long_output_dataset"), "long_output_dataset"
        )
        source_library = str(sas_target.get("source_library") or "")
        source_member = str(
            sas_target.get("source_member") or source.get("dataset") or ""
        )
        if not _SAS_NAME.fullmatch(source_library) or len(source_library) > 8:
            raise ValueError("source_library must be a valid 1-8 character SAS libref.")
        if not source_member:
            raise ValueError("source_member is required.")

        return {
            "source": {
                "format": source_format,
                "source_path": str(source.get("source_path")),
                "source_directory": str(source.get("source_directory") or ""),
                "library": source_library,
                "member": sas_name(source_member),
            },
            "subject": sas_name(subject),
            "where_expression": where_expression,
            "items": items,
            "header_label": str(display.get("header_label") or "Overall"),
            "output": output,
            "long_output": long_output,
        }

    def generate(self, configuration: Mapping[str, object]) -> str:
        context = self._prepare(configuration)
        try:
            return self.environment.get_template(
                "baseline_characteristics.sas.j2"
            ).render(**context)
        except TemplateError as error:
            raise ValueError(
                f"Baseline Characteristics SAS template failed: {error}"
            ) from error


__all__ = ["SasBaselineCharacteristicsGenerator"]

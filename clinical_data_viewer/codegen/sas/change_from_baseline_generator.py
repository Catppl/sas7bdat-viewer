from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...resources import resource_path
from .filter_renderer import sas_filter_expression, sas_name, sas_string

_SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STATISTICS = ("N", "MEAN", "STD", "MEDIAN", "Q1", "Q3", "MIN", "MAX")
_NUMERIC_STATISTICS = set(_STATISTICS) - {"N"}
_REQUIRED = {
    "input", "variables", "variable_metadata", "dataset_filter",
    "postbaseline_filter", "item", "row_sort", "statistics", "treatment",
    "total", "calculation", "display", "targets",
}


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(  # noqa: TRY004 - preserve generator validation contract
            f"Change from Baseline block {name!r} must be an object."
        )
    return value


def _sas_member(value: object) -> str:
    text = str(value or "")
    if not _SAS_NAME.fullmatch(text):
        raise ValueError("Change from Baseline dataset name must be a valid SAS name.")
    return text.lower()


def _input(value: object) -> dict[str, str]:
    block = _mapping(value, "input")
    if block.get("kind") == "merge":
        raise ValueError(
            "SAS code generation for merged Change from Baseline sources is not available yet."
        )
    if block.get("kind") != "sas" or str(block.get("format", "")).casefold() != "sas7bdat":
        raise ValueError(
            "Change from Baseline SAS generation supports only SAS7BDAT sources."
        )
    return {"member": _sas_member(block.get("dataset"))}


def _filter(value: object, name: str) -> str:
    block = _mapping(value, name)
    if block.get("language") != "sas_like" or "ast" not in block:
        raise ValueError(f"{name} must contain language 'sas_like' and ast.")
    text = block.get("text", "")
    if not isinstance(text, str):
        raise ValueError(f"{name}.text must be a string.")  # noqa: TRY004
    if text.strip() and block.get("ast") is None:
        raise ValueError(f"{name}.ast cannot be empty when text is provided.")
    try:
        return sas_filter_expression(block.get("ast"), compact_and=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Unsupported {name} AST: {error}") from error


def _variable(name: object, label: str, variables: Mapping[str, object]) -> str:
    text = str(name or "")
    known = {str(value).casefold(): str(value) for value in variables}
    if text.casefold() not in known:
        raise ValueError(f'{label} "{text}" does not exist in variables metadata.')
    return known[text.casefold()]


def _kind(name: str, variables: Mapping[str, object]) -> str:
    metadata = _mapping(variables[name], f"variable_metadata.{name}")
    value = metadata.get("type", metadata.get("kind"))
    if value not in {"character", "numeric"}:
        raise ValueError(f"Unsupported type for variable {name!r}.")
    return str(value)


def _literal(value: object, kind: str, label: str) -> str:
    if kind == "character":
        return sas_string(value if isinstance(value, str) else str(value))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")  # noqa: TRY004
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return repr(value)


def _raw_text(name: str, kind: str) -> str:
    return (
        f"strip({sas_name(name)})"
        if kind == "character"
        else f"strip(put({sas_name(name)}, best32.))"
    )


def _stat_output(statistics: list[str]) -> list[dict[str, str]]:
    aliases = {
        "N": "n", "MEAN": "mean", "STD": "std", "MEDIAN": "median",
        "Q1": "q1", "Q3": "q3", "MIN": "min", "MAX": "max",
    }
    return [{"keyword": aliases[stat], "name": stat} for stat in statistics]


class SasChangeFromBaselineGenerator:
    """Render a readable SAS program from Change from Baseline JSON v1."""

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
        if not isinstance(configuration, Mapping):
            raise ValueError(  # noqa: TRY004 - preserve generator validation contract
                "Change from Baseline SAS generator requires a JSON object."
            )
        if configuration.get("type") != "change_from_baseline_horizontal":
            raise ValueError("The configuration is not a Change from Baseline configuration.")
        if configuration.get("version") != 1:
            raise ValueError("SAS generation requires Change from Baseline configuration v1.")
        missing = sorted(_REQUIRED - set(configuration))
        if missing:
            raise ValueError(
                "Change from Baseline configuration is missing: " + ", ".join(missing)
            )

        source = _input(configuration["input"])
        roles = _mapping(configuration["variables"], "variables")
        metadata = _mapping(configuration["variable_metadata"], "variable_metadata")
        metadata_names = {str(name).casefold(): str(name) for name in metadata}
        metadata = {metadata_names[str(name).casefold()]: value for name, value in metadata.items()}
        subject = _variable(roles.get("subject", "USUBJID"), "Subject variable", metadata)
        change = _variable(roles.get("change"), "Change variable", metadata)
        item = _variable(roles.get("item"), "Item variable", metadata)
        row_names = roles.get("row", [])
        if not isinstance(row_names, list):
            raise ValueError("variables.row must be an array.")  # noqa: TRY004
        rows = [_variable(name, "Row variable", metadata) for name in row_names]
        treatment_name = roles.get("treatment")
        treatment = _variable(treatment_name, "Treatment variable", metadata) if treatment_name else ""
        kinds = {name: _kind(name, metadata) for name in metadata}
        if kinds[change] != "numeric":
            raise ValueError("Change variable must be numeric.")

        calculation = _mapping(configuration["calculation"], "calculation")
        expected = {
            "reference_engine": "python_change_from_baseline_v1",
            "change_source": "existing_dataset_variable",
            "small_n": "nonmissing_change_records",
            "large_n": "distinct_subjects",
            "subject_missing": "exclude",
            "change_missing": "exclude",
            "treatment_missing": "error",
            "duplicate_change_records": "warning_continue",
        }
        for key, value in expected.items():
            if calculation.get(key) != value:
                raise ValueError(f"Unsupported Change from Baseline calculation contract: {key}.")

        statistics = [str(value).upper() for value in configuration["statistics"]]
        if not statistics or any(value not in _STATISTICS for value in statistics):
            raise ValueError("Unsupported Change from Baseline statistics.")
        display = _mapping(configuration["display"], "display")
        if display.get("rounding") != "half_up" or display.get("maximum_decimals") != 4:
            raise ValueError("Change from Baseline display rounding must use half_up and a four-decimal cap.")
        decimals = _mapping(display.get("statistic_decimals"), "display.statistic_decimals")
        statistic_decimals: dict[str, int] = {}
        for statistic in _NUMERIC_STATISTICS:
            value = decimals.get(statistic)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
                raise ValueError(f"{statistic} decimal places must be between 0 and 4.")
            statistic_decimals[statistic] = value

        treatment_block = _mapping(configuration["treatment"], "treatment")
        if treatment_block.get("level_order") != "resolved":
            raise ValueError("Change from Baseline treatment level_order must be resolved.")
        treatment_columns = bool(treatment) and treatment.casefold() not in {name.casefold() for name in rows}
        treatment_kind = kinds[treatment] if treatment else "character"
        levels: list[dict[str, object]] = []
        if treatment_columns:
            resolved_levels = treatment_block.get("resolved_levels", [])
            if not isinstance(resolved_levels, list) or not resolved_levels:
                raise ValueError("Treatment columns require resolved treatment levels.")
            for order, raw_level in enumerate(resolved_levels, start=1):
                level = _mapping(raw_level, "treatment.resolved_levels")
                if "value" not in level or "label" not in level:
                    raise ValueError("Each resolved treatment level requires value and label.")
                levels.append({
                    "order": order,
                    "value": level["value"],
                    "label": str(level["label"]),
                    "literal": _literal(level["value"], treatment_kind, "Treatment value"),
                })

        item_kind = kinds[item]
        item_block = _mapping(configuration["item"], "item")
        labels = _mapping(item_block.get("labels", {}), "item.labels")
        resolved_order = item_block.get("resolved_order", [])
        if not isinstance(resolved_order, list):
            raise ValueError("item.resolved_order must be an array.")  # noqa: TRY004
        item_cases = []
        for order, value in enumerate(resolved_order, start=1):
            if value is None:
                continue
            label = next(
                (label for key, label in labels.items() if str(key).casefold() == str(value).casefold()),
                value,
            )
            item_cases.append({
                "literal": _literal(value, item_kind, "Item value"),
                "order": order,
                "text": str(value),
                "label": str(label),
            })
        label_cases = [{"value": str(value), "label": str(label)} for value, label in labels.items()]

        row_sort = configuration["row_sort"]
        if not isinstance(row_sort, list):
            raise ValueError("row_sort must be an array.")  # noqa: TRY004
        row_directions = {}
        for entry in row_sort:
            sort_entry = _mapping(entry, "row_sort entry")
            row_directions[str(sort_entry.get("variable", "")).casefold()] = str(sort_entry.get("direction", "asc")).lower()
        row_specs = []
        for index, name in enumerate(rows, start=1):
            direction = row_directions.get(name.casefold(), "asc")
            if direction not in {"asc", "desc"}:
                raise ValueError(f"Unsupported row sort direction for {name}.")
            row_specs.append({
                "name": name,
                "sas_name": sas_name(name),
                "kind": kinds[name],
                "direction": direction,
                "missing_flag": f"_row{index}_missing",
            })

        total = _mapping(configuration["total"], "total")
        if total.get("method") != "recompute_distinct_subjects":
            raise ValueError("Unsupported Change from Baseline total method.")
        include_total = bool(total.get("enabled")) and treatment_columns
        dataset_filter = _filter(configuration["dataset_filter"], "dataset_filter")
        postbaseline_filter = _filter(configuration["postbaseline_filter"], "postbaseline_filter")
        where = " and ".join(value for value in (dataset_filter, postbaseline_filter) if value)

        class_base = ["_item_text", "_item_label", "_item_order", "_item_sort"]
        class_base += [row["sas_name"] for row in row_specs]
        class_base += [row["missing_flag"] for row in row_specs]
        class_group = class_base + (["_trt_order", "_trt_label"] if treatment_columns else [])
        row_sort_terms = ["_item_order", "_item_sort"]
        for row in row_specs:
            row_sort_terms.append(row["missing_flag"])
            row_sort_terms.append(("descending " if row["direction"] == "desc" else "") + row["sas_name"])

        output_columns: list[dict[str, str]] = []
        if treatment_columns:
            for level in levels:
                prefix = f"TRT_{level['order']}_"
                for statistic in statistics:
                    output_columns.append({
                        "name": prefix + statistic,
                        "label": f"{level['label']} {statistic}",
                        "format": f"8.{statistic_decimals[statistic]}" if statistic in _NUMERIC_STATISTICS else "",
                    })
                output_columns.append({"name": prefix + "SUBJECT_N", "label": f"{level['label']} N", "format": ""})
            if include_total:
                for statistic in statistics:
                    output_columns.append({
                        "name": "TOTAL_" + statistic,
                        "label": f"Total {statistic}",
                        "format": f"8.{statistic_decimals[statistic]}" if statistic in _NUMERIC_STATISTICS else "",
                    })
                output_columns.append({"name": "TOTAL_SUBJECT_N", "label": "Total N", "format": ""})
        else:
            for statistic in statistics:
                output_columns.append({
                    "name": statistic,
                    "label": statistic,
                    "format": f"8.{statistic_decimals[statistic]}" if statistic in _NUMERIC_STATISTICS else "",
                })
            output_columns.append({"name": "SUBJECT_N", "label": "N", "format": ""})

        return {
            "source_member": source["member"],
            "source_display": f"adam.{source['member']}",
            "dataset_filter": dataset_filter,
            "postbaseline_filter": postbaseline_filter,
            "where": where,
            "subject": subject,
            "change": change,
            "item": item,
            "item_kind": item_kind,
            "item_raw_text": _raw_text(item, item_kind),
            "item_cases": item_cases,
            "label_cases": label_cases,
            "rows": row_specs,
            "treatment": treatment,
            "treatment_kind": treatment_kind,
            "treatment_raw_text": _raw_text(treatment, treatment_kind) if treatment else "'All'",
            "treatment_columns": treatment_columns,
            "treatment_levels": levels,
            "include_total": include_total,
            "statistics": statistics,
            "stat_output": _stat_output(statistics),
            "class_base": class_base,
            "class_group": class_group,
            "row_sort_terms": row_sort_terms,
            "output_columns": output_columns,
            "statistic_decimals": statistic_decimals,
            "item_labels": labels,
            "total_order": len(levels) + 1,
            "target_dataset": str(_mapping(_mapping(configuration["targets"], "targets").get("sas"), "targets.sas").get("output_dataset") or "work.change_from_baseline"),
        }

    def generate(self, configuration: Mapping[str, object]) -> str:
        context = self._prepare(configuration)
        try:
            return self.environment.get_template("change_from_baseline.sas.j2").render(**context)
        except (OSError, TemplateError) as error:
            raise ValueError(f"Unable to render Change from Baseline SAS template: {error}") from error


__all__ = ["SasChangeFromBaselineGenerator"]

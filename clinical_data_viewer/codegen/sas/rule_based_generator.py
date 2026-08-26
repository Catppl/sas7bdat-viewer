from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...resources import resource_path
from .filter_renderer import sas_filter_expression, sas_name, sas_string

_SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED_TOP_LEVEL = (
    "input",
    "variables",
    "dataset_filter",
    "rows",
    "count",
    "treatment",
    "denominator",
    "total",
    "calculation",
    "display",
    "targets",
)


def _safe_token(value: object) -> str:
    token = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_").lower()
    if not token or token[0].isdigit():
        token = f"var_{token}" if token else "variable"
    return token


def _work_member(value: object, used: set[str]) -> str:
    base = _safe_token(value)[:24] or "rb_tmp"
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        suffix_text = str(suffix)
        candidate = base[: 32 - len(suffix_text)] + suffix_text
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
            f"Rule-based configuration block {name!r} must be an object."
        )
    return value


def _required(configuration: Mapping[str, object], name: str) -> object:
    if name not in configuration:
        raise ValueError(f"Rule-based configuration is missing required block: {name}.")
    return configuration[name]


def _validate_dataset_reference(value: object, label: str) -> str:
    reference = str(value or "")
    parts = reference.split(".")
    if len(parts) != 2 or not all(_SAS_NAME.fullmatch(part) for part in parts):
        raise ValueError(f"{label} must be a SAS library.member reference.")
    return reference


def _sas_numeric(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")  # noqa: TRY004
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return repr(value)


def _sas_literal(value: object, kind: str, label: str) -> str:
    if kind == "character":
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a character value.")
        return sas_string(value)
    return _sas_numeric(value, label)


def _filter_text(block: object, label: str) -> str:
    mapping = _mapping(block, label)
    if mapping.get("language") != "sas_like":
        raise ValueError(f"{label} must use language 'sas_like'.")
    if "ast" not in mapping:
        raise ValueError(f"{label}.ast is required.")
    ast = mapping["ast"]
    text = mapping.get("text", "")
    if not isinstance(text, str):
        raise ValueError(f"{label}.text must be a string.")  # noqa: TRY004
    if text.strip() and ast is None:
        raise ValueError(f"{label}.ast cannot be empty when text is provided.")
    try:
        rendered = sas_filter_expression(ast)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Unsupported {label} AST: {error}") from error
    return rendered


def _input_context(source: Mapping[str, object], label: str) -> dict[str, object]:
    kind = source.get("kind")
    if kind == "merge":
        raise ValueError(
            "SAS code generation for merged Rule-based sources is not available yet."
        )
    if kind != "sas":
        raise ValueError(f"{label}.kind must be 'sas'.")
    input_format = str(source.get("format", "")).casefold()
    if input_format not in {"sas7bdat", "xpt"}:
        raise ValueError(
            f"Unsupported {label} format for SAS generation: {input_format}"
        )
    source_path = source.get("source_path")
    source_directory = source.get("source_directory")
    dataset = source.get("dataset")
    if not dataset:
        raise ValueError(f"{label}.dataset is required.")
    if not source_path:
        raise ValueError(f"{label}.source_path is required.")
    if input_format == "sas7bdat" and not source_directory:
        raise ValueError(f"{label}.source_directory is required for SAS7BDAT input.")
    return {
        "kind": kind,
        "format": input_format,
        "dataset": str(dataset),
        "source_path": str(source_path or ""),
        "source_directory": str(source_directory or ""),
    }


class SasRuleBasedGenerator:
    """Render a standalone SAS program from Rule-based JSON v1."""

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
        if configuration.get("type") != "rule_based_table":
            raise ValueError(
                "The configuration is not a Rule-based Table configuration."
            )
        if configuration.get("version") != 1:
            raise ValueError(
                "SAS generation requires Rule-based Table configuration v1."
            )
        missing = [name for name in _REQUIRED_TOP_LEVEL if name not in configuration]
        if missing:
            raise ValueError(
                "Rule-based configuration is missing: " + ", ".join(missing)
            )

        input_block = _input_context(_mapping(configuration["input"], "input"), "input")
        variables = _mapping(configuration["variables"], "variables")
        treatment_block = _mapping(configuration["treatment"], "treatment")
        count_block = _mapping(configuration["count"], "count")
        calculation = _mapping(configuration["calculation"], "calculation")
        display = _mapping(configuration["display"], "display")
        total = _mapping(configuration["total"], "total")
        targets = _mapping(configuration["targets"], "targets")
        sas_target = _mapping(_required(targets, "sas"), "targets.sas")

        if (
            count_block.get("type") != "distinct"
            or count_block.get("variable") != "USUBJID"
        ):
            raise ValueError(
                "Rule-based SAS generation supports only distinct USUBJID counts."
            )
        if calculation.get("reference_engine") != "python_rule_based_v1":
            raise ValueError("Unsupported Rule-based calculation reference engine.")
        if calculation.get("numerator") != "distinct_subjects":
            raise ValueError("Unsupported Rule-based numerator calculation.")
        if treatment_block.get("missing_policy") != "error":
            raise ValueError("Unsupported Rule-based treatment missing policy.")
        if treatment_block.get("level_order") != "resolved":
            raise ValueError("Rule-based treatment level_order must be 'resolved'.")
        if calculation.get("subject_missing") != "exclude":
            raise ValueError("Unsupported Rule-based subject missing policy.")
        if calculation.get("treatment_missing") != "error":
            raise ValueError("Unsupported Rule-based treatment missing policy.")
        if calculation.get("percent_method") != "freq_divided_by_denom_times_100":
            raise ValueError("Unsupported Rule-based percent calculation.")
        if calculation.get("total_method") != "recompute_distinct_subjects":
            raise ValueError("Unsupported Rule-based total calculation.")
        if display.get("rounding") != "half_up":
            raise ValueError("Rule-based display rounding must be 'half_up'.")
        digits = display.get("percent_digits")
        if (
            isinstance(digits, bool)
            or not isinstance(digits, int)
            or not 0 <= digits <= 4
        ):
            raise ValueError(
                "Rule-based percent_digits must be an integer from 0 to 4."
            )
        if display.get("zero_denominator_display") != "0 (—)":
            raise ValueError("Unsupported zero denominator display contract.")
        if not isinstance(total.get("enabled"), bool):
            raise ValueError("total.enabled must be true or false.")  # noqa: TRY004
        if total.get("method") != "recompute_distinct_subjects":
            raise ValueError("Unsupported Rule-based total method.")

        variable_types: dict[str, str] = {}
        for name, metadata in variables.items():
            variable_metadata = _mapping(metadata, f"variables.{name}")
            kind = variable_metadata.get("type")
            if kind not in {"character", "numeric"}:
                raise ValueError(f"Unsupported type for variable {name!r}: {kind!r}.")
            variable_types[str(name).casefold()] = str(kind)
        treatment_variable = str(treatment_block.get("variable") or "")
        treatment_kind = variable_types.get(treatment_variable.casefold())
        if treatment_kind is None:
            raise ValueError(
                f"Treatment variable does not exist: {treatment_variable}."
            )
        if variable_types.get("usubjid") is None:
            raise ValueError("Rule-based configuration must define USUBJID.")

        resolved_levels = treatment_block.get("resolved_levels")
        if not isinstance(resolved_levels, list):
            raise ValueError(  # noqa: TRY004
                "treatment.resolved_levels must be an array."
            )
        levels: list[dict[str, object]] = []
        seen_levels: set[str] = set()
        for level in resolved_levels:
            level_mapping = _mapping(level, "treatment.resolved_levels[]")
            if "value" not in level_mapping or "label" not in level_mapping:
                raise ValueError("Each resolved treatment level needs value and label.")
            value = level_mapping["value"]
            key = repr(value)
            if key in seen_levels or value is None:
                raise ValueError(
                    "Resolved treatment levels must be unique and non-missing."
                )
            seen_levels.add(key)
            literal = _sas_literal(value, treatment_kind, "treatment level value")
            levels.append(
                {
                    "value": value,
                    "label": str(level_mapping["label"]),
                    "literal": literal,
                }
            )

        rows_value = configuration["rows"]
        if not isinstance(rows_value, list) or not rows_value:
            raise ValueError("Rule-based configuration rows must be a non-empty array.")
        used_members: set[str] = set()
        work_source = _work_member("rb_source", used_members)
        work_population = _work_member("rb_population", used_members)
        work_items = _work_member("rb_items", used_members)
        work_long = _work_member("rb_long", used_members)
        work_denominator = _work_member("rb_denominator", used_members)
        work_denominator_total = _work_member("rb_denominator_total", used_members)
        rows: list[dict[str, object]] = []
        row_ids: set[str] = set()
        for index, row in enumerate(rows_value, start=1):
            row_mapping = _mapping(row, f"rows[{index - 1}]")
            row_id = str(row_mapping.get("id") or "")
            item = str(row_mapping.get("item") or "")
            indent = row_mapping.get("indent")
            if not row_id or not item:
                raise ValueError(f"rows[{index - 1}] requires id and item.")
            if row_id.casefold() in row_ids:
                raise ValueError(f"rows[{index - 1}].id must be unique.")
            row_ids.add(row_id.casefold())
            if (
                isinstance(indent, bool)
                or not isinstance(indent, int)
                or not 0 <= indent <= 8
            ):
                raise ValueError(
                    f"rows[{index - 1}].indent must be an integer from 0 to 8."
                )
            rows.append(
                {
                    "order": index,
                    "id": row_id,
                    "item": item,
                    "indent": indent,
                    "filter": _filter_text(
                        row_mapping.get("filter"), f"rows[{index - 1}].filter"
                    ),
                    "source_member": _work_member(f"rb_{row_id}_source", used_members),
                    "member": _work_member(f"rb_{row_id}", used_members),
                }
            )

        denominator = _mapping(configuration["denominator"], "denominator")
        denominator_type = denominator.get("type")
        if denominator_type not in {"same_universe", "nonmissing", "population"}:
            raise ValueError(
                f"Unsupported Rule-based denominator type: {denominator_type!r}."
            )
        denominator_context: dict[str, object] = {"type": denominator_type}
        if denominator_type == "nonmissing":
            analysis_value = str(denominator.get("analysis_value_variable") or "")
            if analysis_value.casefold() not in variable_types:
                raise ValueError(
                    f"Analysis value variable does not exist: {analysis_value}."
                )
            denominator_context["analysis_value"] = sas_name(analysis_value)
        elif denominator_type == "population":
            population = _mapping(
                denominator.get("population"), "denominator.population"
            )
            population_input = _input_context(
                _mapping(population.get("input"), "denominator.population.input"),
                "denominator.population.input",
            )
            denominator_context["population"] = {
                "input": population_input,
                "filter": _filter_text(
                    population.get("filter"), "denominator.population.filter"
                ),
                "member": sas_name(population_input["dataset"]),
            }
            population_variables = _mapping(
                population.get("variables"), "denominator.population.variables"
            )
            if treatment_variable.casefold() not in {
                str(name).casefold() for name in population_variables
            }:
                raise ValueError(
                    "Population variables must include the treatment variable."
                )
            if "USUBJID".casefold() not in {
                str(name).casefold() for name in population_variables
            }:
                raise ValueError("Population variables must include USUBJID.")

        output_dataset = _validate_dataset_reference(
            _mapping(sas_target, "targets.sas").get("output_dataset"),
            "targets.sas.output_dataset",
        )
        source_member = sas_target.get("source_member")
        source_library = sas_target.get("source_library")
        if not source_library or not source_member:
            raise ValueError(
                "targets.sas.source_library and source_member are required."
            )
        source_reference = _validate_dataset_reference(
            f"{source_library}.{source_member}", "targets.sas source"
        )
        long_branches: list[dict[str, object]] = []
        for row in rows:
            for order, level in enumerate(levels, start=1):
                long_branches.append(
                    {
                        "row": row,
                        "order": order,
                        "value": level["value"],
                        "literal": level["literal"],
                        "label": level["label"],
                        "frequency_table": row["member"],
                        "total": False,
                    }
                )
            if total["enabled"]:
                long_branches.append(
                    {
                        "row": row,
                        "order": len(levels) + 1,
                        "value": None,
                        "literal": "''" if treatment_kind == "character" else ".",
                        "label": "Total",
                        "frequency_table": f"{row['member']}_total",
                        "total": True,
                    }
                )

        return {
            "version": 1,
            "source": input_block,
            "source_reference": source_reference,
            "source_library": str(source_library),
            "source_member": str(source_member),
            "dataset_filter": _filter_text(
                configuration["dataset_filter"], "dataset_filter"
            ),
            "treatment": sas_name(treatment_variable),
            "treatment_variable": treatment_variable,
            "subject": sas_name("USUBJID"),
            "levels": levels,
            "rows": rows,
            "long_branches": long_branches,
            "denominator": denominator_context,
            "total_enabled": total["enabled"],
            "digits": digits,
            "round_increment": 10 ** (-digits),
            "output_dataset": output_dataset,
            "work_source": work_source,
            "work_population": work_population,
            "work_items": work_items,
            "work_long": work_long,
            "work_denominator": work_denominator,
            "work_denominator_total": work_denominator_total,
            "sas_string": sas_string,
            "sas_name": sas_name,
            "treatment_kind": treatment_kind,
        }

    def generate(self, configuration: dict[str, object]) -> str:
        if not isinstance(configuration, Mapping):
            raise ValueError(  # noqa: TRY004
                "Rule-based SAS configuration must be an object."
            )
        try:
            context = self._prepare(configuration)
            return self.environment.get_template("rule_based.sas.j2").render(**context)
        except (KeyError, TypeError, TemplateError) as error:
            raise ValueError(
                f"Unable to render the Rule-based SAS template: {error}"
            ) from error


__all__ = ["SasRuleBasedGenerator"]

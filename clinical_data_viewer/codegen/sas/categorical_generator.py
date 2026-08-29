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
    "numerator",
    "items",
    "count",
    "treatment",
    "denominator",
    "total",
    "sort",
    "calculation",
    "display",
    "output",
    "targets",
)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
            f"Categorical configuration block {name!r} must be an object."
        )
    return value


def _input(value: object, label: str) -> dict[str, object]:
    source = _mapping(value, label)
    if source.get("kind") == "merge":
        raise ValueError(
            "SAS code generation for merged Categorical sources is not available yet."
        )
    if source.get("kind") != "sas":
        raise ValueError(f"{label}.kind must be 'sas'.")
    source_format = str(source.get("format") or "").casefold()
    if source_format not in {"sas7bdat", "xpt"}:
        raise ValueError(f"Unsupported {label} format: {source_format}.")
    if not source.get("source_path"):
        raise ValueError(f"{label}.source_path is required.")
    if source_format == "sas7bdat" and not source.get("source_directory"):
        raise ValueError(f"{label}.source_directory is required for SAS7BDAT input.")
    return {
        "kind": "sas",
        "format": source_format,
        "dataset": str(source.get("dataset") or ""),
        "source_path": str(source.get("source_path") or ""),
        "source_directory": str(source.get("source_directory") or ""),
    }


def _filter(value: object, label: str) -> str:
    block = _mapping(value, label)
    if block.get("language") != "sas_like":
        raise ValueError(f"{label} must use language 'sas_like'.")
    if "ast" not in block:
        raise ValueError(f"{label}.ast is required.")
    text = block.get("text", "")
    if not isinstance(text, str):
        raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
            f"{label}.text must be a string."
        )
    if text.strip() and block.get("ast") is None:
        raise ValueError(f"{label}.ast cannot be empty when text is provided.")
    try:
        return sas_filter_expression(block.get("ast"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Unsupported {label} AST: {error}") from error


def _dataset_reference(library: object, member: object, label: str) -> str:
    libref = str(library or "")
    if not _SAS_NAME.fullmatch(libref) or len(libref) > 8:
        raise ValueError(f"{label} library must be a valid 1-8 character SAS libref.")
    if not member:
        raise ValueError(f"{label} member is required.")
    return f"{libref}.{sas_name(member)}"


def _output_reference(value: object, label: str) -> str:
    reference = str(value or "")
    parts = reference.split(".")
    if len(parts) != 2 or not all(_SAS_NAME.fullmatch(part) for part in parts):
        raise ValueError(f"{label} must be a SAS library.member reference.")
    if len(parts[0]) > 8:
        raise ValueError(f"{label} library must not exceed 8 characters.")
    return reference


def _safe_token(value: object) -> str:
    token = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_").lower()
    if not token or token[0].isdigit():
        token = f"item_{token}" if token else "item"
    return token


def _work_member(prefix: str, item: object, used: set[str]) -> str:
    base = f"{prefix}_{_safe_token(item)}"[:28]
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        suffix_text = str(suffix)
        candidate = base[: 32 - len(suffix_text)] + suffix_text
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _numeric(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
            f"{label} must be numeric."
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return repr(value)


def _literal(value: object, kind: str, label: str) -> str:
    if kind == "character":
        if not isinstance(value, str):
            raise ValueError(f"{label} must be character.")
        return sas_string(value)
    return _numeric(value, label)


class SasCategoricalGenerator:
    """Render readable SAS from the Categorical Table JSON v1 contract."""

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
        if configuration.get("type") != "categorical_table":
            raise ValueError("The configuration is not a Categorical Table configuration.")
        if configuration.get("version") != 1:
            raise ValueError("SAS generation requires Categorical Table configuration v1.")
        missing = [name for name in _REQUIRED_TOP_LEVEL if name not in configuration]
        if missing:
            raise ValueError("Categorical configuration is missing: " + ", ".join(missing))

        source = _input(configuration["input"], "input")
        variables = _mapping(configuration["variables"], "variables")
        variable_types: dict[str, str] = {}
        variable_names: dict[str, str] = {}
        variable_lengths: dict[str, int] = {}
        for name, metadata in variables.items():
            metadata_block = _mapping(metadata, f"variables.{name}")
            kind = metadata_block.get("type")
            if kind not in {"character", "numeric"}:
                raise ValueError(f"Unsupported type for variable {name!r}: {kind!r}.")
            variable_types[str(name).casefold()] = str(kind)
            variable_names[str(name).casefold()] = str(name)
            length = metadata_block.get("length")
            variable_lengths[str(name).casefold()] = (
                min(length, 32767)
                if isinstance(length, int) and not isinstance(length, bool) and length > 0
                else 200
            )

        numerator = _mapping(configuration["numerator"], "numerator")
        numerator_filter = _filter(numerator.get("filter"), "numerator.filter")
        count = _mapping(configuration["count"], "count")
        count_type = count.get("type")
        if count_type not in {"distinct_subjects", "records"}:
            raise ValueError("Unsupported Categorical count type.")
        subject_name = str(count.get("subject_variable") or "")
        if subject_name.casefold() not in variable_types:
            raise ValueError(f"Subject variable does not exist: {subject_name}.")
        subject = sas_name(variable_names[subject_name.casefold()])

        treatment = _mapping(configuration["treatment"], "treatment")
        treatment_name = str(treatment.get("source_variable") or "")
        treatment_kind = variable_types.get(treatment_name.casefold())
        if treatment_kind is None:
            raise ValueError(f"Treatment variable does not exist: {treatment_name}.")
        if treatment.get("missing_policy") != "error":
            raise ValueError("Unsupported Categorical treatment missing policy.")
        if treatment.get("level_order") != "resolved":
            raise ValueError("Categorical treatment level_order must be 'resolved'.")
        levels_value = treatment.get("resolved_levels")
        if not isinstance(levels_value, list):
            raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
                "treatment.resolved_levels must be an array."
            )
        levels: list[dict[str, object]] = []
        seen_levels: set[str] = set()
        for index, entry in enumerate(levels_value, start=1):
            level = _mapping(entry, "treatment.resolved_levels[]")
            if "value" not in level or "label" not in level:
                raise ValueError("Each treatment level requires value and label.")
            value = level["value"]
            if value is None:
                raise ValueError("Resolved treatment levels cannot contain missing values.")
            key = repr(value)
            if key in seen_levels:
                raise ValueError("Resolved treatment levels must be unique.")
            seen_levels.add(key)
            levels.append(
                {
                    "order": index,
                    "value": value,
                    "label": str(level["label"]),
                    "literal": _literal(value, treatment_kind, "treatment level"),
                }
            )

        total = _mapping(configuration["total"], "total")
        if not isinstance(total.get("enabled"), bool):
            raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
                "total.enabled must be true or false."
            )
        if total.get("method") != "recompute_from_analysis_universe":
            raise ValueError("Unsupported Categorical total method.")
        total_enabled = bool(total["enabled"])
        output_columns = len(levels) + (1 if total_enabled else 0)

        sort = _mapping(configuration["sort"], "sort")
        if sort.get("items") != "configured_order":
            raise ValueError("Unsupported Categorical item sorting contract.")
        for name in ("contexts", "levels"):
            block = _mapping(sort.get(name), f"sort.{name}")
            expected = {
                "method": "runtime_value_ascending",
                "character_collation": "case_insensitive",
                "numeric_order": "numeric",
                "missing": "last",
            }
            if any(block.get(key) != value for key, value in expected.items()):
                raise ValueError(f"Unsupported Categorical {name} sorting contract.")

        calculation = _mapping(configuration["calculation"], "calculation")
        expected_calculation = {
            "reference_engine": "python_categorical_v1",
            "numerator": count_type,
            "numerator_filter_scope": "source_only",
            "denominator_filter_scope": "independent",
            "item_filter_applies_to_denominator": False,
            "treatment_missing": "error",
            "percent_method": "freq_divided_by_denom_times_100",
            "total_method": "recompute_from_analysis_universe",
        }
        if any(calculation.get(key) != value for key, value in expected_calculation.items()):
            raise ValueError("Unsupported Categorical calculation contract.")

        display = _mapping(configuration["display"], "display")
        digits = display.get("percent_digits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 4:
            raise ValueError("Categorical percent_digits must be an integer from 0 to 4.")
        if display.get("rounding") != "half_up":
            raise ValueError("Categorical display rounding must be 'half_up'.")
        if display.get("zero_denominator_display") != "0 (—)":
            raise ValueError("Unsupported Categorical zero denominator display.")
        if display.get("level_indent_spaces") != 4 or display.get("header_rows") is not True:
            raise ValueError("Unsupported Categorical row display contract.")

        output = _mapping(configuration["output"], "output")
        wide_output = _mapping(output.get("wide"), "output.wide")
        long_output = _mapping(output.get("long"), "output.long")
        expected_wide = {
            "item_column": "item",
            "item_label": "Event",
            "treatment_column_pattern": "col{index}",
            "treatment_label_pattern": "{label} n (%)",
        }
        if output.get("layout") != "wide_and_long" or any(
            wide_output.get(key) != value for key, value in expected_wide.items()
        ):
            raise ValueError("Unsupported Categorical output contract.")
        expected_long = [
            "item_order",
            "item_variable",
            "item_label",
            "context",
            "level",
            "treatment",
            "trt_order",
            "freq",
            "denom",
            "pct",
        ]
        if long_output.get("columns") != expected_long:
            raise ValueError("Unsupported Categorical long output columns.")

        targets = _mapping(configuration["targets"], "targets")
        sas_target = _mapping(targets.get("sas"), "targets.sas")
        source_reference = _dataset_reference(
            sas_target.get("source_library"),
            sas_target.get("source_member"),
            "targets.sas source",
        )
        output_dataset = _output_reference(
            sas_target.get("output_dataset"), "targets.sas.output_dataset"
        )
        long_output_dataset = _output_reference(
            sas_target.get("long_output_dataset"),
            "targets.sas.long_output_dataset",
        )

        denominator = _mapping(configuration["denominator"], "denominator")
        denominator_type = denominator.get("type")
        if denominator_type not in {
            "population",
            "nonmissing",
            "baseline_postbaseline",
        }:
            raise ValueError(f"Unsupported Categorical denominator: {denominator_type!r}.")
        expected_subject_missing = (
            "exclude_for_eligibility"
            if denominator_type == "baseline_postbaseline"
            else "exclude"
            if count_type == "distinct_subjects"
            else "not_applicable"
        )
        if (
            count.get("subject_missing") != expected_subject_missing
            or calculation.get("subject_missing") != expected_subject_missing
        ):
            raise ValueError("Unsupported Categorical subject missing contract.")
        denominator_context: dict[str, object] = {"type": denominator_type}
        denominator_types = variable_types
        denominator_names = variable_names
        denominator_treatment = sas_name(variable_names[treatment_name.casefold()])
        denominator_subject = subject
        if denominator_type == "population":
            population = _mapping(denominator.get("population"), "denominator.population")
            population_input = _input(population.get("input"), "denominator.population.input")
            population_variables = _mapping(
                population.get("variables"), "denominator.population.variables"
            )
            population_types: dict[str, str] = {}
            population_names: dict[str, str] = {}
            for name, metadata in population_variables.items():
                kind = _mapping(
                    metadata, f"denominator.population.variables.{name}"
                ).get("type")
                if kind not in {"character", "numeric"}:
                    raise ValueError(f"Unsupported population variable type: {kind!r}.")
                population_types[str(name).casefold()] = str(kind)
                population_names[str(name).casefold()] = str(name)
            population_treatment = str(population.get("treatment_variable") or "")
            if population_treatment.casefold() not in population_types:
                raise ValueError("Population treatment variable does not exist.")
            if population_types[population_treatment.casefold()] != treatment_kind:
                raise ValueError("Source and population treatment types must match.")
            if subject_name.casefold() not in population_types:
                raise ValueError("Population subject variable does not exist.")
            population_reference = _dataset_reference(
                sas_target.get("population_library"),
                sas_target.get("population_member"),
                "targets.sas population",
            )
            denominator_context["population"] = {
                "input": population_input,
                "library": str(sas_target.get("population_library")),
                "reference": population_reference,
                "filter": _filter(population.get("filter"), "denominator.population.filter"),
                "treatment": sas_name(population_names[population_treatment.casefold()]),
                "subject": sas_name(population_names[subject_name.casefold()]),
            }
            denominator_types = population_types
            denominator_names = population_names
            denominator_treatment = denominator_context["population"]["treatment"]
            denominator_subject = denominator_context["population"]["subject"]
        elif denominator_type == "nonmissing":
            if denominator.get("base_filter") != "numerator.filter":
                raise ValueError("Unsupported Non-missing denominator filter scope.")
            analysis_name = str(denominator.get("analysis_value_variable") or "")
            if analysis_name.casefold() not in variable_types:
                raise ValueError("Non-missing analysis value variable does not exist.")
            denominator_context["analysis_value"] = sas_name(
                variable_names[analysis_name.casefold()]
            )
        else:
            if count_type != "records":
                raise ValueError("Baseline + Postbaseline n1 requires record count.")
            analysis_name = str(denominator.get("analysis_value_variable") or "")
            if analysis_name.casefold() not in variable_types:
                raise ValueError("n1 analysis value variable does not exist.")
            eligibility = _mapping(denominator.get("eligibility"), "denominator.eligibility")
            expected_eligibility = {
                "base_filter": "numerator.filter",
                "match_variables": "treatment_subject_and_item_context",
                "baseline_analysis_nonmissing": True,
                "postbaseline_analysis_nonmissing": True,
                "numerator_source": "eligible_postbaseline_records",
                "denominator_source": "eligible_postbaseline_records",
            }
            if any(eligibility.get(key) != value for key, value in expected_eligibility.items()):
                raise ValueError("Unsupported Categorical n1 eligibility contract.")
            denominator_context.update(
                {
                    "analysis_value": sas_name(variable_names[analysis_name.casefold()]),
                    "baseline_filter": _filter(
                        denominator.get("baseline_filter"), "denominator.baseline_filter"
                    ),
                    "postbaseline_filter": _filter(
                        denominator.get("postbaseline_filter"),
                        "denominator.postbaseline_filter",
                    ),
                }
            )

        items_value = configuration["items"]
        if not isinstance(items_value, list) or not items_value:
            raise ValueError("Categorical items must be a non-empty array.")
        used: set[str] = set()
        items: list[dict[str, object]] = []
        seen_items: set[str] = set()
        for order, item_value in enumerate(items_value, start=1):
            item = _mapping(item_value, f"items[{order - 1}]")
            variable_name = str(item.get("variable") or "")
            folded = variable_name.casefold()
            if folded not in variable_types:
                raise ValueError(f"Categorical item variable does not exist: {variable_name}.")
            if folded in seen_items:
                raise ValueError("Categorical item variables must be unique.")
            seen_items.add(folded)
            missing_level = _mapping(item.get("missing_level"), "item.missing_level")
            if not isinstance(missing_level.get("include"), bool):
                raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
                    "item.missing_level.include must be true or false."
                )
            if missing_level.get("label") != "(Missing)":
                raise ValueError("Unsupported Categorical missing level label.")
            level_order = _mapping(item.get("level_order"), "item.level_order")
            if level_order.get("method") != "runtime_value_ascending":
                raise ValueError("Unsupported Categorical item level order.")
            context_values = item.get("context_variables")
            if not isinstance(context_values, list):
                raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
                    "item.context_variables must be an array."
                )
            contexts: list[dict[str, object]] = []
            seen_contexts: set[str] = set()
            for index, context_name_value in enumerate(context_values, start=1):
                context_name = str(context_name_value)
                context_folded = context_name.casefold()
                if context_folded not in variable_types:
                    raise ValueError(f"Context variable does not exist: {context_name}.")
                if context_folded in seen_contexts:
                    raise ValueError("Item context variables must be unique.")
                if denominator_type == "population":
                    if context_folded not in denominator_types:
                        raise ValueError(
                            f"Population context variable does not exist: {context_name}."
                        )
                    if denominator_types[context_folded] != variable_types[context_folded]:
                        raise ValueError(
                            f"Population context variable type differs: {context_name}."
                        )
                seen_contexts.add(context_folded)
                contexts.append(
                    {
                        "index": index,
                        "name": sas_name(variable_names[context_folded]),
                        "denominator_name": sas_name(denominator_names[context_folded]),
                        "label": variable_names[context_folded],
                        "kind": variable_types[context_folded],
                        "length": variable_lengths[context_folded],
                        "alias": f"ctx{index}",
                    }
                )
            token = _safe_token(variable_name)
            members = {
                "denominator": _work_member("den", token, used),
                "numerator": _work_member("num", token, used),
                "calculation": _work_member("calc", token, used),
                "rows": _work_member("row", token, used),
                "long": _work_member("long", token, used),
            }
            if denominator_type == "baseline_postbaseline":
                members.update(
                    {
                        "baseline": _work_member("base", token, used),
                        "postbaseline": _work_member("post", token, used),
                        "eligible": _work_member("elig", token, used),
                    }
                )
            items.append(
                {
                    "order": order,
                    "variable": sas_name(variable_names[folded]),
                    "variable_label": variable_names[folded],
                    "label": str(item.get("label") or variable_names[folded]),
                    "kind": variable_types[folded],
                    "length": variable_lengths[folded],
                    "contexts": contexts,
                    "include_missing": bool(missing_level["include"]),
                    "members": members,
                }
            )

        return {
            "version": 1,
            "source": source,
            "source_library": str(sas_target.get("source_library")),
            "source_reference": source_reference,
            "numerator_filter": numerator_filter,
            "treatment": sas_name(variable_names[treatment_name.casefold()]),
            "subject": subject,
            "count_type": count_type,
            "levels": levels,
            "total_enabled": total_enabled,
            "total_order": len(levels) + 1,
            "output_columns": output_columns,
            "denominator": denominator_context,
            "denominator_treatment": denominator_treatment,
            "denominator_subject": denominator_subject,
            "items": items,
            "digits": digits,
            "round_increment": "1" if digits == 0 else f"{10 ** (-digits):.{digits}f}",
            "percent_format": f"{4 + digits}.{digits}",
            "output_dataset": output_dataset,
            "long_output_dataset": long_output_dataset,
        }

    def generate(self, configuration: dict[str, object]) -> str:
        if not isinstance(configuration, Mapping):
            raise ValueError(  # noqa: TRY004 - invalid contract is reported uniformly
                "Categorical SAS configuration must be an object."
            )
        try:
            context = self._prepare(configuration)
            return self.environment.get_template("categorical_table.sas.j2").render(
                **context
            )
        except (KeyError, TypeError, TemplateError) as error:
            raise ValueError(f"Unable to render the Categorical SAS template: {error}") from error


__all__ = ["SasCategoricalGenerator"]

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
        raise TypeError(f"Shift Table {label} must be an object.")
    return value


def _levels(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Shift Table {label} must be a nonempty list.")
    rows = [_mapping(row, f"{label} item") for row in value]
    orders = [row.get("order") for row in rows]
    if any(
        isinstance(order, bool) or not isinstance(order, int) or order < 1
        for order in orders
    ):
        raise ValueError(f"Shift Table {label} orders must be positive integers.")
    if len(orders) != len(set(orders)):
        raise ValueError(f"Shift Table {label} orders contain duplicates.")
    return sorted(rows, key=lambda row: int(row["order"]))


def _reference(value: object, label: str) -> str:
    text = str(value or "")
    parts = text.split(".")
    if len(parts) != 2 or not all(_SAS_NAME.fullmatch(part) for part in parts):
        raise ValueError(f"{label} must be a SAS library.member reference.")
    if len(parts[0]) > 8:
        raise ValueError(f"{label} library must not exceed 8 characters.")
    return text


def _literal(value: object, kind: str, label: str) -> str:
    if kind == "character":
        if not isinstance(value, str):
            raise ValueError(f"{label} must be character.")
        return sas_string(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric.")
    return format(float(value), ".15g")


def _normalize_sas_spacing(code: str) -> str:
    """Keep at most one blank line between generated SAS code blocks."""
    return re.sub(r"(?:[ \t]*\n){3,}", "\n\n", code)


class SasShiftTableGenerator:
    """Render readable SAS from the Shift Table JSON v1 contract."""

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
        if configuration.get("type") != "shift_table":
            raise ValueError("The configuration is not a Shift Table configuration.")
        if configuration.get("version") != 1:
            raise ValueError("SAS generation requires Shift Table configuration v1.")
        source = _mapping(configuration.get("input"), "input")
        if source.get("kind") == "merge":
            raise ValueError(
                "SAS code generation for merged Shift Table sources is not available."
            )
        if (
            source.get("kind") != "sas"
            or str(source.get("format", "")).casefold() != "sas7bdat"
        ):
            raise ValueError("Shift Table SAS generation requires a sas7bdat source.")
        metadata = _mapping(configuration.get("variable_metadata"), "variable_metadata")
        variables = _mapping(configuration.get("variables"), "variables")
        ordering = _mapping(configuration.get("ordering"), "ordering")
        calculation = _mapping(configuration.get("calculation"), "calculation")
        display = _mapping(configuration.get("display"), "display")
        population = _mapping(configuration.get("population"), "population")
        targets = _mapping(configuration.get("targets"), "targets")
        sas_targets = _mapping(targets.get("sas"), "targets.sas")
        if calculation.get("reference_engine") != "python_shift_table_v1":
            raise ValueError("Unsupported Shift Table reference engine.")
        required_calculation = {
            "count": "distinct_subjects",
            "denominator": "parameter_visit_treatment_complete_grade_subjects",
            "duplicate_policy": "warn_and_continue",
            "missing_grade_policy": "exclude_with_summary",
            "total_policy": "independent_distinct_subject",
        }
        for name, expected in required_calculation.items():
            if calculation.get(name) != expected:
                raise ValueError(f"Unsupported Shift Table {name} contract.")
        if display.get("treatment_layout") != "across_columns":
            raise ValueError("Shift Table v1 only supports treatment across columns.")
        if display.get("rounding") != "half_up":
            raise ValueError("Shift Table SAS generation requires half_up rounding.")
        digits = display.get("percent_digits")
        if (
            isinstance(digits, bool)
            or not isinstance(digits, int)
            or not 0 <= digits <= 4
        ):
            raise ValueError("Shift Table percent_digits must be between 0 and 4.")

        lookup: dict[str, Mapping[str, object]] = {
            str(name).casefold(): _mapping(block, f"variable_metadata.{name}")
            for name, block in metadata.items()
        }
        names: dict[str, str] = {str(name).casefold(): str(name) for name in metadata}
        resolved: dict[str, str] = {}
        kinds: dict[str, str] = {}
        for role in (
            "subject",
            "parameter",
            "visit",
            "treatment",
            "baseline_grade",
            "post_grade",
            "analysis_flag",
        ):
            requested = str(variables.get(role) or "")
            name = names.get(requested.casefold())
            if name is None:
                raise ValueError(f"Unknown Shift Table variable: {requested}.")
            kind = lookup[name.casefold()].get("type")
            if kind not in {"character", "numeric"}:
                raise ValueError(f"Unsupported type for {name}: {kind}.")
            resolved[role] = sas_name(name)
            kinds[role] = str(kind)

        parameter_levels = _levels(ordering.get("parameters"), "parameters")
        visit_levels = _levels(ordering.get("visits"), "visits")
        treatment_levels = _levels(ordering.get("treatments"), "treatments")
        grade_levels = _levels(ordering.get("grade_levels"), "grade_levels")

        def prepared_levels(
            rows: list[Mapping[str, object]], kind: str, label: str
        ) -> list[dict[str, object]]:
            prepared: list[dict[str, object]] = []
            for row in rows:
                display_label = str(row.get("label") or "")
                if not display_label:
                    raise ValueError(f"Shift Table {label} labels cannot be empty.")
                prepared.append(
                    {
                        "raw": row.get("raw"),
                        "literal": _literal(row.get("raw"), kind, label),
                        "label": display_label,
                        "order": int(row["order"]),
                    }
                )
            return prepared

        prepared_parameters = prepared_levels(
            parameter_levels, kinds["parameter"], "parameter"
        )
        prepared_visits = prepared_levels(visit_levels, kinds["visit"], "visit")
        prepared_treatments = prepared_levels(
            treatment_levels, kinds["treatment"], "treatment"
        )
        prepared_grades = prepared_levels(
            grade_levels, kinds["baseline_grade"], "grade"
        )
        if kinds["baseline_grade"] != kinds["post_grade"]:
            raise ValueError("Baseline and post-baseline grade types must match.")

        dataset_filter = _mapping(configuration.get("dataset_filter"), "dataset_filter")
        if dataset_filter.get("language") != "sas_like" or "ast" not in dataset_filter:
            raise ValueError("dataset_filter must contain a sas_like AST.")
        where_expression = sas_filter_expression(
            dataset_filter.get("ast"), compact_and=True
        )
        flag_literal = _literal(
            variables.get("analysis_flag_value"),
            kinds["analysis_flag"],
            "analysis flag value",
        )

        population_input = _mapping(population.get("input"), "population.input")
        if (
            population_input.get("kind") != "sas"
            or str(population_input.get("format", "")).casefold() != "sas7bdat"
        ):
            raise ValueError(
                "Shift Table Population SAS generation requires a sas7bdat source."
            )
        population_metadata = _mapping(
            population.get("variable_metadata"), "population.variable_metadata"
        )
        population_names = {
            str(name).casefold(): str(name) for name in population_metadata
        }
        population_types = {
            str(name).casefold(): str(
                _mapping(block, f"population.variable_metadata.{name}").get("type")
            )
            for name, block in population_metadata.items()
        }
        population_subject_requested = str(population.get("subject_variable") or "")
        population_treatment_requested = str(population.get("treatment_variable") or "")
        population_subject_name = population_names.get(
            population_subject_requested.casefold()
        )
        population_treatment_name = population_names.get(
            population_treatment_requested.casefold()
        )
        if population_subject_name is None or population_treatment_name is None:
            raise ValueError("Population subject or treatment variable does not exist.")
        population_treatment_kind = population_types[
            population_treatment_name.casefold()
        ]
        if population_treatment_kind != kinds["treatment"]:
            raise ValueError(
                "Analysis and Population treatment variables must have matching storage types."
            )
        population_filter = _mapping(population.get("filter"), "population.filter")
        if (
            population_filter.get("language") != "sas_like"
            or "ast" not in population_filter
        ):
            raise ValueError("population.filter must contain a sas_like AST.")
        population_where = sas_filter_expression(
            population_filter.get("ast"), compact_and=True
        )
        source_reference = _reference(
            f"{sas_targets.get('source_library')}.{sas_targets.get('source_member')}",
            "Shift Table source",
        )
        population_reference = _reference(
            f"{sas_targets.get('population_library')}.{sas_targets.get('population_member')}",
            "Shift Table population source",
        )
        output_reference = _reference(
            sas_targets.get("output_dataset"), "output_dataset"
        )
        long_reference = _reference(
            sas_targets.get("long_output_dataset"), "long_output_dataset"
        )
        include_total = calculation.get("include_overall_total")
        if not isinstance(include_total, bool):
            raise TypeError("include_overall_total must be boolean.")
        round_unit = "1" if digits == 0 else "0." + "0" * (digits - 1) + "1"
        return {
            "source_reference": source_reference,
            "population_reference": population_reference,
            "output_reference": output_reference,
            "long_reference": long_reference,
            "variables": resolved,
            "kinds": kinds,
            "flag_literal": flag_literal,
            "where_expression": where_expression,
            "population_where": population_where,
            "population_subject": sas_name(population_subject_name),
            "population_treatment": sas_name(population_treatment_name),
            "parameters": prepared_parameters,
            "visits": prepared_visits,
            "treatments": prepared_treatments,
            "grades": prepared_grades,
            "include_total": include_total,
            "total_treatment_order": len(prepared_treatments) + 1,
            "total_grade_order": len(prepared_grades) + 1,
            "percent_digits": digits,
            "percent_format": f"{digits + 4}.{digits}",
            "round_unit": round_unit,
            "grade_missing": "''" if kinds["baseline_grade"] == "character" else ".",
            "treatment_missing": "''" if kinds["treatment"] == "character" else ".",
        }

    def generate(self, configuration: Mapping[str, object]) -> str:
        context = self._prepare(configuration)
        try:
            rendered = self.environment.get_template("shift_table.sas.j2").render(
                **context
            )
            return _normalize_sas_spacing(rendered)
        except TemplateError as error:
            raise ValueError(f"Unable to render Shift Table SAS: {error}") from error


__all__ = ["SasShiftTableGenerator"]

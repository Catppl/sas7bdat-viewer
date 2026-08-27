from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...resources import resource_path
from .filter_renderer import sas_filter_expression, sas_name, sas_string


_SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED = (
    "input", "variables", "dataset_filter", "hierarchy", "count",
    "treatment", "denominator", "rows", "sort", "total", "calculation",
    "display", "targets",
)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"AE configuration block {name} must be an object.")
    return value


def _sas_ref(value: object, name: str) -> str:
    text = str(value or "")
    if not _SAS_NAME.fullmatch(text):
        raise ValueError(f"{name} must be a valid SAS name.")
    return text


def _input(value: object, name: str) -> dict[str, str]:
    block = _mapping(value, name)
    kind = block.get("kind")
    if kind == "merge":
        raise ValueError("SAS code generation for merged AE sources is not available yet.")
    if kind != "sas":
        raise ValueError(f"{name}.kind must be 'sas'.")
    fmt = str(block.get("format") or "").casefold()
    if fmt not in {"sas7bdat", "xpt"}:
        raise ValueError(f"Unsupported {name} format for SAS generation: {fmt}.")
    dataset = _sas_ref(block.get("dataset"), f"{name}.dataset")
    path = str(block.get("source_path") or "")
    directory = str(block.get("source_directory") or "")
    if not path:
        raise ValueError(f"{name}.source_path is required.")
    if fmt == "sas7bdat" and not directory:
        raise ValueError(f"{name}.source_directory is required for SAS7BDAT input.")
    return {"format": fmt, "dataset": dataset, "path": path, "directory": directory}


def _filter(value: object, name: str) -> str:
    block = _mapping(value, name)
    if block.get("language") != "sas_like" or "ast" not in block:
        raise ValueError(f"{name} must contain language 'sas_like' and ast.")
    text = block.get("text", "")
    if not isinstance(text, str):
        raise ValueError(f"{name}.text must be a string.")
    if text.strip() and block.get("ast") is None:
        raise ValueError(f"{name}.ast cannot be empty when text is provided.")
    try:
        return sas_filter_expression(block.get("ast"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported {name} AST: {exc}") from exc


def _variable(value: object, label: str, variables: Mapping[str, object]) -> str:
    text = str(value or "")
    if text.casefold() not in {str(key).casefold() for key in variables}:
        raise ValueError(f"{label} does not exist in variables metadata.")
    return text


def _raw_value(variable: str, kind: str) -> str:
    """Render a raw-value expression, never a SAS formatted value."""
    name = sas_name(variable)
    return f"strip({name})" if kind == "character" else f"strip(put({name}, best32.))"


class SasAeTableGenerator:
    """Generate readable SAS from AE JSON v1.

    The ``resolved_hierarchy`` member is deliberately not consumed.  It is a
    Python run snapshot; the reusable sort contract is evaluated at runtime.
    """

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
            raise ValueError("AE SAS generator requires a JSON object.")
        if configuration.get("type") != "ae_soc_pt_table":
            raise ValueError("The configuration is not an AE SOC/PT Table configuration.")
        if configuration.get("version") != 1:
            raise ValueError("SAS generation requires AE SOC/PT Table configuration v1.")
        missing = [key for key in _REQUIRED if key not in configuration]
        if missing:
            raise ValueError("AE configuration is missing: " + ", ".join(missing))

        source = _input(configuration["input"], "input")
        variables = _mapping(configuration["variables"], "variables")
        variable_types: dict[str, str] = {}
        for name, metadata in variables.items():
            kind = _mapping(metadata, f"variables.{name}").get("type")
            if kind not in {"character", "numeric"}:
                raise ValueError(f"Unsupported type for variable {name!r}.")
            variable_types[str(name).casefold()] = str(kind)
        hierarchy = _mapping(configuration["hierarchy"], "hierarchy")
        if hierarchy.get("type") != "soc_pt":
            raise ValueError("AE hierarchy type must be 'soc_pt'.")
        soc = _variable(hierarchy.get("soc_variable"), "SOC variable", variables)
        pt = _variable(hierarchy.get("pt_variable"), "PT variable", variables)
        missing_block = _mapping(hierarchy.get("missing"), "hierarchy.missing")
        if missing_block.get("policy") not in {"exclude", "uncoded"} or missing_block.get("label") != "Uncoded":
            raise ValueError("Unsupported AE hierarchy missing policy.")

        count = _mapping(configuration["count"], "count")
        if count.get("type") != "distinct" or str(count.get("variable", "")).casefold() != "usubjid":
            raise ValueError("AE SAS generation supports only distinct USUBJID counts.")
        subject = _variable(count.get("variable"), "Count variable", variables)
        treatment = _mapping(configuration["treatment"], "treatment")
        trt = _variable(treatment.get("variable"), "Treatment variable", variables)
        if treatment.get("missing_policy") != "error" or treatment.get("level_order") != "resolved":
            raise ValueError("Unsupported AE treatment contract.")

        calculation = _mapping(configuration["calculation"], "calculation")
        expected = {
            "reference_engine": "python_ae_soc_pt_v1", "numerator": "distinct_subjects",
            "soc_count": "recompute_distinct_subjects", "pt_count": "recompute_distinct_subjects",
            "subject_missing": "exclude", "treatment_missing": "error",
            "percent_method": "freq_divided_by_denom_times_100", "total_method": "recompute_distinct_subjects",
        }
        for key, expected_value in expected.items():
            if calculation.get(key) != expected_value:
                raise ValueError(f"Unsupported AE calculation contract: {key}.")

        rows = _mapping(configuration["rows"], "rows")
        if not isinstance(rows.get("include_any_ae"), bool):
            raise ValueError("rows.include_any_ae must be true or false.")
        total = _mapping(configuration["total"], "total")
        if not isinstance(total.get("enabled"), bool) or total.get("method") != "recompute_distinct_subjects":
            raise ValueError("Unsupported AE Total contract.")
        sort = _mapping(configuration["sort"], "sort")
        for level in ("soc", "pt"):
            rule = _mapping(sort.get(level), f"sort.{level}")
            if rule.get("by") != "total_frequency" or rule.get("direction") != "desc" or rule.get("tie_breaker") != "alphabetical":
                raise ValueError(f"Unsupported AE {level} sorting contract.")
        display = _mapping(configuration["display"], "display")
        digits = display.get("percent_digits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 4:
            raise ValueError("AE percent_digits must be an integer from 0 to 4.")
        if display.get("rounding") != "half_up" or display.get("zero_denominator_display") != "0 (—)":
            raise ValueError("Unsupported AE display contract.")

        denominator = _mapping(configuration["denominator"], "denominator")
        dtype = denominator.get("type")
        population = None
        population_source = None
        population_filter = ""
        if dtype == "population":
            population = _mapping(denominator.get("population"), "denominator.population")
            population_source = _input(
                population.get("input"), "denominator.population.input"
            )
            pvars = _mapping(population.get("variables"), "denominator.population.variables")
            pnames = {str(key).casefold() for key in pvars}
            if trt.casefold() not in pnames or subject.casefold() not in pnames:
                raise ValueError("Population metadata must contain treatment and USUBJID.")
            for key in (trt, subject):
                metadata = next(value for name, value in pvars.items() if str(name).casefold() == key.casefold())
                if _mapping(metadata, f"denominator.population.variables.{key}").get("type") != variable_types[key.casefold()]:
                    raise ValueError(f"Population variable {key} must have the same type as the AE source.")
            population_filter = _filter(population.get("filter"), "denominator.population.filter")
        elif dtype != "same_universe":
            raise ValueError("Unsupported AE denominator type.")

        targets = _mapping(configuration["targets"], "targets")
        sas_target = _mapping(targets.get("sas"), "targets.sas")
        output = str(sas_target.get("output_dataset") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", output):
            raise ValueError("targets.sas.output_dataset must be a SAS library.member reference.")
        source_library = _sas_ref(sas_target.get("source_library"), "targets.sas.source_library")
        source_member = _sas_ref(sas_target.get("source_member"), "targets.sas.source_member")
        return {
            "source": source, "variables": variables, "soc": soc, "pt": pt,
            "types": variable_types, "subject": subject, "treatment": trt,
            "soc_type": variable_types[soc.casefold()], "pt_type": variable_types[pt.casefold()],
            "treatment_type": variable_types[trt.casefold()], "subject_type": variable_types[subject.casefold()],
            "dataset_filter": _filter(configuration["dataset_filter"], "dataset_filter"),
            "missing_policy": str(missing_block["policy"]), "denominator": dtype,
            "population": population, "population_input": population.get("input") if population else None,
            "population_source": population_source,
            "population_filter": population_filter, "include_any": bool(rows["include_any_ae"]),
            "any_label": str(rows.get("any_ae_label") or "Any AE"),
            "include_total": bool(total["enabled"]), "digits": digits, "output": output,
            "source_library": source_library, "source_member": source_member,
            "soc_missing_literal": "'Uncoded'" if str(missing_block["policy"]) == "uncoded" else "''",
            "pt_missing_literal": "'Uncoded'" if str(missing_block["policy"]) == "uncoded" else "''",
            "raw_treatment": _raw_value(trt, variable_types[trt.casefold()]),
            "raw_subject": _raw_value(subject, variable_types[subject.casefold()]),
            "raw_soc": _raw_value(soc, variable_types[soc.casefold()]),
            "raw_pt": _raw_value(pt, variable_types[pt.casefold()]),
            "row_order_offset": 1 if bool(rows["include_any_ae"]) else 0,
            "max_column": "&ntrt+1" if bool(total["enabled"]) else "&ntrt",
            "percent_increment": 10 ** (-digits),
            "level_source": "pop0" if population_source is not None else "ae0",
        }

    def generate(self, configuration: Mapping[str, object]) -> str:
        ctx = self._prepare(configuration)
        try:
            return self.environment.get_template("ae_table.sas.j2").render(**ctx)
        except (OSError, TemplateError) as error:
            raise ValueError(f"Unable to render the AE SAS template: {error}") from error


__all__ = ["SasAeTableGenerator"]

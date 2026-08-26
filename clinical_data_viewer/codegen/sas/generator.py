from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...resources import resource_path
from .filter_renderer import sas_filter_expression, sas_name, sas_string

_PROC_MEANS_KEYWORDS = {
    "N": "n",
    "NMISS": "nmiss",
    "MEAN": "mean",
    "SD": "std",
    "SE": "stderr",
    "MEDIAN": "median",
    "Q1": "q1",
    "Q3": "q3",
    "MIN": "min",
    "MAX": "max",
    "LCLM": "lclm",
    "UCLM": "uclm",
}


def _sas_string(value: object) -> str:
    return sas_string(value)


def _sas_name(value: object) -> str:
    return sas_name(value)


def _safe_token(value: object) -> str:
    """Build a readable lowercase token for a SAS member name."""
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_").lower()
    if not normalized or normalized[0].isdigit():
        normalized = f"var_{normalized}" if normalized else "variable"

    return normalized


def _work_member(value: object, used: set[str]) -> str:
    """Build a readable, unique, <=32-character WORK member name."""
    base = _safe_token(value)[:32]
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        suffix_text = str(suffix)
        candidate = base[: 32 - len(suffix_text)] + suffix_text
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _sas_filter_expression(expression: dict[str, object] | None) -> str:
    return sas_filter_expression(expression)


class SasProcMeansGenerator:
    """Render a standalone SAS program from a PROC MEANS v3 configuration."""

    def __init__(self, template_directory: Path | None = None) -> None:
        directory = template_directory or resource_path(
            "clinical_data_viewer/codegen/sas/templates"
        )
        self.environment = Environment(
            loader=FileSystemLoader(str(directory)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters["sas_string"] = _sas_string
        self.environment.filters["sas_name"] = _sas_name

    def generate(self, configuration: dict[str, object]) -> str:
        if configuration.get("type") != "proc_means":
            raise ValueError("The configuration is not a PROC MEANS configuration.")
        if configuration.get("version") != 3:
            raise ValueError("SAS generation requires PROC MEANS configuration v3.")

        context = configuration.copy()
        input_format = str(context["input"].get("format", "")).casefold()
        if input_format not in {"sas7bdat", "xpt"}:
            raise ValueError(
                f"Unsupported input format for SAS generation: {input_format}"
            )
        statistics = list(context["statistics"])
        unknown_statistics = sorted(
            set(statistics) - set(_PROC_MEANS_KEYWORDS) - {"SUBJECT_N"}
        )
        if unknown_statistics:
            raise ValueError(
                "Unsupported PROC MEANS statistics: " + ", ".join(unknown_statistics)
            )
        if not context["analysis_variables"]:
            raise ValueError("Select at least one Analysis Variable.")
        proc_statistics = [
            {
                "name": name,
                "keyword": _PROC_MEANS_KEYWORDS[name],
            }
            for name in statistics
            if name in _PROC_MEANS_KEYWORDS
        ]
        analysis = []
        variables = context["variables"]
        source_prefix = _safe_token(context["targets"]["sas"]["source_member"])
        used_work_members: set[str] = set()
        work_source = _work_member(f"{source_prefix}_source", used_work_members)
        work_decimal_values = _work_member(
            f"{source_prefix}_decimal_values", used_work_members
        )
        work_decimal_rules = _work_member(
            f"{source_prefix}_decimal_rules", used_work_members
        )
        for index, name in enumerate(context["analysis_variables"], start=1):
            analysis_token = _safe_token(name)[:12]
            analysis.append(
                {
                    "index": index,
                    "name": name,
                    "label": variables[name]["label"],
                    "work_stats": _work_member(
                        f"{source_prefix}_{analysis_token}_stats", used_work_members
                    ),
                    "work_subjects": _work_member(
                        f"{source_prefix}_{analysis_token}_subjects", used_work_members
                    ),
                    "work_long": _work_member(
                        f"{source_prefix}_{analysis_token}_long", used_work_members
                    ),
                    "work_decimals": _work_member(
                        f"{source_prefix}_{analysis_token}_decimals", used_work_members
                    ),
                }
            )
        offsets = context["display"]["decimal_offsets"]
        decimal_statistics = [
            {"name": name, "offset": int(offsets.get(name, 0))}
            for name in statistics
            if name not in {"SUBJECT_N", "N", "NMISS"}
        ]
        calculation = context["calculation"]
        sas_target = context["targets"]["sas"]
        context.update(
            {
                "analysis": analysis,
                "proc_statistics": proc_statistics,
                "needs_internal_n": not proc_statistics,
                "include_subject_n": "SUBJECT_N" in statistics,
                "decimal_statistics": decimal_statistics,
                "group_variables": [
                    *context["by_variables"],
                    *context["class_variables"],
                ],
                "filter_text": _sas_filter_expression(context["filter"]["ast"]),
                "options": {
                    "subject_id_variable": calculation["subject_count"]["variable"],
                    "alpha": calculation["alpha"],
                    "vardef": "df",
                    "qntldef": 5,
                },
                "sas_source_directory": context["input"]["source_directory"],
                "sas_source_path": context["input"]["source_path"],
                "sas_input_format": input_format,
                "sas_source_library": sas_target["source_library"],
                "sas_source_member": sas_target["source_member"],
                "sas_output_dataset": sas_target["output_dataset"],
                "work_source": work_source,
                "work_decimal_values": work_decimal_values,
                "work_decimal_rules": work_decimal_rules,
                "sas_decimal_tolerance": 1e-12,
            }
        )
        try:
            return self.environment.get_template("proc_means.sas.j2").render(**context)
        except (OSError, TemplateError) as error:
            raise ValueError(f"Unable to render the SAS template: {error}") from error

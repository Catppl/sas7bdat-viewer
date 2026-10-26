from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...resources import resource_path
from .filter_renderer import sas_filter_expression, sas_name, sas_string


_STATISTICS = {
    "subjects": "subjects",
    "n": "n",
    "nmiss": "nmiss",
    "mean": "mean",
    "std": "std",
    "stderr": "stderr",
    "median": "median",
    "q1": "q1",
    "q3": "q3",
    "min": "min",
    "max": "max",
    "lclm": "lclm",
    "uclm": "uclm",
}


def _safe_token(value: object) -> str:
    token = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_").lower()
    if not token or token[0].isdigit():
        token = f"var_{token}" if token else "var"
    return token[:24]


def _sas_string(value: object) -> str:
    return sas_string(value)


def _sas_name(value: object) -> str:
    return sas_name(value)


def _sas_filter(expression: dict[str, object] | None) -> str:
    return sas_filter_expression(expression) if expression else ""


def _reference(value: object, field: str) -> tuple[str, str]:
    text = str(value or "")
    parts = text.split(".")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts):
        raise ValueError(f"{field} must be a SAS library.member reference.")
    return parts[0], parts[1]


class SasProcMeansVerticalGenerator:
    """Render a readable SAS program for PROC MEANS Table Builder (Vertical)."""

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
        self.environment.filters["sas_string"] = _sas_string
        self.environment.filters["sas_name"] = _sas_name

    def _prepare(self, configuration: dict[str, object]) -> dict[str, object]:
        if configuration.get("type") != "proc_means_vertical":
            raise ValueError("The configuration is not a vertical PROC MEANS configuration.")
        if configuration.get("version") != 1:
            raise ValueError("SAS generation requires vertical PROC MEANS configuration v1.")

        input_block = configuration.get("input")
        if not isinstance(input_block, dict):
            raise ValueError("input is required in the vertical PROC MEANS configuration.")
        if input_block.get("kind") == "merge":
            raise ValueError(
                "SAS code generation for merged vertical PROC MEANS sources is not available yet."
            )
        input_format = str(input_block.get("format", "")).casefold()
        if input_format not in {"sas7bdat", "xpt"}:
            raise ValueError(f"Unsupported input format for SAS generation: {input_format}")

        variables = configuration.get("variable_metadata")
        variable_metadata = variables if isinstance(variables, dict) else configuration.get("variables")
        if not isinstance(variable_metadata, dict):
            raise ValueError("variable_metadata is required in the vertical PROC MEANS configuration.")
        variable_lookup = {str(name).casefold(): metadata for name, metadata in variable_metadata.items()}

        variable_block = configuration.get("variables")
        if not isinstance(variable_block, dict):
            raise ValueError("variables is required in the vertical PROC MEANS configuration.")
        analysis_names = [str(name) for name in variable_block.get("analysis", [])]
        group_names = [str(name) for name in variable_block.get("group", [])]
        if not analysis_names:
            raise ValueError("Select at least one Analysis Variable.")
        missing = [name for name in (*analysis_names, *group_names) if name.casefold() not in variable_lookup]
        if missing:
            raise ValueError("Unknown variables: " + ", ".join(missing))

        statistics = [str(value).casefold() for value in configuration.get("statistics", [])]
        unknown = sorted(set(statistics) - set(_STATISTICS))
        if unknown:
            raise ValueError("Unsupported vertical PROC MEANS statistics: " + ", ".join(unknown))

        display = configuration.get("display")
        if not isinstance(display, dict):
            raise ValueError("display is required in the vertical PROC MEANS configuration.")
        if display.get("rounding") != "half_up":
            raise ValueError("Only half_up rounding is supported by the vertical SAS generator.")
        maximum_decimals = int(display.get("maximum_decimals", 4))
        if maximum_decimals != 4:
            raise ValueError("Vertical PROC MEANS maximum_decimals must be 4.")
        base_decimals = {str(key).casefold(): value for key, value in dict(display.get("base_decimals", {})).items()}
        offsets = {str(key).casefold(): int(value) for key, value in dict(display.get("statistic_decimals", {})).items()}

        rows: list[dict[str, object]] = []
        consumed: set[str] = set()
        labels = {str(key).casefold(): str(value) for key, value in dict(configuration.get("item", {}).get("labels", {})).items()}
        for analysis_name in analysis_names:
            token = _safe_token(analysis_name)
            metadata = variable_lookup[analysis_name.casefold()]
            if not isinstance(metadata, dict) or str(metadata.get("type", "")).casefold() != "numeric":
                raise ValueError(f"Analysis Variable {analysis_name} must be numeric.")
            rows.append(
                {
                    "index": len(rows) + 1,
                    "name": analysis_name,
                    "token": token,
                    "label": labels.get(analysis_name.casefold(), str(metadata.get("label") or analysis_name)),
                    "base_decimal": base_decimals.get(analysis_name.casefold()),
                    "offsets": offsets,
                }
            )

        statistic_rows: list[dict[str, object]] = []
        for statistic in statistics:
            if statistic in consumed:
                continue
            if statistic in {"q1", "q3"} and {"q1", "q3"}.issubset(statistics):
                statistic_rows.append({"label": "Q1, Q3", "keys": ["q1", "q3"]})
                consumed.update({"q1", "q3"})
            elif statistic in {"min", "max"} and {"min", "max"}.issubset(statistics):
                statistic_rows.append({"label": "Min, Max", "keys": ["min", "max"]})
                consumed.update({"min", "max"})
            else:
                label = {"subjects": "N", "n": "n", "nmiss": "NMISS"}.get(statistic, statistic.title())
                statistic_rows.append({"label": label, "keys": [statistic]})
                consumed.add(statistic)

        groups = []
        for index, name in enumerate(group_names, start=1):
            metadata = variable_lookup[name.casefold()]
            kind = str(metadata.get("type", "character")).casefold()
            groups.append({"name": name, "sas_name": sas_name(name), "kind": kind, "index": index})

        population = configuration.get("population")
        population = population if isinstance(population, dict) else {}
        population_input = population.get("input")
        population_input = population_input if isinstance(population_input, dict) else input_block
        population_format = str(population_input.get("format", input_format)).casefold()
        if population_format not in {"sas7bdat", "xpt"}:
            raise ValueError(f"Unsupported population input format: {population_format}")

        targets = configuration.get("targets")
        target_sas = targets.get("sas") if isinstance(targets, dict) else None
        if not isinstance(target_sas, dict):
            raise ValueError("targets.sas is required for vertical PROC MEANS SAS generation.")
        source_library = str(target_sas.get("source_library") or "analysis")
        source_member = str(target_sas.get("source_member") or input_block.get("dataset", "")).lower()
        population_library = str(target_sas.get("population_library") or source_library)
        population_member = str(target_sas.get("population_member") or population_input.get("dataset", source_member)).lower()
        output_dataset = str(target_sas.get("output_dataset") or "work.pm_vertical")
        output_library, output_member = _reference(output_dataset, "targets.sas.output_dataset")
        _reference(f"{source_library}.{source_member}", "targets.sas source")
        _reference(f"{population_library}.{population_member}", "targets.sas population")

        calculation = configuration.get("calculation")
        calculation = calculation if isinstance(calculation, dict) else {}
        confidence = float(calculation.get("confidence", 0.95))
        if not 0 < confidence < 1:
            raise ValueError("Confidence must be between 0 and 1.")

        population_variables = population.get("variables")
        population_variables = population_variables if isinstance(population_variables, dict) else variable_metadata
        treatment_name = str(variable_block.get("treatment") or "")
        population_treatment_name = str(variable_block.get("population_treatment") or treatment_name)
        treatment_group_index = next(
            (
                index
                for index, name in enumerate(group_names, start=1)
                if name.casefold() == treatment_name.casefold()
            ),
        )
        source_treatment_meta = variable_lookup.get(treatment_name.casefold(), {})
        population_treatment_meta = {
            str(name).casefold(): value for name, value in population_variables.items()
        }.get(population_treatment_name.casefold(), {})

        return {
            "version": 1,
            "source": {
                "library": source_library,
                "member": source_member,
                "directory": input_block.get("source_directory"),
                "path": input_block.get("source_path"),
                "format": input_format,
            },
            "population_source": {
                "library": population_library,
                "member": population_member,
                "directory": population_input.get("source_directory"),
                "path": population_input.get("source_path"),
                "format": population_format,
                "uses_source": population.get("input") is None,
            },
            "output_library": output_library,
            "output_member": output_member,
            "output_dataset": output_dataset,
            "source_filter": _sas_filter(configuration.get("dataset_filter", {}).get("ast")),
            "population_filter": _sas_filter(population.get("filter", {}).get("ast")),
            "analysis": rows,
            "group_variables": groups,
            "group_names": group_names,
            "statistics": statistic_rows,
            "statistic_keys": statistics,
            "proc_statistics": [key for key in statistics if key != "subjects"],
            "offsets": offsets,
            "include_total": bool(configuration.get("total", {}).get("enabled", True)),
            "treatment": treatment_name,
            "population_treatment": population_treatment_name if variable_block.get("population_treatment") else "",
            "treatment_group_index": treatment_group_index,
            "source_treatment_kind": str(source_treatment_meta.get("type", "character")).casefold(),
            "population_treatment_kind": str(population_treatment_meta.get("type", "character")).casefold(),
            "subject": "USUBJID",
            "confidence": confidence,
            "alpha": round(1.0 - confidence, 12),
            "analysis_source_library": source_library,
            "population_source_library": population_library,
        }

    def generate(self, configuration: dict[str, object]) -> str:
        context = self._prepare(configuration)
        try:
            return self.environment.get_template("proc_means_vertical.sas.j2").render(**context)
        except (OSError, TemplateError) as error:
            raise ValueError(f"Unable to render the vertical PROC MEANS template: {error}") from error


__all__ = ["SasProcMeansVerticalGenerator"]

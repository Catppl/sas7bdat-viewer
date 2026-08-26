from __future__ import annotations

import re
from collections.abc import Mapping

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


def _libname(source: Mapping[str, str], library: str) -> list[str]:
    if source["format"] == "xpt":
        return [f"libname {library} xport {sas_string(source['path'])};"]
    return [f"libname {library} {sas_string(source['directory'])};"]


def _raw_value(variable: str, kind: str) -> str:
    """Render a raw-value expression, never a SAS formatted value."""
    name = sas_name(variable)
    return f"strip({name})" if kind == "character" else f"strip(put({name}, best32.))"


class SasAeTableGenerator:
    """Generate readable SAS from AE JSON v1.

    The ``resolved_hierarchy`` member is deliberately not consumed.  It is a
    Python run snapshot; the reusable sort contract is evaluated at runtime.
    """

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
        population_filter = ""
        if dtype == "population":
            population = _mapping(denominator.get("population"), "denominator.population")
            population_input = _input(population.get("input"), "denominator.population.input")
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
            "population_filter": population_filter, "include_any": bool(rows["include_any_ae"]),
            "any_label": str(rows.get("any_ae_label") or "Any AE"),
            "include_total": bool(total["enabled"]), "digits": digits, "output": output,
            "source_library": source_library, "source_member": source_member,
        }

    def generate(self, configuration: Mapping[str, object]) -> str:
        ctx = self._prepare(configuration)
        source = ctx["source"]
        lines: list[str] = [
            "/*", "  Generated by SASDataViewer", "  AE SOC/PT Table", 
            "  Reference engine: python_ae_soc_pt_v1",
            "  Hierarchy sorting is recalculated at runtime from total frequency.", "*/", "",
            "options validvarname=any;", "",
        ]
        lines += _libname(source, ctx["source_library"])
        population_input = ctx["population_input"]
        if population_input is not None:
            pop = _input(population_input, "denominator.population.input")
            # LIBREFs are limited to eight characters in Base SAS.  Keep the
            # population reference short and distinct from the analysis libref.
            lines += _libname(pop, "pop")
        lines += ["", "/* Prepare AE source */", "data ae0;", f"    set {ctx['source_library']}.{ctx['source_member']};"]
        if ctx["dataset_filter"]:
            lines.append(f"    if not ({ctx['dataset_filter']}) then delete;")
        soc_missing = "'Uncoded'" if ctx["missing_policy"] == "uncoded" else "''"
        pt_missing = soc_missing
        lines += [
            "    length _trt _trt_key _soc _soc_key _pt _pt_key _subjid $200;",
            f"    if missing({sas_name(ctx['treatment'])}) then do; put \"ERROR: missing treatment values exist.\"; abort cancel; end;",
            f"    _trt = {_raw_value(ctx['treatment'], ctx['treatment_type'])};",
            "    _trt_key = lowcase(_trt);",
            f"    if missing({sas_name(ctx['subject'])}) then delete;",
            f"    _subjid = {_raw_value(ctx['subject'], ctx['subject_type'])};",
            f"    if missing({sas_name(ctx['soc'])}) then _soc = {soc_missing}; else _soc = {_raw_value(ctx['soc'], ctx['soc_type'])};",
            "    _soc_key = lowcase(_soc);",
            f"    if missing({sas_name(ctx['pt'])}) then _pt = {pt_missing}; else _pt = {_raw_value(ctx['pt'], ctx['pt_type'])};",
            "    _pt_key = lowcase(_pt);",
            "run;", "",
        ]
        level_source = "ae0"
        if population_input is not None:
            pop = _input(population_input, "denominator.population.input")
            lines += ["/* Prepare population denominator */", "data pop0;", f"    set pop.{pop['dataset']};"]
            if ctx["population_filter"]:
                lines.append(f"    if not ({ctx['population_filter']}) then delete;")
            lines += [
                "    length _trt _trt_key _subjid $200;",
                f"    if missing({sas_name(ctx['treatment'])}) then do; put \"ERROR: missing treatment values exist in population.\"; abort cancel; end;",
                f"    _trt = {_raw_value(ctx['treatment'], ctx['treatment_type'])};",
                "    _trt_key = lowcase(_trt);",
                f"    if missing({sas_name(ctx['subject'])}) then delete;",
                f"    _subjid = {_raw_value(ctx['subject'], ctx['subject_type'])};",
                "run;", "",
            ]
            level_source = "pop0"
        lines += [
            "/* Treatment levels are discovered from the denominator universe. */",
            "proc sql;",
            "    create table trt as",
            f"    select distinct _trt, _trt_key from {level_source}",
            "    order by _trt_key, _trt;",
            "quit;",
            "data trt;",
            "    set trt;",
            "    trt_order + 1;",
            "    trt_label = _trt;",
            "run;",
            "proc sql noprint;",
            "    select count(*) into :ntrt trimmed from trt;",
            '    select cats(\'"\', tranwrd(trt_label, \'"\', \'""\'), \' n (%)"\')',
            "        into :col_label1- from trt order by trt_order;",
            "quit;", "",
            "/* Denominators (never filtered by SOC/PT) */",
            "proc sql;",
            "    create table denom as",
            "    select",
            "        t.trt_order,",
            "        count(distinct d._subjid) as denom",
            f"    from {level_source} as d",
            "    inner join trt as t",
            "        on d._trt = t._trt",
            "    group by t.trt_order;",
            "quit;",
            "proc sql noprint;",
            f"    select count(distinct _subjid) into :denom_total trimmed from {level_source};",
            "quit;", "",
            "/* Any AE, SOC, and PT frequencies */",
            "proc sql;",
            "    create table any_freq as",
            "    select",
            "        t.trt_order,",
            "        count(distinct a._subjid) as freq",
            "    from ae0 as a",
            "    inner join trt as t",
            "        on a._trt = t._trt",
            "    group by t.trt_order;",
            "quit;",
            "proc sql noprint;",
            "    select count(distinct _subjid) into :any_total trimmed from ae0;",
            "quit;",
            "proc sql;",
            "    create table soc_freq as",
            "    select",
            "        t.trt_order,",
            "        a._soc as soc,",
            "        a._soc_key as soc_key,",
            "        count(distinct a._subjid) as freq",
            "    from ae0 as a",
            "    inner join trt as t",
            "        on a._trt = t._trt",
            "    where not missing(a._soc)",
            "    group by t.trt_order, a._soc, a._soc_key;",
            "quit;",
            "proc sql;",
            "    create table soc_total as",
            "    select",
            "        a._soc as soc,",
            "        a._soc_key as soc_key,",
            "        count(distinct a._subjid) as freq",
            "    from ae0 as a",
            "    where not missing(a._soc)",
            "    group by a._soc, a._soc_key;",
            "quit;",
            "proc sql;",
            "    create table pt_freq as",
            "    select",
            "        t.trt_order,",
            "        a._soc as soc,",
            "        a._soc_key as soc_key,",
            "        a._pt as pt,",
            "        a._pt_key as pt_key,",
            "        count(distinct a._subjid) as freq",
            "    from ae0 as a",
            "    inner join trt as t",
            "        on a._trt = t._trt",
            "    where not missing(a._soc)",
            "      and not missing(a._pt)",
            "    group by t.trt_order, a._soc, a._soc_key, a._pt, a._pt_key;",
            "quit;",
            "proc sql;",
            "    create table pt_total as",
            "    select",
            "        a._soc as soc,",
            "        a._soc_key as soc_key,",
            "        a._pt as pt,",
            "        a._pt_key as pt_key,",
            "        count(distinct a._subjid) as freq",
            "    from ae0 as a",
            "    where not missing(a._soc)",
            "      and not missing(a._pt)",
            "    group by a._soc, a._soc_key, a._pt, a._pt_key;",
            "quit;", "",
            "/* Runtime hierarchy order: total frequency DESC, alphabetical tie */",
            "proc sort data=soc_total out=soc_order;",
            "    by descending freq soc_key soc;",
            "run;",
            "data soc_order;",
            "    set soc_order;",
            "    soc_ord + 1;",
            "run;",
            "proc sql;",
            "    create table soc_order2 as",
            "    select",
            "        s.*,",
            "        coalesce(p.pt_count, 0) as pt_count",
            "    from soc_order as s",
            "    left join (select soc, count(*) as pt_count from pt_total group by soc) as p",
            "        on s.soc = p.soc",
            "    order by s.soc_ord;",
            "quit;",
            "proc sql;",
            "    create table pt_order as",
            "    select p.*, s.soc_ord",
            "    from pt_total as p",
            "    inner join soc_order2 as s",
            "        on p.soc = s.soc",
            "    order by s.soc_ord, descending p.freq, p.pt_key, p.pt;",
            "quit;",
            "data pt_order;",
            "    set pt_order;",
            "    by soc_ord;",
            "    if first.soc_ord then pt_ord=0;",
            "    pt_ord + 1;",
            "run;", "",
            "/* Materialize hierarchy rows from current runtime frequencies. */",
            "proc sql; create table items as",
        ]
        branches: list[str] = []
        offset = 1 if ctx["include_any"] else 0
        if ctx["include_any"]:
            branches.append(f"select 1 as row_order, 'any' as row_type length=4, '' as soc length=200, '' as pt length=200, {sas_string(ctx['any_label'])} as item length=200, 0 as indent from dictionary.tables where libname='WORK' and memname='AE0'")
        branches.append(f"select {offset} + s.soc_ord + coalesce((select sum(x.pt_count) from soc_order2 as x where x.soc_ord < s.soc_ord),0) as row_order, 'soc' as row_type length=4, s.soc, '' as pt, s.soc as item, 0 as indent from soc_order2 as s")
        branches.append(f"select {offset} + p.soc_ord + coalesce((select sum(x.pt_count) from soc_order2 as x where x.soc_ord < p.soc_ord),0) + p.pt_ord as row_order, 'pt' as row_type length=4, p.soc, p.pt, p.pt as item, 1 as indent from pt_order as p")
        lines.append("\nunion all\n".join(branches) + "; quit;")
        lines += [
            "",
            "/* Long result for audit and final transposition */",
            "proc sql;",
            "    create table long0 as",
            "    select",
            "        i.row_order,",
            "        i.row_type,",
            "        i.soc,",
            "        i.pt,",
            "        i.item,",
            "        i.indent,",
            "        t.trt_order,",
            "        t.trt_label,",
            "        coalesce(f.freq, 0) as freq,",
            "        coalesce(d.denom, 0) as denom",
            "    from items as i",
            "    cross join trt as t",
            "    left join denom as d",
            "        on d.trt_order = t.trt_order",
            "    left join (",
            "        select 'any' as row_type length=4, '' as soc length=200,",
            "               '' as pt length=200, trt_order, freq",
            "        from any_freq",
            "        union all",
            "        select 'soc', soc, '', trt_order, freq from soc_freq",
            "        union all",
            "        select 'pt', soc, pt, trt_order, freq from pt_freq",
            "    ) as f",
            "        on f.row_type = i.row_type",
            "       and f.soc = i.soc",
            "       and f.pt = i.pt",
            "       and f.trt_order = t.trt_order;",
            "quit;",
        ]
        if ctx["include_total"]:
            lines += [
                "proc sql;",
                "    create table long_total as",
                "    select",
                "        i.row_order, i.row_type, i.soc, i.pt, i.item, i.indent,",
                "        &ntrt + 1 as trt_order,",
                "        'Total' as trt_label,",
                "        coalesce(case",
                "            when i.row_type = 'any' then &any_total",
                "            when i.row_type = 'soc' then s.freq",
                "            when i.row_type = 'pt' then p.freq",
                "        end, 0) as freq,",
                "        coalesce(&denom_total, 0) as denom",
                "    from items as i",
                "    left join soc_total as s",
                "        on i.row_type = 'soc' and i.soc = s.soc",
                "    left join pt_total as p",
                "        on i.row_type = 'pt'",
                "       and i.soc = p.soc",
                "       and i.pt = p.pt;",
                "quit;",
                "data long1;",
                "    set long0 long_total;",
                "run;",
            ]
        else:
            lines += ["data long1;", "    set long0;", "run;"]
        digits = ctx["digits"]
        increment = 10 ** (-digits)
        lines += [
            "data long;",
            "    set long1;",
            "    length display $200;",
            "    if denom = 0 then display = '0 (—)';",
            "    else do;",
            "        pct = freq / denom * 100;",
            f"        pct = round(pct, {increment});",
            f"        display = cats(strip(put(freq, best.-L)), ' (', strip(put(pct, 12.{digits})), ')');",
            "    end;",
            "run;",
            "",
            "/* Final wide table: item, col1..colN (Total is last when enabled) */",
            "proc sql;",
            f"    create table {ctx['output']} as",
            "    select",
            "        case when i.indent = 0 then strip(i.item)",
            "             else cats(repeat(' ', i.indent * 4 - 1), strip(i.item))",
            "        end as item length=200",
        ]
        max_col = "&ntrt+1" if ctx["include_total"] else "&ntrt"
        lines.append(f"%macro ae_columns; %do _i=1 %to %eval({max_col}); , max(case when l.trt_order = &_i then l.display else '' end) as col&_i length=200 %end; %mend; %ae_columns;")
        lines += [
            "    from (select distinct row_order, row_type, soc, pt, item, indent from items) as i",
            "    left join long as l",
            "        on l.row_order = i.row_order",
            "    group by i.row_order, i.row_type, i.soc, i.pt, i.item, i.indent",
            "    order by i.row_order;",
            "quit;",
            "",
            "/* Add readable treatment labels to the dynamic columns */",
            "data " + ctx["output"] + ";",
            "    set " + ctx["output"] + ";",
            "%macro ae_labels;",
            "%do _i=1 %to &ntrt;",
            "    label col&_i = &&col_label&_i;",
            "%end;",
        ]
        if ctx["include_total"]:
            lines += ["    label col%eval(&ntrt + 1) = 'Total n (%)';"]
        lines += ["%mend;", "%ae_labels;", "run;"]
        return "\n".join(lines).rstrip() + "\n"


__all__ = ["SasAeTableGenerator"]

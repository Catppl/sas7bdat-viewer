from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.codegen.sas import SasCategoricalGenerator


def _filter(ast=None, text=""):
    return {"language": "sas_like", "text": text, "ast": ast}


def _comparison(variable, value):
    return {
        "type": "comparison",
        "variable": variable,
        "operator": "=",
        "operand": {"type": "literal", "value": value},
        "prefix": False,
    }


def configuration(*, denominator="nonmissing", count="distinct_subjects", digits=1):
    variables = {
        "USUBJID": {"type": "character", "label": "Subject", "length": 20, "format": ""},
        "TRTA": {"type": "character", "label": "Treatment", "length": 40, "format": ""},
        "RACE": {"type": "character", "label": "Race", "length": 40, "format": ""},
        "PARAMCD": {"type": "character", "label": "Parameter", "length": 8, "format": ""},
        "AVAL": {"type": "numeric", "label": "Value", "length": 8, "format": ""},
        "TRTEMFL": {"type": "character", "label": "Flag", "length": 1, "format": ""},
        "ABLFL": {"type": "character", "label": "Baseline", "length": 1, "format": ""},
        "AVISITN": {"type": "numeric", "label": "Visit", "length": 8, "format": ""},
    }
    value = {
        "type": "categorical_table",
        "version": 1,
        "input": {
            "kind": "sas",
            "format": "sas7bdat",
            "dataset": "ADAE",
            "source_path": r"C:\project\data\adae.sas7bdat",
            "source_directory": r"C:\project\data",
        },
        "variables": variables,
        "numerator": {
            "filter": _filter(_comparison("TRTEMFL", "Y"), 'TRTEMFL = "Y"')
        },
        "items": [
            {
                "variable": "RACE",
                "label": "Race category",
                "context_variables": ["PARAMCD"],
                "missing_level": {"include": False, "label": "(Missing)"},
                "level_order": {"method": "runtime_value_ascending"},
            }
        ],
        "count": {
            "type": count,
            "subject_variable": "USUBJID",
            "subject_missing": "exclude" if count == "distinct_subjects" else "not_applicable",
        },
        "treatment": {
            "source_variable": "TRTA",
            "missing_policy": "error",
            "level_order": "resolved",
            "resolved_levels": [
                {"value": "Placebo", "label": "Placebo"},
                {"value": "Drug A", "label": "Drug A"},
            ],
        },
        "denominator": {
            "type": "nonmissing",
            "analysis_value_variable": "AVAL",
            "base_filter": "numerator.filter",
        },
        "total": {"enabled": True, "method": "recompute_from_analysis_universe"},
        "sort": {
            "items": "configured_order",
            "contexts": {
                "method": "runtime_value_ascending",
                "character_collation": "case_insensitive",
                "numeric_order": "numeric",
                "missing": "last",
            },
            "levels": {
                "method": "runtime_value_ascending",
                "character_collation": "case_insensitive",
                "numeric_order": "numeric",
                "missing": "last",
            },
        },
        "calculation": {
            "reference_engine": "python_categorical_v1",
            "numerator": count,
            "numerator_filter_scope": "source_only",
            "denominator_filter_scope": "independent",
            "item_filter_applies_to_denominator": False,
            "subject_missing": "exclude" if count == "distinct_subjects" else "not_applicable",
            "treatment_missing": "error",
            "percent_method": "freq_divided_by_denom_times_100",
            "total_method": "recompute_from_analysis_universe",
        },
        "display": {
            "percent_digits": digits,
            "rounding": "half_up",
            "zero_denominator_display": "0 (—)",
            "level_indent_spaces": 4,
            "header_rows": True,
        },
        "output": {
            "layout": "wide_and_long",
            "wide": {
                "item_column": "item",
                "item_label": "Event",
                "treatment_column_pattern": "col{index}",
                "treatment_label_pattern": "{label} n (%)",
            },
            "long": {
                "columns": [
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
            },
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": "adae",
                "output_dataset": "work.cat_result",
                "long_output_dataset": "work.cat_long",
            }
        },
    }
    if denominator == "population":
        value["denominator"] = {
            "type": "population",
            "population": {
                "input": {
                    "kind": "sas",
                    "format": "sas7bdat",
                    "dataset": "ADSL",
                    "source_path": r"C:\project\data\adsl.sas7bdat",
                    "source_directory": r"C:\project\data",
                },
                "variables": {
                    "USUBJID": variables["USUBJID"],
                    "TRT01A": variables["TRTA"],
                    "PARAMCD": variables["PARAMCD"],
                    "SAFFL": variables["TRTEMFL"],
                },
                "treatment_variable": "TRT01A",
                "filter": _filter(_comparison("SAFFL", "Y"), 'SAFFL = "Y"'),
            },
        }
        value["targets"]["sas"].update(
            {"population_library": "pop", "population_member": "adsl"}
        )
    elif denominator == "baseline_postbaseline":
        value["count"]["type"] = "records"
        value["count"]["subject_missing"] = "exclude_for_eligibility"
        value["calculation"]["numerator"] = "records"
        value["calculation"]["subject_missing"] = "exclude_for_eligibility"
        value["denominator"] = {
            "type": "baseline_postbaseline",
            "analysis_value_variable": "AVAL",
            "baseline_filter": _filter(_comparison("ABLFL", "Y"), 'ABLFL = "Y"'),
            "postbaseline_filter": _filter(
                {
                    "type": "comparison",
                    "variable": "AVISITN",
                    "operator": ">",
                    "operand": {"type": "literal", "value": 0},
                    "prefix": False,
                },
                "AVISITN > 0",
            ),
            "eligibility": {
                "base_filter": "numerator.filter",
                "match_variables": "treatment_subject_and_item_context",
                "baseline_analysis_nonmissing": True,
                "postbaseline_analysis_nonmissing": True,
                "numerator_source": "eligible_postbaseline_records",
                "denominator_source": "eligible_postbaseline_records",
            },
        }
    return value


class CategoricalSasGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = SasCategoricalGenerator()

    def test_uses_jinja_template_and_rule_based_readability_style(self) -> None:
        self.assertIn("categorical_table.sas.j2", self.generator.environment.list_templates())
        code = self.generator.generate(configuration())
        self.assertIn("/* Prepare source data */", code)
        self.assertIn("/* Item 1: RACE */", code)
        self.assertIn("data cat_src;", code)
        self.assertIn("create table den_race as", code)
        self.assertIn("create table num_race as", code)
        self.assertIn("data work.cat_result;", code)
        self.assertIn("data work.cat_long;", code)
        self.assertIn("col{i} = cat(", code)
        self.assertIn("strip(put(cnt{i}, 3.))", code)
        self.assertIn("item = 'Event'", code)
        self.assertNotIn("cats(", code)
        self.assertNotIn("__cde", code)
        self.assertNotIn("categorical_treatment_frequency_intermediate", code)
        count_lines = [line for line in code.splitlines() if "count(distinct" in line]
        self.assertTrue(count_lines)
        self.assertTrue(all("case when" in line for line in count_lines))

    def test_custom_template_can_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "categorical_table.sas.j2"
            path.write_text("/* custom categorical */\n", encoding="utf-8")
            rendered = SasCategoricalGenerator(Path(temporary)).generate(configuration())
            self.assertEqual(rendered, "/* custom categorical */\n")

    def test_population_uses_independent_filter_and_treatment_variable(self) -> None:
        code = self.generator.generate(configuration(denominator="population"))
        self.assertIn("libname pop ", code)
        self.assertIn("set pop.adsl;", code)
        self.assertIn("if not (SAFFL = 'Y') then delete;", code)
        self.assertIn("if missing(TRT01A) then do;", code)
        self.assertIn("from pop_src", code)
        self.assertIn("when TRT01A = 'Placebo'", code)
        denominator_section = code.split("Calculate this Item denominator", 1)[1].split("quit;", 1)[0]
        self.assertNotIn("RACE", denominator_section)
        self.assertIn("PARAMCD", denominator_section)

    def test_nonmissing_and_n1_semantics_are_visible(self) -> None:
        nonmissing = self.generator.generate(configuration())
        denominator_section = nonmissing.split("Calculate this Item denominator", 1)[1].split("quit;", 1)[0]
        self.assertIn("and not missing(AVAL)", denominator_section)
        self.assertNotIn("not missing(RACE)", denominator_section)

        n1 = self.generator.generate(configuration(denominator="baseline_postbaseline"))
        self.assertIn("create table base_race as", n1)
        self.assertIn("from cat_src(where=(ABLFL = 'Y'))", n1)
        self.assertIn("set cat_src(where=(AVISITN > 0));", n1)
        self.assertIn("create table elig_race as", n1)
        self.assertIn("p.TRTA = b._trt", n1)
        self.assertIn("p.USUBJID = b._subjid", n1)
        self.assertIn("p.PARAMCD = b.ctx1", n1)
        self.assertIn("sum(case when TRTA = 'Placebo' then 1 else 0 end) as count1", n1)

    def test_context_and_level_sorting_are_runtime_and_type_aware(self) -> None:
        code = self.generator.generate(configuration())
        self.assertIn("ctx1_miss = missing(ctx1);", code)
        self.assertIn("ctx1_key = lowcase(ctx1);", code)
        self.assertIn("level_miss = missing(level);", code)
        self.assertIn("level_key = lowcase(level);", code)
        self.assertIn("ctx1_miss ctx1_key ctx1", code)
        self.assertIn("level_miss level_key level", code)

        numeric = configuration()
        numeric["variables"]["RACE"]["type"] = "numeric"
        numeric_code = self.generator.generate(numeric)
        self.assertIn("level_key = level;", numeric_code)
        self.assertNotIn("level_key = lowcase(level);", numeric_code)

    def test_missing_level_total_columns_and_percent_formats(self) -> None:
        value = configuration(digits=4)
        value["items"][0]["missing_level"]["include"] = True
        code = self.generator.generate(value)
        numerator_section = code.split("Count every runtime level", 1)[1].split("quit;", 1)[0]
        self.assertNotIn("not missing(RACE)", numerator_section)
        self.assertIn("col1 = 'Placebo n (%)'", code)
        self.assertIn("col2 = 'Drug A n (%)'", code)
        self.assertIn("col3 = 'Total n (%)'", code)
        self.assertIn("round(_pct, 0.0001)", code)
        self.assertIn("8.4", code)
        self.assertNotIn("sum(count", code.casefold())

        for digits, increment, number_format in (
            (0, "1", "4.0"),
            (1, "0.1", "5.1"),
            (2, "0.01", "6.2"),
            (3, "0.001", "7.3"),
            (4, "0.0001", "8.4"),
        ):
            with self.subTest(digits=digits):
                rendered = self.generator.generate(configuration(digits=digits))
                self.assertIn(f"round(_pct, {increment})", rendered)
                self.assertIn(number_format, rendered)

    def test_numeric_treatment_literals_and_no_total(self) -> None:
        value = configuration()
        value["variables"]["TRTA"]["type"] = "numeric"
        value["treatment"]["resolved_levels"] = [
            {"value": 2, "label": "Drug B"},
            {"value": 1, "label": "Placebo"},
        ]
        value["total"]["enabled"] = False
        code = self.generator.generate(value)
        self.assertIn("TRTA = 2", code)
        self.assertIn("TRTA = 1", code)
        self.assertIn("length col1-col2 $200;", code)
        self.assertNotIn("Total n (%)", code)

    def test_xpt_and_empty_resolved_columns_are_valid(self) -> None:
        value = configuration()
        value["input"]["format"] = "xpt"
        value["treatment"]["resolved_levels"] = []
        value["total"]["enabled"] = False
        code = self.generator.generate(value)
        self.assertIn("libname analysis xport", code)
        self.assertIn("No resolved treatment columns", code)
        self.assertIn("data work.cat_result;", code)
        self.assertIn("data work.cat_long;", code)
        self.assertNotIn("array cnt", code)

    def test_filter_is_rendered_from_ast_not_copied_from_text(self) -> None:
        value = configuration()
        value["numerator"]["filter"]["text"] = "THIS TEXT MUST NOT BE COPIED"
        code = self.generator.generate(value)
        self.assertIn("if not (TRTEMFL = 'Y') then delete;", code)
        self.assertNotIn("THIS TEXT MUST NOT BE COPIED", code)

    def test_merge_and_malformed_contracts_are_rejected(self) -> None:
        value = configuration()
        value["input"]["kind"] = "merge"
        with self.assertRaisesRegex(ValueError, "merged Categorical"):
            self.generator.generate(value)
        for key in ("items", "sort", "calculation", "output"):
            with self.subTest(key=key):
                value = configuration()
                del value[key]
                with self.assertRaises(ValueError):
                    self.generator.generate(value)


if __name__ == "__main__":
    unittest.main()

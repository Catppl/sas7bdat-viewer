from __future__ import annotations

import copy
import unittest

from clinical_data_viewer.codegen.sas import SasRuleBasedGenerator


def _filter(ast: dict[str, object] | None, text: str = "") -> dict[str, object]:
    return {"language": "sas_like", "text": text, "ast": ast}


def _comparison(
    variable: str,
    value: object,
    *,
    numeric: bool = False,
    operator: str = "=",
    prefix: bool = False,
    variable_operand: bool = False,
) -> dict[str, object]:
    return {
        "type": "comparison",
        "variable": variable,
        "operator": operator,
        "operand": {
            "type": "variable" if variable_operand else "literal",
            **(
                {}
                if variable_operand
                else {
                    "value_type": "numeric" if numeric else "character",
                    "value": value,
                }
            ),
            **({"name": value} if variable_operand else {}),
        },
        "prefix": prefix,
    }


def _base_configuration() -> dict[str, object]:
    return {
        "type": "rule_based_table",
        "version": 1,
        "input": {
            "kind": "sas",
            "format": "sas7bdat",
            "dataset": "ADAE",
            "source_path": r"C:\project\data\adae.sas7bdat",
            "source_directory": r"C:\project\data",
        },
        "variables": {
            "USUBJID": {
                "type": "character",
                "label": "Subject",
                "length": 20,
                "format": "",
            },
            "TRT01A": {
                "type": "character",
                "label": "Treatment",
                "length": 40,
                "format": "",
            },
            "TRTEMFL": {
                "type": "character",
                "label": "TEAE",
                "length": 1,
                "format": "",
            },
            "AESER": {
                "type": "character",
                "label": "Serious",
                "length": 1,
                "format": "",
            },
            "AVAL": {"type": "numeric", "label": "Value", "length": None, "format": ""},
        },
        "dataset_filter": _filter(_comparison("TRTEMFL", "Y"), 'TRTEMFL = "Y"'),
        "rows": [
            {
                "id": "row_001",
                "item": "Any TEAE",
                "indent": 0,
                "filter": _filter(None),
            },
            {
                "id": "row_002",
                "item": "Serious TEAE",
                "indent": 1,
                "filter": _filter(_comparison("AESER", "Y"), 'AESER = "Y"'),
            },
        ],
        "count": {"type": "distinct", "variable": "USUBJID"},
        "treatment": {
            "variable": "TRT01A",
            "missing_policy": "error",
            "level_order": "resolved",
            "resolved_levels": [
                {"value": "Drug B", "label": "Drug B"},
                {"value": "Placebo", "label": "Placebo"},
                {"value": "Drug A", "label": "Drug A"},
            ],
        },
        "denominator": {"type": "same_universe"},
        "total": {"enabled": True, "method": "recompute_distinct_subjects"},
        "calculation": {
            "reference_engine": "python_rule_based_v1",
            "numerator": "distinct_subjects",
            "subject_missing": "exclude",
            "treatment_missing": "error",
            "percent_method": "freq_divided_by_denom_times_100",
            "total_method": "recompute_distinct_subjects",
        },
        "display": {
            "percent_digits": 1,
            "rounding": "half_up",
            "zero_denominator_display": "0 (—)",
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": "adae",
                "output_dataset": "work.rule_based_result",
            }
        },
    }


class RuleBasedSasCodegenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = SasRuleBasedGenerator()

    def test_template_renders_row_counts_then_set_then_display(self) -> None:
        code = self.generator.generate(_base_configuration())

        self.assertIn("Reference engine: python_rule_based_v1", code)
        self.assertIn(r"libname analysis 'C:\project\data';", code)
        self.assertIn("set analysis.adae;", code)
        self.assertIn("if not (TRTEMFL = 'Y') then delete;", code)
        self.assertIn("data rb_src;", code)
        self.assertIn("create table denom as", code)
        self.assertIn("create table row1 as", code)
        self.assertIn("create table row2 as", code)
        self.assertIn("from row2_src(where=(AESER = 'Y'))", code)
        self.assertIn("data counts;", code)
        self.assertIn("set\n        row1\n        row2", code)
        self.assertIn("data work.rule_based_result;", code)
        self.assertIn("length col1-col4 $200;", code)
        self.assertIn("keep item col1-col4;", code)
        self.assertIn("col{i} = cat(", code)
        self.assertIn("strip(put(cnt{i}, 3.))", code)
        self.assertNotIn("cats(", code)
        self.assertIn("item = 'Event'", code)
        self.assertIn("label", code)
        self.assertIn("col1 = 'Drug B n (%)'", code)
        self.assertIn("col2 = 'Placebo n (%)'", code)
        self.assertIn("col3 = 'Drug A n (%)'", code)
        self.assertIn("col4 = 'Total n (%)'", code)
        self.assertNotIn("rb_long", code)
        self.assertNotIn("long_branches", code)
        self.assertNotIn("union all", code.casefold())
        self.assertNotIn("group by", code.casefold())
        self.assertNotIn("TRT_1", code)
        self.assertNotIn("as TOTAL", code)

        self.assertLess(code.index("create table row1 as"), code.index("data counts;"))
        self.assertLess(code.index("data counts;"), code.index("data work.rule_based_result;"))

        count_lines = [
            line.strip() for line in code.splitlines() if "count(distinct" in line
        ]
        self.assertTrue(count_lines)
        self.assertTrue(all("end)) as" in line for line in count_lines))

    def test_conditional_counts_preserve_resolved_treatment_order_and_total_semantics(
        self,
    ) -> None:
        code = self.generator.generate(_base_configuration())

        expected_counts = [
            "count(distinct (case when TRT01A = 'Drug B' then USUBJID end)) as count1,",
            "count(distinct (case when TRT01A = 'Placebo' then USUBJID end)) as count2,",
            "count(distinct (case when TRT01A = 'Drug A' then USUBJID end)) as count3,",
            "count(distinct (case when not missing(TRT01A) then USUBJID end)) as count4",
        ]
        for expression in expected_counts:
            self.assertGreaterEqual(code.count(expression), 1)

        expected_denoms = [
            "count(distinct (case when TRT01A = 'Drug B' then USUBJID end)) as denom1,",
            "count(distinct (case when TRT01A = 'Placebo' then USUBJID end)) as denom2,",
            "count(distinct (case when TRT01A = 'Drug A' then USUBJID end)) as denom3,",
            "count(distinct (case when not missing(TRT01A) then USUBJID end)) as denom4",
        ]
        for expression in expected_denoms:
            self.assertIn(expression, code)
        self.assertNotIn("sum(count", code.casefold())
        self.assertNotIn("sum(denom", code.casefold())

    def test_filters_are_rendered_from_ast_and_character_quotes_are_escaped(
        self,
    ) -> None:
        configuration = _base_configuration()
        configuration["rows"][0]["item"] = "Patient's TEAE"
        configuration["dataset_filter"] = _filter(
            {
                "type": "boolean",
                "operator": "and",
                "left": _comparison("TRTEMFL", "Y"),
                "right": {
                    "type": "in",
                    "variable": "TRT01A",
                    "values": [
                        {"type": "literal", "value_type": "character", "value": "A"},
                        {"type": "literal", "value_type": "character", "value": "B"},
                    ],
                    "negated": False,
                },
            },
            'TRTEMFL = "Y" and TRT01A in ("A", "B")',
        )
        code = self.generator.generate(configuration)
        self.assertIn(
            "if not ((TRTEMFL = 'Y' and TRT01A in ('A', 'B'))) then delete;",
            code,
        )
        self.assertIn("'Patient''s TEAE'", code)
        self.assertNotIn('TRTEMFL = "Y"', code)

    def test_ne_prefix_or_between_and_missing_filters_are_sas_compatible(self) -> None:
        configuration = _base_configuration()
        configuration["dataset_filter"] = _filter(
            {
                "type": "boolean",
                "operator": "or",
                "left": _comparison("TRTEMFL", "Y", operator="!=", prefix=False),
                "right": {
                    "type": "between",
                    "variable": "AVAL",
                    "lower": {"type": "literal", "value_type": "numeric", "value": 1},
                    "upper": {"type": "literal", "value_type": "numeric", "value": 3},
                    "negated": False,
                },
            }
        )
        configuration["rows"][0]["filter"] = _filter(
            {
                "type": "boolean",
                "operator": "and",
                "left": _comparison("AEDECOD", "HEAD", operator="=", prefix=True),
                "right": {"type": "missing", "variable": "AESER"},
            }
        )
        code = self.generator.generate(configuration)
        self.assertIn(
            "if not ((TRTEMFL ne 'Y' or AVAL between 1 and 3)) then delete;",
            code,
        )
        self.assertIn(
            "from row1_src(where=((AEDECOD =: 'HEAD' and missing(AESER))))",
            code,
        )
        self.assertNotIn("where (TRTEMFL ne 'Y'", code)

    def test_empty_treatment_levels_without_total_still_emit_rule_rows(self) -> None:
        configuration = _base_configuration()
        configuration["treatment"]["resolved_levels"] = []
        configuration["total"] = {
            "enabled": False,
            "method": "recompute_distinct_subjects",
        }
        code = self.generator.generate(configuration)
        self.assertIn("data denom;", code)
        self.assertIn("select distinct", code)
        self.assertIn("data counts;", code)
        self.assertIn("keep item;", code)
        self.assertNotIn("array cnt", code)
        self.assertNotIn("col1", code)
        self.assertNotIn("rb_long", code)

    def test_empty_treatment_levels_with_total_emit_a_valid_total_column(self) -> None:
        configuration = _base_configuration()
        configuration["treatment"]["resolved_levels"] = []
        code = self.generator.generate(configuration)

        total_expression = (
            "count(distinct (case when not missing(TRT01A) then USUBJID end))"
        )
        self.assertIn(f"{total_expression} as denom1", code)
        self.assertIn(f"{total_expression} as count1", code)
        self.assertIn("length col1-col1 $200;", code)
        self.assertIn("col1 = 'Total n (%)'", code)
        self.assertNotIn("col2", code)

    def test_same_universe_denominator_does_not_use_row_filter(self) -> None:
        configuration = _base_configuration()
        code = self.generator.generate(configuration)
        denominator_section = code.split("/* Calculate denominators", 1)[1].split(
            "quit;", 1
        )[0]
        self.assertIn("from rb_src", denominator_section)
        self.assertNotIn("AESER", denominator_section)

    def test_percent_digits_render_dynamic_width_and_precision(self) -> None:
        for digits, increment, number_format in (
            (0, "1", "4.0"),
            (1, "0.1", "5.1"),
            (2, "0.01", "6.2"),
            (3, "0.001", "7.3"),
            (4, "0.0001", "8.4"),
        ):
            with self.subTest(digits=digits):
                configuration = _base_configuration()
                configuration["display"]["percent_digits"] = digits
                code = self.generator.generate(configuration)
                self.assertIn(f"round(_pct, {increment})", code)
                self.assertIn(
                    f"strip(put(round(_pct, {increment}), {number_format}))",
                    code,
                )

    def test_numeric_treatment_literals_and_order_are_preserved(self) -> None:
        configuration = _base_configuration()
        configuration["variables"] = {
            "USUBJID": configuration["variables"]["USUBJID"],
            "TRT01AN": {
                "type": "numeric",
                "label": "Treatment",
                "length": None,
                "format": "",
            },
        }
        configuration["treatment"] = {
            "variable": "TRT01AN",
            "missing_policy": "error",
            "level_order": "resolved",
            "resolved_levels": [
                {"value": 2, "label": "Drug B"},
                {"value": 1, "label": "Placebo"},
            ],
        }
        configuration["dataset_filter"] = _filter(None)
        configuration["rows"] = [
            {"id": "row_001", "item": "All", "indent": 0, "filter": _filter(None)}
        ]
        configuration["total"] = {
            "enabled": False,
            "method": "recompute_distinct_subjects",
        }
        code = self.generator.generate(configuration)
        self.assertIn("TRT01AN = 2", code)
        self.assertIn("TRT01AN = 1", code)
        self.assertLess(
            code.index("col1 = 'Drug B n (%)'"),
            code.index("col2 = 'Placebo n (%)'"),
        )
        self.assertNotIn("as TOTAL", code)

    def test_population_denominator_is_independent_and_uses_pop_libref(self) -> None:
        configuration = _base_configuration()
        configuration["denominator"] = {
            "type": "population",
            "population": {
                "input": {
                    "kind": "sas",
                    "format": "xpt",
                    "dataset": "ADSL",
                    "source_path": r"C:\project\data\adsl.xpt",
                    "source_directory": r"C:\project\data",
                },
                "variables": {
                    "USUBJID": {
                        "type": "character",
                        "label": "Subject",
                        "length": 20,
                        "format": "",
                    },
                    "TRT_POP": {
                        "type": "character",
                        "label": "Treatment",
                        "length": 40,
                        "format": "",
                    },
                },
                "treatment_variable": "TRT_POP",
                "filter": _filter(_comparison("TRT_POP", "A"), 'TRT_POP = "A"'),
            },
        }
        code = self.generator.generate(configuration)
        self.assertIn(r"libname pop xport 'C:\project\data\adsl.xpt';", code)
        self.assertNotIn("libname population", code)
        self.assertIn("set pop.ADSL;", code)
        self.assertIn("if not (TRT_POP = 'A') then delete;", code)
        self.assertIn("if missing(TRT_POP)", code)
        self.assertIn("from rb_pop", code)
        denominator_section = code.split("/* Calculate denominators", 1)[1].split(
            "quit;", 1
        )[0]
        self.assertIn("TRT_POP", denominator_section)
        self.assertNotIn("TRTEMFL", denominator_section)

    def test_nonmissing_denominator_has_analysis_value_condition(self) -> None:
        configuration = _base_configuration()
        configuration["denominator"] = {
            "type": "nonmissing",
            "analysis_value_variable": "AVAL",
        }
        code = self.generator.generate(configuration)
        denominator_section = code.split("/* Calculate denominators", 1)[1].split(
            "quit;", 1
        )[0]
        self.assertIn("not missing(AVAL)", denominator_section)
        self.assertIn("not missing(USUBJID)", denominator_section)

    def test_xpt_source_uses_xport_libname(self) -> None:
        configuration = _base_configuration()
        configuration["input"] = copy.deepcopy(configuration["input"])
        configuration["input"]["format"] = "xpt"
        configuration["input"]["source_path"] = r"C:\project\data\adae.xpt"
        code = self.generator.generate(configuration)
        self.assertIn(r"libname analysis xport 'C:\project\data\adae.xpt';", code)

    def test_resolved_hierarchy_is_ignored_by_generator(self) -> None:
        configuration = _base_configuration()
        configuration["resolved_hierarchy"] = [
            {"item": "This must not become a generated row"}
        ]
        code = self.generator.generate(configuration)
        self.assertNotIn("This must not become a generated row", code)

    def test_validation_rejects_unsupported_contracts_and_merge_sources(self) -> None:
        cases = [
            ("type", "The configuration is not a Rule-based Table configuration."),
            ("version", "configuration v1"),
            ("count", "distinct USUBJID"),
            ("numerator", "numerator calculation"),
            ("display", "half_up"),
            ("ast", "dataset_filter.ast is required"),
            ("missing", "missing: rows"),
        ]
        for case, expected in cases:
            configuration = _base_configuration()
            if case == "type":
                configuration["type"] = "proc_means"
            elif case == "version":
                configuration["version"] = 2
            elif case == "count":
                configuration["count"] = {"type": "record", "variable": "USUBJID"}
            elif case == "numerator":
                configuration["calculation"]["numerator"] = "records"
            elif case == "display":
                configuration["display"]["rounding"] = "bankers"
            elif case == "ast":
                del configuration["dataset_filter"]["ast"]
            else:
                del configuration["rows"]
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, expected):
                self.generator.generate(configuration)
        merged = _base_configuration()
        merged["input"] = copy.deepcopy(merged["input"])
        merged["input"]["kind"] = "merge"
        merged["input"]["format"] = "merge"
        merged["input"]["source_path"] = None
        merged["input"]["source_directory"] = None
        with self.assertRaisesRegex(ValueError, "merged Rule-based sources"):
            self.generator.generate(merged)


if __name__ == "__main__":
    unittest.main()

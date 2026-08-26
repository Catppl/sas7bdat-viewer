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

    def test_complete_configuration_renders_readable_stable_code(self) -> None:
        code = self.generator.generate(_base_configuration())
        self.assertIn("Reference engine: python_rule_based_v1", code)
        self.assertIn(r"libname analysis 'C:\project\data';", code)
        self.assertIn("set analysis.adae;", code)
        self.assertIn("where TRTEMFL = 'Y';", code)
        self.assertIn("if not (AESER = 'Y') then delete;", code)
        self.assertIn("count(distinct USUBJID)", code)
        self.assertIn("display = '0 (—)';", code)
        self.assertIn("round(pct, 0.1)", code)
        self.assertIn("label TRT_1='Drug B n (%)';", code)
        self.assertIn("label TRT_2='Placebo n (%)';", code)
        self.assertIn("label TRT_3='Drug A n (%)';", code)
        self.assertIn(
            "case when items.indent > 0\n"
            "                 then repeat(' ', items.indent * 4 - 1)",
            code,
        )
        self.assertLess(code.index("label TRT_1"), code.index("label TRT_2"))
        self.assertLess(code.index("label TRT_2"), code.index("label TRT_3"))
        self.assertIn("as TOTAL length=200", code)
        self.assertNotIn("sum(treatment", code.casefold())

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
        self.assertIn("where (TRTEMFL = 'Y' and TRT01A in ('A', 'B'));", code)
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
        self.assertIn("where (TRTEMFL ne 'Y' or AVAL between 1 and 3);", code)
        self.assertIn(
            "if not ((AEDECOD =: 'HEAD' and missing(AESER))) then delete;", code
        )
        self.assertNotIn("and (AEDECOD =: 'HEAD'", code)

    def test_empty_treatment_levels_without_total_still_emit_rule_rows(self) -> None:
        configuration = _base_configuration()
        configuration["treatment"]["resolved_levels"] = []
        configuration["total"] = {
            "enabled": False,
            "method": "recompute_distinct_subjects",
        }
        code = self.generator.generate(configuration)
        self.assertIn("create table rb_items as", code)
        self.assertIn("data rb_long;", code)
        self.assertIn("create table work.rule_based_result as", code)
        self.assertNotIn("as TRT_1", code)
        self.assertNotIn("create table rb_long as\n    ;", code)

    def test_percent_digits_zero_and_two_are_rendered(self) -> None:
        for digits, increment in ((0, "1"), (2, "0.01")):
            with self.subTest(digits=digits):
                configuration = _base_configuration()
                configuration["display"]["percent_digits"] = digits
                code = self.generator.generate(configuration)
                self.assertIn(f"round(pct, {increment})", code)
                self.assertIn(f"32.{digits}", code)

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
        self.assertIn("if missing(TRT01AN)", code)
        self.assertIn("2 as trt_value", code)
        self.assertIn("1 as trt_value", code)
        self.assertLess(
            code.index("label TRT_1='Drug B n (%)'"),
            code.index("label TRT_2='Placebo n (%)'"),
        )
        self.assertNotIn("as TOTAL length=200", code)

    def test_population_denominator_is_independent_and_uses_population_member(
        self,
    ) -> None:
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
                    "TRT01A": {
                        "type": "character",
                        "label": "Treatment",
                        "length": 40,
                        "format": "",
                    },
                },
                "filter": _filter(_comparison("TRT01A", "A"), 'TRT01A = "A"'),
            },
        }
        code = self.generator.generate(configuration)
        self.assertIn(r"libname population xport 'C:\project\data\adsl.xpt';", code)
        self.assertIn("set population.ADSL;", code)
        self.assertIn("where TRT01A = 'A';", code)
        self.assertIn("from rb_population", code)
        denominator_section = code.split("/* Denominator counts", 1)[1].split(
            "quit;", 1
        )[0]
        self.assertNotIn("AESER = 'Y'", denominator_section)

    def test_nonmissing_denominator_has_analysis_value_condition(self) -> None:
        configuration = _base_configuration()
        configuration["denominator"] = {
            "type": "nonmissing",
            "analysis_value_variable": "AVAL",
        }
        code = self.generator.generate(configuration)
        denominator_section = code.split("/* Denominator counts", 1)[1].split(
            "quit;", 1
        )[0]
        self.assertIn("not missing(AVAL)", code)
        self.assertIn("not missing(USUBJID)", denominator_section)

    def test_validation_rejects_unsupported_contracts_and_merge_sources(self) -> None:
        cases = [
            ("type", "The configuration is not a Rule-based Table configuration."),
            ("version", "configuration v1"),
            ("count", "distinct USUBJID"),
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

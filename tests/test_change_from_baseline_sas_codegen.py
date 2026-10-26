from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.codegen.sas import SasChangeFromBaselineGenerator


def _comparison(
    variable: str, value: object, *, character: bool = True
) -> dict[str, object]:
    return {
        "type": "comparison",
        "variable": variable,
        "operator": "=",
        "operand": {
            "type": "literal",
            "value": value,
            "value_type": "character" if character else "numeric",
        },
        "prefix": False,
    }


def configuration() -> dict[str, object]:
    metadata = {
        "USUBJID": {
            "type": "character",
            "label": "Subject",
            "length": 20,
            "format": "",
        },
        "CHG": {"type": "numeric", "label": "Change", "length": 8, "format": "8.2"},
        "PARAMCD": {
            "type": "character",
            "label": "Parameter",
            "length": 8,
            "format": "",
        },
        "AVISITN": {"type": "numeric", "label": "Visit", "length": 8, "format": ""},
        "TRT01A": {
            "type": "character",
            "label": "Treatment",
            "length": 20,
            "format": "",
        },
    }
    return {
        "type": "change_from_baseline_horizontal",
        "version": 1,
        "input": {
            "kind": "sas",
            "format": "sas7bdat",
            "dataset": "ADLB",
            "source_path": r"C:\data\adlb.sas7bdat",
            "source_directory": r"C:\data",
        },
        "variables": {
            "subject": "USUBJID",
            "change": "CHG",
            "item": "PARAMCD",
            "row": ["AVISITN"],
            "treatment": "TRT01A",
        },
        "variable_metadata": metadata,
        "dataset_filter": {
            "language": "sas_like",
            "text": 'ANL01FL = "Y"',
            "ast": _comparison("ANL01FL", "Y"),
        },
        "postbaseline_filter": {
            "language": "sas_like",
            "text": "AVISITN > 0",
            "ast": {
                "type": "comparison",
                "variable": "AVISITN",
                "operator": ">",
                "operand": {"type": "literal", "value": 0, "value_type": "numeric"},
                "prefix": False,
            },
        },
        "item": {
            "labels": {"ALB": "Albumin", "ALT": "Alanine"},
            "order_source": "where_literals_or_default",
            "resolved_order": ["ALT", "ALB"],
        },
        "row_sort": [{"variable": "AVISITN", "direction": "desc"}],
        "statistics": ["N", "MEAN", "STD", "MEDIAN", "Q1", "Q3", "MIN", "MAX"],
        "treatment": {
            "level_order": "resolved",
            "resolved_levels": [
                {"value": "Drug A", "label": "Drug A"},
                {"value": "Placebo", "label": "Placebo"},
            ],
        },
        "total": {"enabled": True, "method": "recompute_distinct_subjects"},
        "calculation": {
            "reference_engine": "python_change_from_baseline_v1",
            "change_source": "existing_dataset_variable",
            "small_n": "nonmissing_change_records",
            "large_n": "distinct_subjects",
            "subject_missing": "exclude",
            "change_missing": "exclude",
            "treatment_missing": "error",
            "duplicate_change_records": "warning_continue",
        },
        "display": {
            "rounding": "half_up",
            "maximum_decimals": 4,
            "statistic_decimals": {
                "MEAN": 1,
                "STD": 2,
                "MEDIAN": 1,
                "Q1": 1,
                "Q3": 1,
                "MIN": 0,
                "MAX": 0,
            },
        },
        "targets": {"sas": {"output_dataset": "work.change_from_baseline"}},
    }


class ChangeFromBaselineSasGeneratorTests(unittest.TestCase):
    def test_uses_jinja_template_and_adam_member_without_libname(self) -> None:
        generator = SasChangeFromBaselineGenerator()
        self.assertIn(
            "change_from_baseline.sas.j2", generator.environment.list_templates()
        )
        code = generator.generate(configuration())
        self.assertIn("set adam.adlb(where=(ANL01FL = 'Y' and AVISITN > 0));", code)
        self.assertNotIn("libname", code.casefold())
        self.assertIn("proc means data=cb_source", code)

    def test_preserves_fixed_treatment_order_and_total_columns(self) -> None:
        code = SasChangeFromBaselineGenerator().generate(configuration())
        self.assertLess(code.index("_trt_order = 1"), code.index("_trt_order = 2"))
        self.assertIn("TRT_1_MEAN", code)
        self.assertIn("TRT_2_MEAN", code)
        self.assertIn("TOTAL_MEAN", code)
        self.assertIn("TRT_1_N = 'Drug A n'", code)
        self.assertIn("TRT_1_SUBJECT_N = 'Drug A N'", code)
        self.assertIn("TOTAL_N = 'Total n'", code)
        self.assertIn("TOTAL_SUBJECT_N = 'Total N'", code)
        self.assertIn("Total enabled: yes", code)
        self.assertNotIn("select distinct", code.casefold())

    def test_item_labels_are_in_one_source_block_and_decimal_formats_are_rendered(
        self,
    ) -> None:
        code = SasChangeFromBaselineGenerator().generate(configuration())
        self.assertIn("if _item_text = 'ALB' then _item_label = 'Albumin';", code)
        self.assertIn("format TRT_1_MEAN 8.1;", code)
        self.assertIn("format TRT_1_STD 8.2;", code)
        self.assertIn("format TRT_1_MIN 8.0;", code)
        self.assertIn("data work.change_from_baseline;", code)

    def test_rejects_merge_and_non_sas7bdat_inputs(self) -> None:
        merge = configuration()
        merge["input"] = {**merge["input"], "kind": "merge", "format": "merge"}
        with self.assertRaisesRegex(ValueError, "merged Change"):
            SasChangeFromBaselineGenerator().generate(merge)
        xpt = configuration()
        xpt["input"] = {**xpt["input"], "format": "xpt"}
        with self.assertRaisesRegex(ValueError, "only SAS7BDAT"):
            SasChangeFromBaselineGenerator().generate(xpt)

    def test_custom_template_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "change_from_baseline.sas.j2"
            template.write_text("/* custom change template */\n", encoding="utf-8")
            code = SasChangeFromBaselineGenerator(Path(directory)).generate(
                configuration()
            )
        self.assertEqual(code, "/* custom change template */\n")

    def test_without_treatment_uses_one_non_treatment_column_set(self) -> None:
        cfg = configuration()
        cfg["variables"] = {**cfg["variables"], "treatment": None}
        cfg["treatment"] = {"level_order": "resolved", "resolved_levels": []}
        cfg["total"] = {"enabled": True, "method": "recompute_distinct_subjects"}
        code = SasChangeFromBaselineGenerator().generate(cfg)
        self.assertIn("max(case when STATISTIC = 'MEAN' then VALUE end) as MEAN", code)
        self.assertNotIn("TRT_1_MEAN", code)
        self.assertNotIn("TOTAL_MEAN", code)
        self.assertNotIn("_cb_row", code)


if __name__ == "__main__":
    unittest.main()

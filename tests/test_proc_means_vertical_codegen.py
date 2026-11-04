from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.codegen.sas import SasProcMeansVerticalGenerator
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.proc_means_vertical import ProcMeansVerticalConfig
from clinical_data_viewer.proc_means_vertical.configuration import (
    build_proc_means_vertical_configuration,
)
from tests.test_proc_means_vertical import make_vertical_handle


class ProcMeansVerticalCodegenTests(unittest.TestCase):
    def _configuration(self, root: Path) -> dict[str, object]:
        source = make_vertical_handle(
            root,
            "ADLB",
            (("S1", "A", 1.1, 2.2), ("S2", "B", 2.2, 3.3)),
        )
        empty = FilterEngine(source.metadata.variables).compile("")
        config = ProcMeansVerticalConfig(
            ("AVAL", "CHG"),
            ("TRT01A",),
            "TRT01A",
            "TRT01A",
            ("n", "mean", "std", "q1", "q3", "min", "max"),
            empty,
            "",
            empty,
            "",
            (("AVAL", "Total Cholesterol"), ("CHG", "Change")),
            True,
            (("mean", 1),),
            0.95,
            (("AVAL", 1), ("CHG", 2)),
        )
        configuration = build_proc_means_vertical_configuration(source, config, None)
        configuration["_runtime"] = {"population_levels": ["A", "B"]}
        return configuration

    def test_generator_uses_runtime_groups_and_short_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code = SasProcMeansVerticalGenerator().generate(
                self._configuration(Path(temporary))
            )
        self.assertIn("data adlb;", code)
        self.assertIn("set analysis.adlb;", code)
        self.assertIn("proc means data=adlb", code)
        self.assertNotIn("data pm_src;", code)
        self.assertNotIn("data pop_src;", code)
        self.assertNotIn("libname analysis ", code)
        self.assertIn("stat_aval", code)
        self.assertIn("stat_chg", code)
        self.assertIn("proc transpose", code)
        self.assertNotRegex(code, r"\n{3,}")
        self.assertIn("work.proc_means_res", code)
        self.assertIn("trt_ord", code)
        self.assertEqual(code.count("%proc_means_vertical("), 2)
        self.assertIn("row_base=0", code)
        self.assertIn("row_base=5", code)
        self.assertIn("%local stat_order;", code)
        self.assertIn("%let stat_order = %eval(&stat_order. + 1);", code)
        self.assertIn("stat_order = &stat_order.;", code)
        self.assertIn("if TRT01A = 'A' then trt_ord = 1;", code)
        self.assertIn("else if TRT01A = 'B' then trt_ord = 2;", code)
        self.assertNotIn("select coalesce(max(trt_ord), 0) into :n_trt", code)
        self.assertIn("trt_ord = 3;", code)
        self.assertIn('col3 = "Total (N=&nt)"', code)
        self.assertNotIn("length trt_key $200;", code)
        self.assertNotIn("trt_value", code)
        self.assertNotIn("proc sort data=trt;", code)
        self.assertNotIn("trt1_value", code)
        self.assertNotIn("%let n1=&n1;", code)
        self.assertNotIn("resolved_hierarchy", code)

    def test_dynamic_multi_group_layout_keeps_runtime_total_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["variable_metadata"]["AVISIT"] = {
            "type": "character",
            "label": "Analysis Visit",
            "length": 40,
            "format": "",
        }
        configuration["variables"]["group"] = ["TRT01A", "AVISIT"]

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn("select coalesce(max(trt_ord), 0) into :n_trt", code)
        self.assertIn("trt_ord = &n_trt + 1;", code)
        self.assertIn('col%eval(&n_trt + 1) = "Total (N=&nt)"', code)
        self.assertNotIn("proc_means_vertical_analysis_variable", code)

    def test_explicit_same_population_source_and_filter_are_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["population"] = {
            "input": dict(configuration["input"]),
            "variables": dict(configuration["variable_metadata"]),
            "filter": dict(configuration["dataset_filter"]),
        }

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertEqual(code.count("data adlb;"), 1)
        self.assertIn("Population N uses the prepared adlb data", code)
        self.assertNotIn("data adlb_pop;", code)
        self.assertNotIn("data pop_src;", code)

    def test_different_population_source_uses_its_dataset_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
            root = Path(temporary)
        population_input = dict(configuration["input"])
        population_input.update(
            {
                "dataset": "ADSL",
                "source_path": str(root / "adsl.sas7bdat"),
            }
        )
        configuration["population"] = {
            "input": population_input,
            "variables": dict(configuration["variable_metadata"]),
            "filter": {"language": "sas_like", "text": "", "ast": {}},
        }
        configuration["targets"]["sas"]["population_member"] = "adsl"

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn("data adlb;", code)
        self.assertIn("data adsl;", code)
        self.assertIn("set analysis.adsl;", code)
        self.assertIn("into :n1 trimmed from adsl where", code)
        self.assertNotIn("libname analysis ", code)

    def test_same_population_source_with_different_filter_uses_pop_suffix(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["population"] = {
            "input": dict(configuration["input"]),
            "variables": dict(configuration["variable_metadata"]),
            "filter": {
                "language": "sas_like",
                "text": 'TRT01A = "A"',
                "ast": {
                    "type": "comparison",
                    "variable": "TRT01A",
                    "operator": "=",
                    "prefix": False,
                    "operand": {"type": "literal", "value": "A"},
                },
            },
        }

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn("data adlb;", code)
        self.assertIn("data adlb_pop;", code)
        self.assertIn("where TRT01A = 'A';", code)
        self.assertIn("into :n1 trimmed from adlb_pop where", code)

    def test_generator_rejects_xpt_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["input"]["format"] = "xpt"
        configuration["input"]["source_path"] = "/data/adlb.xpt"

        with self.assertRaisesRegex(ValueError, "requires a sas7bdat source"):
            SasProcMeansVerticalGenerator().generate(configuration)

    def test_generator_emits_population_and_decimal_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code = SasProcMeansVerticalGenerator().generate(
                self._configuration(Path(temporary))
            )
        self.assertIn(
            "select count(distinct USUBJID) into :n1 trimmed from adlb where TRT01A = 'A';",
            code,
        )
        self.assertIn(
            "select count(distinct USUBJID) into :n2 trimmed from adlb where TRT01A = 'B';",
            code,
        )
        self.assertIn(
            "select count(distinct USUBJID) into :nt trimmed from adlb where not missing(TRT01A);",
            code,
        )
        self.assertNotIn("%let n1=&n1;", code)
        self.assertNotIn("%let n2=&n2;", code)
        self.assertNotIn("%let nt=&nt;", code)
        self.assertNotIn("Population treatment contains missing values", code)
        self.assertNotIn("data _null_;", code)
        self.assertNotIn("create table pop_n", code)
        self.assertNotIn("create table pop_total", code)
        self.assertIn("if TRT01A = 'A' then trt_ord = 1;", code)
        self.assertIn("else if TRT01A = 'B' then trt_ord = 2;", code)
        self.assertNotIn("data trt_n", code)
        self.assertNotIn("into :col_labels", code)
        self.assertIn("data work.proc_means_res;", code)
        self.assertIn('col1 = "A (N=&n1)"', code)
        self.assertIn("where not missing(&var.)", code)
        self.assertIn("var=AVAL", code)
        self.assertNotIn("base_aval", code)
        self.assertNotIn("base_chg", code)
        self.assertNotIn("data pm_decimals;", code)
        self.assertNotIn("putn(", code)
        self.assertIn(
            'row_value = "   " || strip(put(value1, best.));',
            code,
        )
        self.assertIn("mean_decimals=2", code)
        self.assertNotIn('if key2 = "" then do;', code)
        self.assertNotIn("value1_text", code)
        self.assertNotIn("value2_text", code)
        self.assertNotIn('then\n        row_value', code)
        self.assertIn("data pm_labels;", code)
        self.assertIn("vardef=df qntldef=5 alpha=0.05", code)
        self.assertNotIn("data long_aval_", code)
        self.assertNotIn("data long_chg_", code)

    def test_generator_requires_current_population_levels_for_treatment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration.pop("_runtime")

        with self.assertRaisesRegex(ValueError, "No Population treatment levels"):
            SasProcMeansVerticalGenerator().generate(configuration)

    def test_generator_uses_backend_resolved_base_decimals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["display"]["base_decimals"] = {"AVAL": None, "CHG": None}
        configuration["_runtime"]["base_decimals"] = {"AVAL": 1, "CHG": 2}

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertNotIn("base_aval", code)
        self.assertNotIn("base_chg", code)
        self.assertIn("mean_decimals=2", code)
        self.assertIn("mean_decimals=3", code)

    def test_generator_requires_resolved_base_decimals_for_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["display"]["base_decimals"] = {"AVAL": None, "CHG": None}
        configuration["_runtime"].pop("base_decimals", None)

        with self.assertRaisesRegex(ValueError, "requires resolved base decimals"):
            SasProcMeansVerticalGenerator().generate(configuration)

    def test_generator_uses_numeric_population_literals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["variable_metadata"]["TRT01A"]["type"] = "numeric"
        configuration["_runtime"] = {"population_levels": [1, 2]}

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn("where TRT01A = 1;", code)
        self.assertIn("where TRT01A = 2;", code)
        self.assertIn("if TRT01A = 1 then trt_ord = 1;", code)
        self.assertIn("else if TRT01A = 2 then trt_ord = 2;", code)
        self.assertIn(
            'col1 = "1 (N=&n1)"',
            code,
        )
        self.assertIn(
            'col2 = "2 (N=&n2)"',
            code,
        )

    def test_generator_without_treatment_uses_one_overall_population_macro(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["variables"]["treatment"] = None
        configuration["variables"]["population_treatment"] = None
        configuration["_runtime"] = {"population_levels": []}

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn(
            "select count(distinct USUBJID) into :n1 trimmed from adlb;",
            code,
        )
        self.assertNotIn("%let n1=&n1;", code)
        self.assertNotIn("into :nt", code)
        self.assertIn('col1 = "All (N=&n1)"', code)

    def test_generator_rejects_merge_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["input"]["kind"] = "merge"
        with self.assertRaisesRegex(ValueError, "merged vertical"):
            SasProcMeansVerticalGenerator().generate(configuration)

    def test_generator_renders_groups_as_rows_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["display"]["layout"] = "rows"
        configuration["variable_metadata"]["AVISIT"] = {
            "type": "character",
            "label": "Analysis Visit",
            "length": 40,
            "format": "",
        }
        configuration["variables"]["row_group"] = ["AVISIT"]
        code = SasProcMeansVerticalGenerator().generate(configuration)
        self.assertIn("PROC MEANS Table Builder (Vertical - Groups as Rows)", code)
        self.assertIn("class AVISIT TRT01A;", code)
        self.assertIn("create table row0", code)
        self.assertIn("create table out_total_&token. as", code)
        self.assertIn("coalesce(s.n, 0) as n", code)
        self.assertIn("by output_row group item statistic", code)
        self.assertIn("prefix=col", code)
        self.assertNotIn("data pm_decimals;", code)
        self.assertIn("length statistic row_value $200;", code)
        self.assertIn("var row_value;", code)
        self.assertNotIn('if key2 = "" then do;', code)
        self.assertNotIn('then\n        row_value', code)
        self.assertNotIn("data long_aval_", code)
        self.assertNotIn("resolved_hierarchy", code)

    def test_generator_renders_filter_ast(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["dataset_filter"] = {
            "language": "sas_like",
            "text": 'TRT01A = "A"',
            "ast": {
                "type": "comparison",
                "variable": "TRT01A",
                "operator": "=",
                "prefix": False,
                "operand": {"type": "literal", "value": "A"},
            },
        }
        code = SasProcMeansVerticalGenerator().generate(configuration)
        self.assertIn("where TRT01A = 'A';", code)

    def test_single_analysis_uses_shared_values_labels_and_decimals_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["variables"]["analysis"] = ["AVAL"]
        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertEqual(code.count("data values_&token.;"), 2)
        self.assertNotIn("data values_chg;", code)
        self.assertEqual(code.count("data labels_&token.;"), 1)
        self.assertEqual(code.count("data pm_labels;"), 1)
        self.assertNotIn("data pm_decimals;", code)
        self.assertEqual(code.count("data pm_long;"), 1)
        self.assertNotIn("data long_aval_", code)
        self.assertEqual(code.count("%macro proc_means_vertical("), 1)
        self.assertEqual(code.count("%proc_means_vertical("), 1)

    def test_macro_label_argument_is_macro_quoted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["item"]["labels"]["AVAL"] = "Cholesterol, Total"

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn("label=%str('Cholesterol, Total')", code)

    def test_generator_combines_confidence_limits_with_fixed_display_precision(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["statistics"] = ["lclm", "uclm"]
        configuration["calculation"]["confidence"] = 0.9
        configuration["calculation"]["alpha"] = 0.1

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn("stat_label = '90% CI Mean';", code)
        self.assertIn("key1 = 'lclm';", code)
        self.assertIn("key2 = 'uclm';", code)
        self.assertIn(
            'row_value = catx(", ", ifc(missing(value1), "—", strip(put(value1, 8.&lclm_decimals.))), ifc(missing(value2), "—", strip(put(value2, 8.&uclm_decimals.))));',
            code,
        )
        self.assertNotIn('if key2 = "" then do;', code)
        self.assertNotIn('then\n        row_value', code)
        self.assertNotIn("display_dec =", code)
        self.assertNotIn("stat_label = 'Lclm';", code)
        self.assertNotIn("stat_label = 'Uclm';", code)

    def test_rows_generator_combines_confidence_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            configuration = self._configuration(Path(temporary))
        configuration["statistics"] = ["lclm", "uclm"]
        configuration["display"]["layout"] = "rows"
        configuration["variable_metadata"]["AVISIT"] = {
            "type": "character",
            "label": "Analysis Visit",
            "length": 40,
            "format": "",
        }
        configuration["variables"]["row_group"] = ["AVISIT"]

        code = SasProcMeansVerticalGenerator().generate(configuration)

        self.assertIn("stat_label = '95% CI Mean';", code)
        self.assertIn("key1 = 'lclm';", code)
        self.assertIn("key2 = 'uclm';", code)
        self.assertIn(
            'row_value = catx(", ", ifc(missing(value1), "—", strip(put(value1, 8.&lclm_decimals.))), ifc(missing(value2), "—", strip(put(value2, 8.&uclm_decimals.))));',
            code,
        )
        self.assertNotIn('if key2 = "" then do;', code)
        self.assertNotIn('then\n        row_value', code)
        self.assertNotIn("display_dec =", code)


if __name__ == "__main__":
    unittest.main()

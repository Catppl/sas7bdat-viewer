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
        return build_proc_means_vertical_configuration(source, config, None)

    def test_generator_uses_runtime_groups_and_short_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code = SasProcMeansVerticalGenerator().generate(
                self._configuration(Path(temporary))
            )
        self.assertIn("data pm_src;", code)
        self.assertIn("proc means data=pm_src", code)
        self.assertIn("stat_aval", code)
        self.assertIn("stat_chg", code)
        self.assertIn("proc transpose", code)
        self.assertIn("work.pm_vertical", code)
        self.assertIn("grp_ord", code)
        self.assertNotIn("resolved_hierarchy", code)
        self.assertNotIn("proc_means_vertical_analysis_variable", code)

    def test_generator_emits_population_and_decimal_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            code = SasProcMeansVerticalGenerator().generate(
                self._configuration(Path(temporary))
            )
        self.assertIn("count(distinct USUBJID) as big_n", code)
        self.assertIn("where not missing(AVAL)", code)
        self.assertIn("base_aval", code)
        self.assertIn("base_chg", code)
        self.assertIn("data pm_decimals;", code)
        self.assertIn("display_dec = min(4, base_dec + add_dec);", code)
        self.assertIn("data pm_labels;", code)
        self.assertIn("vardef=df qntldef=5 alpha=0.05", code)
        self.assertNotIn("data long_aval_", code)
        self.assertNotIn("data long_chg_", code)

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
        self.assertIn("create table out_total_aval", code)
        self.assertIn("coalesce(s.n, 0) as n", code)
        self.assertIn("by output_row group item statistic", code)
        self.assertIn("prefix=col", code)
        self.assertIn("data pm_decimals;", code)
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

        self.assertEqual(code.count("data values_aval;"), 1)
        self.assertNotIn("data values_chg;", code)
        self.assertEqual(code.count("data pm_labels;"), 1)
        self.assertEqual(code.count("data pm_decimals;"), 1)
        self.assertEqual(code.count("data pm_long;"), 1)
        self.assertNotIn("data long_aval_", code)


if __name__ == "__main__":
    unittest.main()

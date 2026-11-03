from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.codegen.sas import SasShiftTableGenerator
from clinical_data_viewer.shift_table import build_shift_table_configuration
from tests.test_shift_table import make_analysis, make_config, make_population


class ShiftTableSasCodegenTests(unittest.TestCase):
    def test_generator_uses_jinja_and_emits_readable_distinct_subject_program(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),),
            )
            population = make_population(root, (("S1", "Y", "A"),))
            configuration = build_shift_table_configuration(
                source, make_config(source, population), population=population
            )
            code = SasShiftTableGenerator().generate(configuration)
            folded = code.casefold()
            self.assertIn("data parm_ord;", folded)
            self.assertIn("data visit_ord;", folded)
            self.assertIn("data trt_ord;", folded)
            self.assertIn("data grade_ord;", folded)
            self.assertIn("count(distinct usubjid)", folded)
            self.assertIn("work.shift_table_long", folded)
            self.assertIn("work.shift_table", folded)
            self.assertIn("proc transpose", folded)
            self.assertIn(
                "select count(distinct usubjid) into :n1 trimmed from adam.adsl where",
                folded,
            )
            self.assertIn("%let n1=&n1;", folded)
            self.assertIn(
                "cat(strip(put(subject_n, 8.)), ' (', strip(put(percent_value, 5.1)), ')')",
                folded,
            )
            self.assertNotIn("libname ", folded)
            self.assertNotIn("delete shift_table", folded)

    def test_generator_preserves_configured_order_labels_and_optional_total(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),),
            )
            population = make_population(root, (("S1", "Y", "A"),))
            configuration = build_shift_table_configuration(
                source, make_config(source, population), population=population
            )
            code = SasShiftTableGenerator().generate(configuration)
            self.assertLess(
                code.index("parm_raw = 'AST'"), code.index("parm_raw = 'ALT'")
            )
            self.assertLess(
                code.index("visit_raw = 'Week 4'"), code.index("visit_raw = 'Week 8'")
            )
            self.assertIn("Total (N=&nt)", code)
            self.assertIn("total_detail", code)

    def test_generator_rejects_wrong_contract_and_merge_source(self) -> None:
        generator = SasShiftTableGenerator()
        with self.assertRaisesRegex(ValueError, "not a Shift Table"):
            generator.generate({"type": "other", "version": 1})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),),
            )
            population = make_population(root, (("S1", "Y", "A"),))
            configuration = build_shift_table_configuration(
                source, make_config(source, population), population=population
            )
            configuration["input"]["kind"] = "merge"
            with self.assertRaisesRegex(ValueError, "merged"):
                generator.generate(configuration)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.codegen import build_proc_means_configuration
from clinical_data_viewer.codegen.sas import SasProcMeansGenerator
from clinical_data_viewer.domain import (
    DatasetHandle,
    DatasetMetadata,
    VariableMetadata,
)
from clinical_data_viewer.proc_means import ProcMeansConfig


class SasProcMeansGeneratorTests(unittest.TestCase):
    def make_source(
        self,
        root: Path,
        *,
        include_subject: bool = True,
        dataset_name: str = "ADLB",
        extension: str = ".sas7bdat",
    ) -> DatasetHandle:
        variables = [
            VariableMetadata("PARAMCD", "Parameter"),
            VariableMetadata("AVISITN", kind="numeric"),
            VariableMetadata("TRT01AN", kind="numeric"),
            VariableMetadata("AVAL", "Analysis Value", "numeric"),
            VariableMetadata("ANL01FL"),
        ]
        if include_subject:
            variables.insert(0, VariableMetadata("USUBJID"))
        source = root / "original data" / f"{dataset_name}{extension}"
        source.parent.mkdir()
        source.touch()
        temporary = root / "viewer-temp" / f"{dataset_name}{extension}"
        temporary.parent.mkdir()
        temporary.touch()
        return DatasetHandle(
            source,
            temporary,
            root / "viewer-temp" / "dataset.sqlite",
            DatasetMetadata(dataset_name, 10, tuple(variables)),
            10,
            True,
        )

    def test_v3_configuration_and_sas_use_original_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory))
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD", "AVISITN"),
                ("TRT01AN",),
                ("subjects", "n", "mean", "std", "stderr", "lclm", "uclm"),
                filter_text='ANL01FL = "Y"',
                decimal_group_variables=("PARAMCD", "AVISITN"),
                decimal_offsets=(("mean", 1), ("std", 2)),
                confidence=0.95,
            )
            configuration = build_proc_means_configuration(source, config)
            self.assertEqual(configuration["version"], 3)
            self.assertEqual(
                configuration["input"]["source_path"], str(source.source_path)
            )
            self.assertEqual(
                configuration["display"]["decimal_group_variables"],
                ["PARAMCD", "AVISITN"],
            )
            self.assertEqual(
                configuration["calculation"]["subject_count"]["variable"],
                "USUBJID",
            )
            configuration["filter"]["text"] = "THIS TEXT IS NOT RENDERED"

            code = SasProcMeansGenerator().generate(configuration)
            self.assertIn(
                "libname analysis '" + str(source.source_path.parent) + "';", code
            )
            self.assertNotIn(str(source.temporary_path.parent), code)
            self.assertIn("where ANL01FL = 'Y';", code)
            self.assertIn("by PARAMCD AVISITN;", code)
            self.assertIn("class TRT01AN;", code)
            self.assertIn("var AVAL;", code)
            self.assertIn("vardef=df qntldef=5 alpha=0.05", code)
            self.assertIn("std=SD", code)
            self.assertIn("stderr=SE", code)
            self.assertIn("lclm=LCLM", code)
            self.assertIn("count(distinct USUBJID) as SUBJECT_N", code)
            self.assertIn("__CDE_CANDIDATE=0 to 4", code)
            self.assertIn("__CDE_BASE_DECIMALS+1", code)
            self.assertIn("__CDE_BASE_DECIMALS+2", code)
            self.assertIn("data work.proc_means_result;", code)
            self.assertIn("data work.adlb_source;", code)
            self.assertIn("work.adlb_aval_stats", code)
            self.assertIn("work.adlb_aval_subjects", code)
            self.assertIn("work.adlb_aval_long", code)
            self.assertIn("work.adlb_aval_decimals", code)
            self.assertIn("work.adlb_decimal_rules", code)
            self.assertNotIn("work.pm_", code)
            self.assertNotIn("__CDE_PM_", code)

    def test_subject_only_and_no_grouping_or_filter_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory), include_subject=False)
            config = ProcMeansConfig(("AVAL",), statistics=("subjects",))
            configuration = build_proc_means_configuration(source, config)
            code = SasProcMeansGenerator().generate(configuration)
            self.assertIsNone(configuration["calculation"]["subject_count"]["variable"])
            self.assertIn("SUBJECT_N=.;", code)
            self.assertIn("n=__CDE_INTERNAL_N", code)
            self.assertNotIn("    where ;", code)
            self.assertNotIn("    class ;", code)
            self.assertNotIn("    by ;", code)

    def test_sas_literals_escape_apostrophes_and_name_literals(self) -> None:
        with tempfile.TemporaryDirectory(prefix="patient's-") as directory:
            source = self.make_source(Path(directory), dataset_name="AD LB")
            config = ProcMeansConfig(("AVAL",), statistics=("mean",))
            code = SasProcMeansGenerator().generate(
                build_proc_means_configuration(source, config)
            )
            self.assertIn("patient''s-", code)
            self.assertIn("set analysis.'ad lb'n;", code)

    def test_source_member_is_dynamic_and_lowercase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory), dataset_name="ADAE")
            config = ProcMeansConfig(("AVAL",), statistics=("mean",))
            configuration = build_proc_means_configuration(source, config)
            self.assertEqual(configuration["targets"]["sas"]["source_member"], "adae")
            code = SasProcMeansGenerator().generate(configuration)
            self.assertIn("set analysis.adae;", code)
            self.assertIn("data work.adae_source;", code)
            self.assertNotIn("analysis.adlb", code.casefold())

    def test_xpt_configuration_and_sas_use_xport_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory), extension=".xpt")
            configuration = build_proc_means_configuration(
                source, ProcMeansConfig(("AVAL",), statistics=("mean",))
            )
            code = SasProcMeansGenerator().generate(configuration)
            self.assertEqual(configuration["input"]["format"], "xpt")
            self.assertIn(
                "libname analysis xport '" + str(source.source_path) + "';", code
            )
            self.assertNotIn("libname analysis '" + str(source.source_path.parent), code)

    def test_sas_where_is_rendered_from_python_filter_ast(self) -> None:
        cases = {
            'PARAMCD in ("ALB", "ALT")': "where PARAMCD in ('ALB', 'ALT');",
            "not missing(AVAL)": "where not (missing(AVAL));",
            'PARAMCD like "A%"': "where PARAMCD like 'A%';",
            'PARAMCD =: "AL"': "where PARAMCD =: 'AL';",
            'PARAMCD contains "AL"': "where PARAMCD contains 'AL';",
            "AVISITN between 1 and TRT01AN": ("where AVISITN between 1 and TRT01AN;"),
        }
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory))
            for filter_text, expected in cases.items():
                with self.subTest(filter_text=filter_text):
                    config = ProcMeansConfig(
                        ("AVAL",), statistics=("mean",), filter_text=filter_text
                    )
                    configuration = build_proc_means_configuration(source, config)
                    configuration["filter"]["text"] = "ignored by SAS renderer"
                    code = SasProcMeansGenerator().generate(configuration)
                    self.assertIn(expected, code)


if __name__ == "__main__":
    unittest.main()

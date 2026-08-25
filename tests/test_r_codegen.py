from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.codegen import build_proc_means_configuration
from clinical_data_viewer.codegen.r import RProcMeansGenerator
from clinical_data_viewer.domain import (
    DatasetHandle,
    DatasetMetadata,
    VariableMetadata,
)
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.proc_means import ProcMeansConfig, ProcMeansEngine
from clinical_data_viewer.temp_manager import TempManager


class RProcMeansGeneratorTests(unittest.TestCase):
    def make_source(self, root: Path, *, dataset_name: str = "ADLB") -> DatasetHandle:
        variables = (
            VariableMetadata("USUBJID"),
            VariableMetadata("PARAMCD", "Parameter"),
            VariableMetadata("AVISITN", kind="numeric"),
            VariableMetadata("TRT01AN", kind="numeric"),
            VariableMetadata("AVAL", "Analysis Value", "numeric"),
            VariableMetadata("ANL01FL"),
        )
        source = root / "original data" / f"{dataset_name}.sas7bdat"
        source.parent.mkdir()
        source.touch()
        temporary = root / "viewer-temp" / f"{dataset_name}.sas7bdat"
        temporary.parent.mkdir()
        temporary.touch()
        return DatasetHandle(
            source,
            temporary,
            root / "viewer-temp" / "dataset.sqlite",
            DatasetMetadata(dataset_name, 10, variables),
            10,
            True,
        )

    def test_v3_configuration_renders_python_equivalent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory))
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD", "AVISITN"),
                ("TRT01AN",),
                ("subjects", "n", "nmiss", "mean", "std", "median", "lclm"),
                filter_text='ANL01FL = "Y"',
                decimal_group_variables=("PARAMCD", "AVISITN"),
                decimal_offsets=(("mean", 1), ("std", 2)),
                confidence=0.95,
            )
            configuration = build_proc_means_configuration(source, config)
            configuration["filter"]["text"] = "This display text is not reparsed"

            code = RProcMeansGenerator().generate(configuration)

            self.assertIn('haven::read_sas("' + str(source.source_path) + '")', code)
            self.assertNotIn(str(source.temporary_path.parent), code)
            self.assertIn('cde_compare(data[["ANL01FL"]], "Y", "=")', code)
            self.assertIn('cde_by_variables <- c("PARAMCD", "AVISITN")', code)
            self.assertIn('cde_class_variables <- c("TRT01AN")', code)
            self.assertIn(
                'cde_group_variables <- c("PARAMCD", "AVISITN", "TRT01AN")', code
            )
            self.assertIn("cde_qntldef5 <- function", code)
            self.assertIn("qt((1 + confidence) / 2, df = count - 1L)", code)
            self.assertIn("SUBJECT_N = if (is.null(subject_values))", code)
            self.assertIn(
                'cde_decimal_group_variables <- c("PARAMCD", "AVISITN")', code
            )
            self.assertIn(
                'min(4, proc_means_result[["__CDE_BASE_DECIMALS"]][[index]] + 1)', code
            )
            self.assertIn(
                'min(4, proc_means_result[["__CDE_BASE_DECIMALS"]][[index]] + 2)', code
            )
            self.assertIn(
                "proc_means_result is the final long-format result object", code
            )

    def test_filter_ast_operators_render_without_reparsing_where_text(self) -> None:
        cases = {
            'PARAMCD in ("ALB", "ALT")': 'cde_in(data[["PARAMCD"]], c("ALB", "ALT"))',
            "not missing(AVAL)": '(!(cde_missing(data[["AVAL"]], "numeric")))',
            'PARAMCD like "A%"': 'cde_like(data[["PARAMCD"]], "A%", NA)',
            'PARAMCD =: "AL"': 'cde_prefix_compare(data[["PARAMCD"]], "AL", "=")',
            'PARAMCD contains "AL"': 'cde_contains(data[["PARAMCD"]], "AL")',
            "AVISITN between 1 and TRT01AN": (
                'cde_between(data[["AVISITN"]], 1, data[["TRT01AN"]])'
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory))
            for filter_text, expected in cases.items():
                with self.subTest(filter_text=filter_text):
                    config = ProcMeansConfig(
                        ("AVAL",), statistics=("mean",), filter_text=filter_text
                    )
                    configuration = build_proc_means_configuration(source, config)
                    configuration["filter"]["text"] = "ignored by the R renderer"
                    code = RProcMeansGenerator().generate(configuration)
                    self.assertIn(expected, code)

    def test_dynamic_source_path_and_output_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(Path(directory), dataset_name="ADAE")
            configuration = build_proc_means_configuration(
                source, ProcMeansConfig(("AVAL",), statistics=("mean",))
            )
            code = RProcMeansGenerator().generate(configuration)
            self.assertIn('haven::read_sas("' + str(source.source_path) + '")', code)
            self.assertIn("proc_means_result <- data.frame", code)
            self.assertNotIn("analysis.adlb", code.casefold())

    @unittest.skipUnless(shutil.which("Rscript"), "Rscript is not installed")
    def test_generated_r_matches_python_engine_for_fixture(self) -> None:
        """Optional integration test; skipped on machines without R + haven."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "ADLB.sas7bdat"
            r_fixture = f"""
if (!requireNamespace("haven", quietly = TRUE)) quit(status = 42)
haven::write_sas(data.frame(
  USUBJID = c("01", "01", "02", "03", "04", "05", "06", "07", "08"),
  PARAMCD = c("ALB", "ALB", "ALB", "ALB", "ALT", "ALT", NA, NA, "ALB"),
  TRT01AN = c(1, 1, 1, 1, 1, 1, 2, 2, 1),
  AVAL = c(1.2, 2.3, 3.45, 4.55, NA, NA, 2.01, 2.99, 100),
  ANL01FL = c("Y", "Y", "Y", "Y", "Y", "Y", "Y", "Y", "N")
), {json.dumps(str(source_path))})
"""
            fixture = subprocess.run(
                ["Rscript", "-e", r_fixture],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if fixture.returncode == 42:
                self.skipTest("R package haven is not installed")
            self.assertEqual(fixture.returncode, 0, fixture.stderr)

            rows = [
                (1, "01", "ALB", 1, 1.2, "Y"),
                (2, "01", "ALB", 1, 2.3, "Y"),
                (3, "02", "ALB", 1, 3.45, "Y"),
                (4, "03", "ALB", 1, 4.55, "Y"),
                (5, "04", "ALT", 1, None, "Y"),
                (6, "05", "ALT", 1, None, "Y"),
                (7, "06", None, 2, 2.01, "Y"),
                (8, "07", None, 2, 2.99, "Y"),
                (9, "08", "ALB", 1, 100, "N"),
            ]
            variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("PARAMCD"),
                VariableMetadata("TRT01AN", kind="numeric"),
                VariableMetadata("AVAL", "Analysis Value", "numeric"),
                VariableMetadata("ANL01FL"),
            )
            database_path = root / "source.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                    '"USUBJID" TEXT, "PARAMCD" TEXT, "TRT01AN" REAL, '
                    '"AVAL" REAL, "ANL01FL" TEXT)'
                )
                connection.executemany(
                    "INSERT INTO dataset VALUES (?, ?, ?, ?, ?, ?)", rows
                )
                connection.execute(
                    "CREATE TABLE cache_info (cached_rows INTEGER, total_rows INTEGER, complete INTEGER)"
                )
                connection.execute("INSERT INTO cache_info VALUES (9, 9, 1)")
                connection.commit()
            temporary = root / "temporary.sas7bdat"
            temporary.touch()
            source = DatasetHandle(
                source_path,
                temporary,
                database_path,
                DatasetMetadata("ADLB", len(rows), variables),
                len(rows),
                True,
            )
            where_text = 'ANL01FL = "Y"'
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD",),
                ("TRT01AN",),
                (
                    "subjects",
                    "n",
                    "nmiss",
                    "mean",
                    "std",
                    "stderr",
                    "median",
                    "q1",
                    "q3",
                    "min",
                    "max",
                    "lclm",
                    "uclm",
                ),
                FilterEngine(variables).compile(where_text),
                where_text,
                ("PARAMCD",),
                (("mean", 1), ("std", 2)),
            )
            python_result = ProcMeansEngine(TempManager(root / "temp")).run(
                source, config
            )
            r_output = root / "r-result.csv"
            r_program = RProcMeansGenerator().generate(
                build_proc_means_configuration(source, config)
            )
            program_path = root / "generated.R"
            program_path.write_text(
                r_program
                + "\nwrite.csv(proc_means_result, "
                + json.dumps(str(r_output))
                + ', row.names = FALSE, na = "")\n',
                encoding="utf-8",
            )
            completed = subprocess.run(
                ["Rscript", str(program_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            with closing(sqlite3.connect(python_result.database_path)) as connection:
                python_rows = connection.execute(
                    'SELECT "PARAMCD", "TRT01AN", "SUBJECT_N", "N", "NMISS", '
                    '"MEAN", "SD", "SE", "MEDIAN", "Q1", "Q3", "MIN", "MAX", '
                    '"LCLM", "UCLM", "__CDE_BASE_DECIMALS" FROM dataset '
                    'ORDER BY "PARAMCD" IS NOT NULL, "PARAMCD", "TRT01AN"'
                ).fetchall()
            with r_output.open(newline="", encoding="utf-8") as stream:
                r_rows = list(csv.DictReader(stream))

            self.assertEqual(len(r_rows), len(python_rows))
            statistic_names = (
                "SUBJECT_N",
                "N",
                "NMISS",
                "MEAN",
                "SD",
                "SE",
                "MEDIAN",
                "Q1",
                "Q3",
                "MIN",
                "MAX",
                "LCLM",
                "UCLM",
                "__CDE_BASE_DECIMALS",
            )
            for python_row, r_row in zip(python_rows, r_rows, strict=True):
                self.assertEqual(python_row[0] or "", r_row["PARAMCD"])
                self.assertEqual(float(python_row[1]), float(r_row["TRT01AN"]))
                for index, name in enumerate(statistic_names, start=2):
                    expected = python_row[index]
                    actual = r_row[name]
                    if expected is None:
                        self.assertEqual(actual, "")
                    elif name in {"SUBJECT_N", "N", "NMISS", "__CDE_BASE_DECIMALS"}:
                        self.assertEqual(int(expected), int(float(actual)))
                    else:
                        self.assertAlmostEqual(
                            float(expected), float(actual), places=11
                        )


if __name__ == "__main__":
    unittest.main()

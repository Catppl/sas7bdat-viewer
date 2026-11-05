from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p21_issue_explorer.integration import SnapshotReader
from p21_issue_explorer.matching import IssueMatcher
from p21_issue_explorer.matching.where_builder import where_condition
from p21_issue_explorer.report import P21ReportReader
from snapshot_factory import create_fixture_snapshot

from clinical_data_viewer.domain import VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.where_parser import parse_where

FIXTURES = Path(__file__).parents[1] / "fixtures"


class WhereBuilderTests(unittest.TestCase):
    def test_quotes_character_numeric_and_missing_values(self) -> None:
        where = " AND ".join(
            (
                where_condition("USUBJID", "O'Brien", "character"),
                where_condition("AVAL", "1.00", "numeric"),
                where_condition("AEENDY", "null", "numeric"),
            )
        )
        self.assertEqual(
            where,
            "USUBJID = 'O''Brien' AND AVAL = 1 AND MISSING(AEENDY)",
        )
        parse_where(where)

    def test_generated_where_compiles_with_viewer_filter_engine(self) -> None:
        where = "USUBJID = 'O''Brien' AND AVAL = 1 AND MISSING(AEENDY)"
        engine = FilterEngine(
            [
                VariableMetadata("USUBJID", kind="character", length=20),
                VariableMetadata("AVAL", kind="numeric", length=8),
                VariableMetadata("AEENDY", kind="numeric", length=8),
            ]
        )
        compiled = engine.compile(where)
        self.assertIn('"USUBJID" = ?', compiled.sql)
        self.assertEqual(compiled.parameters, ("O'Brien", 1))

    def test_all_frozen_fixture_where_expressions_compile_in_viewer(self) -> None:
        report = P21ReportReader().read(FIXTURES / "p21_synthetic_report.xlsx")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for domain in ("AE", "LB"):
                with SnapshotReader().open(
                    create_fixture_snapshot(root / domain, domain, FIXTURES)
                ) as snapshot:
                    engine = FilterEngine(
                        [
                            VariableMetadata(variable.name, kind=variable.sas_type)
                            for variable in snapshot.manifest.variables
                        ]
                    )
                    for issue in report.issues:
                        if issue.dataset != domain:
                            continue
                        result = IssueMatcher().match(issue, snapshot)
                        expressions = [
                            result.expected_where,
                            result.applied_where,
                            *(item.applied_where for item in result.candidates),
                        ]
                        for expression in expressions:
                            if expression:
                                with self.subTest(
                                    domain=domain,
                                    sheet_row=issue.sheet_row,
                                    expression=expression,
                                ):
                                    engine.compile(expression)


if __name__ == "__main__":
    unittest.main()

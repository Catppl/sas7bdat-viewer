from __future__ import annotations

import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.integrations.p21.excel_export import (
    P21DomainExcelExporter,
    P21DomainExportSpec,
)

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _sheet(book: ZipFile, index: int) -> ET.Element:
    return ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))


def _value(xml: ET.Element, cell_ref: str) -> str | None:
    cell = xml.find(f".//x:c[@r='{cell_ref}']", NS)
    if cell is None:
        return None
    inline = cell.find("x:is/x:t", NS)
    number = cell.find("x:v", NS)
    return (
        inline.text
        if inline is not None
        else number.text
        if number is not None
        else None
    )


class P21ExcelExportTests(unittest.TestCase):
    def test_rule_sheets_preserve_duplicate_rows_and_include_all_variables(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            spec = P21DomainExportSpec.from_payload(
                {
                    "domain": "AE",
                    "report_name": "p21-report.xlsx",
                    "source_fingerprint": "source-fingerprint",
                    "rules": [
                        {
                            "rule_id": "CT2002",
                            "p21_issue_count": 2,
                            "rows": [
                                self._row(1, "Matched – Exact", "issue-1", 8),
                                self._row(
                                    1, "Review – Partial/Lenient Match", "issue-2", 9
                                ),
                                self._row(2, "Matched – Exact", "issue-2", 9),
                            ],
                        },
                        {
                            "rule_id": "NO-MATCH",
                            "p21_issue_count": 1,
                            "rows": [],
                        },
                    ],
                }
            )
            destination = root / "p21.xlsx"

            exported = P21DomainExcelExporter().export(source, spec, destination)

            self.assertEqual(exported, 3)
            with ZipFile(destination) as book:
                workbook = ET.fromstring(book.read("xl/workbook.xml"))
                names = [
                    item.attrib["name"] for item in workbook.findall(".//x:sheet", NS)
                ]
                self.assertEqual(names, ["Export Summary", "Export Info", "CT2002 (3)"])
                summary = _sheet(book, 1)
                data = _sheet(book, 3)
                self.assertEqual(_value(summary, "E2"), "3")
                self.assertEqual(_value(summary, "E3"), "0")
                self.assertEqual(
                    [_value(data, reference) for reference in ("A2", "A3", "A4")],
                    ["STUDY", "STUDY", "STUDY"],
                )
                self.assertEqual(
                    [_value(data, reference) for reference in ("B2", "B3", "B4")],
                    ["01", "01", "02"],
                )
                self.assertEqual(_value(data, "E1"), "_P21_RULE_ID")
                self.assertEqual(_value(data, "F1"), "_P21_MATCH_STATUS")
                self.assertEqual(_value(data, "G3"), "issue-2")
                self.assertEqual(_value(data, "H3"), "9")
                exact_style = data.find(".//x:c[@r='A2']", NS).attrib["s"]
                review_style = data.find(".//x:c[@r='A3']", NS).attrib["s"]
                self.assertNotEqual(exact_style, review_style)
                styles = book.read("xl/styles.xml").decode("utf-8").upper()
                self.assertIn("FFDFF3E5", styles)
                self.assertIn("FFFFF2B2", styles)
                export_info = _sheet(book, 2)
                self.assertEqual(_value(export_info, "A7"), "Duplicate Policy")
                self.assertIn("Preserved", _value(export_info, "B7"))

    def test_sheet_names_are_sanitized_and_collision_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._source(root)
            spec = P21DomainExportSpec.from_payload(
                {
                    "domain": "AE",
                    "report_name": "report.xlsx",
                    "source_fingerprint": "source-fingerprint",
                    "rules": [
                        {
                            "rule_id": "ABC/DEF",
                            "p21_issue_count": 1,
                            "rows": [self._row(1, "Matched – Exact", "one", 2)],
                        },
                        {
                            "rule_id": "ABC:DEF",
                            "p21_issue_count": 1,
                            "rows": [self._row(2, "Matched – Exact", "two", 3)],
                        },
                    ],
                }
            )
            destination = root / "names.xlsx"
            P21DomainExcelExporter().export(source, spec, destination)
            with ZipFile(destination) as book:
                workbook = ET.fromstring(book.read("xl/workbook.xml"))
                names = [
                    item.attrib["name"] for item in workbook.findall(".//x:sheet", NS)
                ]
            self.assertEqual(names[2:], ["ABC_DEF (1)", "ABC_DEF~2 (1)"])
            self.assertTrue(all(len(name) <= 31 for name in names))

    @staticmethod
    def _row(source_row: int, status: str, issue_id: str, sheet_row: int):
        return {
            "source_row": source_row,
            "status": status,
            "issue_id": issue_id,
            "issue_sheet_row": sheet_row,
        }

    @staticmethod
    def _source(root: Path) -> DatasetHandle:
        database = root / "dataset.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                "STUDYID TEXT, USUBJID TEXT, AETERM TEXT, AVAL REAL)"
            )
            connection.executemany(
                "INSERT INTO dataset VALUES (?, ?, ?, ?, ?)",
                (
                    (1, "STUDY", "01", "Headache", 1.0),
                    (2, "STUDY", "02", "Nausea", 2.0),
                ),
            )
            connection.commit()
        source = root / "AE.sas7bdat"
        source.touch()
        return DatasetHandle(
            source,
            source,
            database,
            DatasetMetadata(
                "AE",
                2,
                (
                    VariableMetadata("STUDYID", kind="character"),
                    VariableMetadata("USUBJID", kind="character"),
                    VariableMetadata("AETERM", kind="character"),
                    VariableMetadata("AVAL", kind="numeric"),
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()

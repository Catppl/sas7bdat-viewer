from __future__ import annotations

import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

from clinical_data_viewer.domain import (
    DatasetHandle,
    DatasetMetadata,
    SortSpec,
    VariableMetadata,
)
from clinical_data_viewer.excel_exporter import ExcelExporter, ExcelExportView
from clinical_data_viewer.filter_engine import FilterEngine

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sheet(book: ZipFile, index: int) -> ET.Element:
    return ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))


def value(xml: ET.Element, cell_ref: str) -> str | None:
    cell = xml.find(f".//x:c[@r='{cell_ref}']", NS)
    if cell is None:
        return None
    inline = cell.find("x:is/x:t", NS)
    number = cell.find("x:v", NS)
    return inline.text if inline is not None else number.text if number is not None else None


def fill_color(book: ZipFile, data: ET.Element, cell_ref: str) -> str:
    styles = ET.fromstring(book.read("xl/styles.xml"))
    cell = data.find(f".//x:c[@r='{cell_ref}']", NS)
    style = int(cell.attrib["s"])
    xf = styles.findall("x:cellXfs/x:xf", NS)[style]
    fill = styles.findall("x:fills/x:fill", NS)[int(xf.attrib["fillId"])]
    foreground = fill.find("x:patternFill/x:fgColor", NS)
    return foreground.attrib["rgb"]


class ExcelExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "dataset.sqlite"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                'CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "NAME" TEXT, '
                '"GROUP" TEXT, "AGE" REAL, "ADT" REAL, "DIFF" TEXT, "WARN" INTEGER)'
            )
            connection.executemany(
                'INSERT INTO dataset("NAME", "GROUP", "AGE", "ADT", "DIFF", "WARN") '
                'VALUES (?, ?, ?, ?, ?, ?)',
                [
                    ("=formula", "Y", 45, 24345, '["AGE"]', 0),
                    ("second", "N", 50, 24346, "[]", 0),
                    ("third", "Y", 60.25, 24349, "[]", 1),
                ],
            )
            connection.commit()
        metadata = DatasetMetadata(
            "fixture",
            3,
            (
                VariableMetadata("NAME", label="Subject", kind="character"),
                VariableMetadata("GROUP", kind="character"),
                VariableMetadata("AGE", kind="numeric"),
                VariableMetadata("ADT", kind="numeric", format="YYMMDD10."),
                VariableMetadata("DIFF", kind="character"),
                VariableMetadata("WARN", kind="numeric"),
            ),
            diff_columns_column="DIFF",
            row_warning_column="WARN",
            export_excluded_columns=("DIFF", "WARN"),
        )
        source = self.root / "fixture.sas7bdat"
        source.write_bytes(b"fixture")
        handle = DatasetHandle(source, source, self.database, metadata)
        self.view = ExcelExportView(
            handle,
            ("NAME", "AGE", "ADT", "DIFF"),
            FilterEngine(metadata.variables).compile('GROUP = "Y"'),
            SortSpec("AGE", ascending=False),
            'GROUP = "Y"',
            ((1, "Light Purple"),),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_three_sheets_filtered_sorted_columns_dates_and_highlights(self) -> None:
        exporter = ExcelExporter()
        count, estimated = exporter.estimate_bytes(self.view)
        self.assertEqual(count, 2)
        self.assertGreater(estimated, 0)
        destination = self.root / "view.xlsx"
        self.assertEqual(exporter.export(self.view, destination), 2)
        with ZipFile(destination) as book:
            workbook = ET.fromstring(book.read("xl/workbook.xml"))
            names = [item.attrib["name"] for item in workbook.findall(".//x:sheet", NS)]
            self.assertEqual(names, ["Export Info", "Metadata", "Data"])
            info, metadata, data = (sheet(book, index) for index in (1, 2, 3))
            self.assertEqual(value(info, "B4"), 'GROUP = "Y"')
            self.assertEqual(value(info, "B5"), "AGE DESC")
            self.assertEqual(value(info, "B6"), "2")
            self.assertEqual(value(metadata, "F5"), "Yes")
            self.assertEqual(value(metadata, "F6"), "No")
            self.assertEqual([value(data, ref) for ref in ("A1", "B1", "C1")], ["NAME", "AGE", "ADT"])
            self.assertEqual(value(data, "A2"), "third")
            self.assertEqual(value(data, "B2"), "60.25")
            self.assertEqual(value(data, "A3"), "=formula")
            self.assertEqual(value(data, "B3"), "45")
            self.assertIsNone(value(data, "D1"))
            self.assertIsNotNone(data.find("x:autoFilter", NS))
            self.assertIsNotNone(data.find("x:sheetViews/x:sheetView/x:pane", NS))
            self.assertIsNotNone(data.find("x:cols/x:col", NS))
            self.assertIsNotNone(data.find(".//x:c[@r='C2']/x:v", NS))
            self.assertEqual(fill_color(book, data, "B2"), "FFFFD9D9")
            self.assertEqual(fill_color(book, data, "B3"), "FFFFF2B2")
            self.assertEqual(fill_color(book, data, "A3"), "FFE8DDFF")
            self.assertNotIn("xl/worksheets/_rels", book.namelist())

    def test_invalid_output_keeps_existing_destination(self) -> None:
        destination = self.root / "existing.xlsx"
        destination.write_bytes(b"original")
        bad_view = ExcelExportView(
            self.view.handle,
            ("DIFF",),
            self.view.compiled_filter,
            None,
        )
        with self.assertRaisesRegex(ValueError, "No exportable columns"):
            ExcelExporter().export(bad_view, destination)
        self.assertEqual(destination.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob("*.part.xlsx")), [])

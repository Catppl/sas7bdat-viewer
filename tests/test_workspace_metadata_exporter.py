from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
from clinical_data_viewer.workspace_metadata_exporter import WorkspaceMetadataExporter

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def workbook_sheet_names(book: ZipFile) -> list[str]:
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    return [item.attrib["name"] for item in workbook.findall(".//x:sheet", NS)]


def sheet(book: ZipFile, index: int) -> ET.Element:
    return ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))


def value(xml: ET.Element, cell_ref: str) -> str | None:
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


class WorkspaceMetadataExporterTests(unittest.TestCase):
    def test_one_canonical_metadata_sheet_per_dataset_in_snapshot_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (root / "adsl.sas7bdat", root / "adae.xpt")
            calls: list[Path] = []
            progress: list[str] = []

            def read_metadata(path: Path) -> DatasetMetadata:
                calls.append(path)
                return DatasetMetadata(
                    path.stem,
                    10,
                    (
                        VariableMetadata("USUBJID", "Subject", "character", 20, "$20."),
                        VariableMetadata("AGE", "Age", "numeric", 8, "8."),
                    ),
                )

            destination = root / "workspace.xlsx"
            result = WorkspaceMetadataExporter(read_metadata).export(
                paths, destination, progress.append
            )

            self.assertEqual(calls, list(paths))
            self.assertEqual(result.dataset_count, 2)
            self.assertEqual(result.variable_count, 4)
            self.assertEqual(result.failures, ())
            self.assertEqual(
                progress,
                [
                    "Reading metadata 1/2: adsl.sas7bdat",
                    "Reading metadata 2/2: adae.xpt",
                ],
            )
            with ZipFile(destination) as book:
                self.assertEqual(workbook_sheet_names(book), ["adsl", "adae"])
                first = sheet(book, 1)
                self.assertEqual(
                    [value(first, f"{column}1") for column in "ABCDEF"],
                    ["Name", "Label", "Type", "Length", "Format", "Exported"],
                )
                self.assertEqual(value(first, "A2"), "USUBJID")
                self.assertEqual(value(first, "F2"), "Yes")

    def test_sheet_names_are_safe_unique_and_limited_to_31_characters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (
                root / "same.sas7bdat",
                root / "SAME.xpt",
                root / "a_very_long_dataset_filename_more_than_31_chars.sas7bdat",
            )
            destination = root / "safe.xlsx"
            metadata = DatasetMetadata(
                "fixture", 0, (VariableMetadata("A", kind="numeric"),)
            )
            WorkspaceMetadataExporter(lambda _path: metadata).export(paths, destination)
            with ZipFile(destination) as book:
                names = workbook_sheet_names(book)
            self.assertEqual(names[:2], ["same", "SAME_2"])
            self.assertEqual(len(names[2]), 31)
            self.assertEqual(len({name.casefold() for name in names}), 3)

    def test_failed_file_gets_error_sheet_and_other_files_continue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "adsl.sas7bdat"
            bad = root / "broken.xpt"
            destination = root / "partial.xlsx"

            def read_metadata(path: Path) -> DatasetMetadata:
                if path == bad:
                    raise PermissionError("The file is in use")
                return DatasetMetadata(path.stem, 1, (VariableMetadata("USUBJID"),))

            result = WorkspaceMetadataExporter(read_metadata).export(
                (good, bad), destination
            )
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(result.failures[0].path, bad)
            with ZipFile(destination) as book:
                self.assertEqual(workbook_sheet_names(book), ["adsl", "broken"])
                failure = sheet(book, 2)
                self.assertEqual(value(failure, "B2"), "Metadata read failed")
                self.assertEqual(value(failure, "B3"), "The file is in use")

    def test_empty_input_and_total_failure_preserve_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "existing.xlsx"
            destination.write_bytes(b"original")
            exporter = WorkspaceMetadataExporter(lambda _path: None)  # type: ignore[arg-type]
            with self.assertRaisesRegex(ValueError, "does not contain"):
                exporter.export((), destination)
            self.assertEqual(destination.read_bytes(), b"original")
            self.assertEqual(list(root.glob("*.part.xlsx")), [])


if __name__ == "__main__":
    unittest.main()

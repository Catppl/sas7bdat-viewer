from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from p21_issue_explorer.integration import SnapshotContractError, SnapshotReader
from snapshot_factory import create_snapshot


class SnapshotReaderTests(unittest.TestCase):
    def test_reads_valid_snapshot_and_opens_database_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = create_snapshot(
                Path(temp) / "snapshot",
                "AE",
                {"USUBJID": "character", "AESEQ": "numeric"},
                [{"_source_row": 1, "USUBJID": "S1", "AESEQ": 1}],
            )
            with SnapshotReader().open(root) as snapshot:
                self.assertEqual(snapshot.manifest.domain, "AE")
                self.assertEqual(
                    list(snapshot.records()),
                    [{"_source_row": 1, "USUBJID": "S1", "AESEQ": 1.0}],
                )
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    snapshot._connection.execute(
                        "INSERT INTO records VALUES (2, 'S2', 2)"
                    )

    def test_rejects_unknown_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = create_snapshot(
                Path(temp) / "snapshot",
                "AE",
                {"USUBJID": "character"},
                [{"_source_row": 1, "USUBJID": "S1"}],
            )
            path = root / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["snapshot_schema_version"] = 2
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                SnapshotContractError, "Unsupported snapshot_schema_version"
            ):
                SnapshotReader().open(root)

    def test_rejects_row_count_and_source_row_identity_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = create_snapshot(
                Path(temp) / "snapshot",
                "AE",
                {"USUBJID": "character"},
                [{"_source_row": 1, "USUBJID": "S1"}],
            )
            path = root / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["source_row_count"] = 2
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SnapshotContractError, "row count mismatch"):
                SnapshotReader().open(root)

    def test_requires_dedicated_unique_source_row_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = create_snapshot(
                Path(temp) / "snapshot",
                "AE",
                {"USUBJID": "character"},
                [{"_source_row": 1, "USUBJID": "S1"}],
            )
            connection = sqlite3.connect(root / "records.sqlite")
            connection.execute("DROP INDEX records_source_row_uq")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(SnapshotContractError, "unique index"):
                SnapshotReader().open(root)


if __name__ == "__main__":
    unittest.main()

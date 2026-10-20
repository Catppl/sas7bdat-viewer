from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from clinical_data_viewer.sqlite_access import (
    attach_read_only,
    connect_attachment_workspace,
    connect_read_only,
    connect_result_database,
    database_uri,
)


class SqliteAccessTests(unittest.TestCase):
    def test_uri_enabled_connections_and_read_only_attach_support_special_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "空 格 # O'Reilly"
            root.mkdir()
            source = root / "源 数据库#.sqlite"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE dataset (value TEXT)")
                connection.execute("INSERT INTO dataset VALUES ('ok')")
                connection.commit()

            real_connect = sqlite3.connect
            with patch(
                "clinical_data_viewer.sqlite_access.sqlite3.connect",
                wraps=real_connect,
            ) as mocked_connect:
                target = connect_result_database(root / "结果 database.sqlite")
                workspace = connect_attachment_workspace()
                read_only = connect_read_only(source)

            try:
                self.assertTrue(
                    all(
                        call.kwargs.get("uri") is True
                        for call in mocked_connect.call_args_list
                    )
                )
                attach_read_only(
                    target,
                    "source_db",
                    source,
                    stage="attach_source_database",
                )
                attach_read_only(
                    workspace,
                    "source_db",
                    source,
                    stage="attach_source_database",
                )
                self.assertEqual(
                    target.execute("SELECT value FROM source_db.dataset").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    workspace.execute("SELECT value FROM source_db.dataset").fetchone()[
                        0
                    ],
                    "ok",
                )
                self.assertEqual(
                    read_only.execute("SELECT value FROM dataset").fetchone()[0], "ok"
                )
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    target.execute("INSERT INTO source_db.dataset VALUES ('changed')")
            finally:
                read_only.close()
                workspace.close()
                target.close()

    def test_database_uri_percent_encodes_special_filename_characters(self) -> None:
        uri = database_uri(Path("folder") / "数据 #?.sqlite", mode="ro")
        self.assertIn("%23%3F.sqlite", uri)
        self.assertTrue(uri.endswith("?mode=ro"))

    def test_missing_cache_reports_the_operation_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            connection = connect_attachment_workspace()
            try:
                with self.assertRaisesRegex(
                    FileNotFoundError,
                    "attach_adsl_database failed.*Reopen or reload",
                ):
                    attach_read_only(
                        connection,
                        "adsl_db",
                        Path(temporary) / "missing.sqlite",
                        stage="attach_adsl_database",
                    )
            finally:
                connection.close()

    def test_attach_alias_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.sqlite"
            source.touch()
            connection = connect_attachment_workspace()
            try:
                with self.assertRaisesRegex(
                    ValueError, "Invalid SQLite database alias"
                ):
                    attach_read_only(
                        connection,
                        "source-db",
                        source,
                        stage="attach_source_database",
                    )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()

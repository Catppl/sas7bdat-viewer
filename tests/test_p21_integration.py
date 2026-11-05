from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.integrations.p21.host import P21HostController
from clinical_data_viewer.integrations.p21.manifest import (
    ManifestError,
    P21SidecarManifest,
    discover_sidecar_manifest,
)
from clinical_data_viewer.integrations.p21.protocol import (
    PROTOCOL_VERSION,
    FrameDecoder,
    ProtocolError,
    ProtocolMessage,
    encode_message,
)
from clinical_data_viewer.integrations.p21.result_view import (
    P21ResultBuilder,
    P21ResultSpec,
)
from clinical_data_viewer.integrations.p21.snapshot import (
    SnapshotBuilder,
    SnapshotCancelled,
    SnapshotRegistry,
)
from clinical_data_viewer.temp_manager import TempManager


def make_source(root: Path) -> DatasetHandle:
    directory = root / "source"
    directory.mkdir()
    database = directory / "dataset.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            "STUDYID TEXT, USUBJID TEXT, AESER TEXT, AVAL REAL)"
        )
        connection.executemany(
            "INSERT INTO dataset VALUES (?, ?, ?, ?, ?)",
            (
                (1, "STUDY", "01", "Y", 1.0),
                (2, "STUDY", "02", "N", None),
                (3, "STUDY", "03", "Y", 2.0),
            ),
        )
        connection.commit()
    source_file = directory / "AE.sas7bdat"
    source_file.touch()
    return DatasetHandle(
        source_file,
        source_file,
        database,
        DatasetMetadata(
            "AE",
            3,
            (
                VariableMetadata("STUDYID", kind="character"),
                VariableMetadata("USUBJID", kind="character"),
                VariableMetadata("AESER", kind="character"),
                VariableMetadata("AVAL", kind="numeric"),
            ),
        ),
        3,
        True,
    )


def make_large_source(root: Path, row_count: int = 25_000) -> DatasetHandle:
    directory = root / "large-source"
    directory.mkdir()
    database = directory / "dataset.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            "STUDYID TEXT, USUBJID TEXT, PARAMCD TEXT, AVAL REAL)"
        )
        connection.executemany(
            "INSERT INTO dataset VALUES (?, ?, ?, ?, ?)",
            (
                (index, "STUDY", f"SUBJ-{index:06d}", "TEST", float(index))
                for index in range(1, row_count + 1)
            ),
        )
        connection.commit()
    source_file = directory / "ADLB.sas7bdat"
    source_file.touch()
    return DatasetHandle(
        source_file,
        source_file,
        database,
        DatasetMetadata(
            "ADLB",
            row_count,
            (
                VariableMetadata("STUDYID", kind="character"),
                VariableMetadata("USUBJID", kind="character"),
                VariableMetadata("PARAMCD", kind="character"),
                VariableMetadata("AVAL", kind="numeric"),
            ),
        ),
        row_count,
        True,
    )


class P21ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.message = ProtocolMessage(
            PROTOCOL_VERSION,
            "request-1",
            "hello",
            "token",
            {"client": "test"},
        )

    def test_decoder_accepts_partial_and_multiple_frames(self) -> None:
        frame = encode_message(self.message)
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(frame[:3]), ())
        self.assertEqual(decoder.feed(frame[3:7]), ())
        self.assertEqual(decoder.feed(frame[7:]), (self.message,))
        self.assertEqual(decoder.feed(frame + frame), (self.message, self.message))

    def test_decoder_rejects_invalid_lengths_and_json(self) -> None:
        with self.assertRaises(ProtocolError):
            FrameDecoder().feed(struct.pack(">I", 0))
        with self.assertRaises(ProtocolError):
            FrameDecoder(max_payload_bytes=4).feed(struct.pack(">I", 5))
        with self.assertRaises(ProtocolError):
            FrameDecoder().feed(struct.pack(">I", 1) + b"{")

    def test_host_rejects_wrong_token_and_version_before_handshake(self) -> None:
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication([])

        class FakeSocket:
            def __init__(self) -> None:
                self.aborted = False
                self.written = bytearray()

            def abort(self) -> None:
                self.aborted = True

            def write(self, value: bytes) -> int:
                self.written.extend(value)
                return len(value)

            def flush(self) -> bool:
                return True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "sidecar"
            executable.touch()
            manifest = P21SidecarManifest(
                root / "manifest.json", executable, (), PROTOCOL_VERSION, "0" * 64
            )
            manager = TempManager(root / "temp")
            host = P21HostController(
                manifest,
                manager,
                lambda _domain: None,
                dict,
                lambda _kind, _payload: {},
            )
            host.session_token = "correct"
            wrong_token = FakeSocket()
            host.socket = wrong_token  # type: ignore[assignment]
            host._handle_message(
                ProtocolMessage(PROTOCOL_VERSION, "1", "hello", "wrong", {})
            )
            self.assertTrue(wrong_token.aborted)

            wrong_version = FakeSocket()
            host.socket = wrong_version  # type: ignore[assignment]
            host._handle_message(
                ProtocolMessage(PROTOCOL_VERSION + 1, "2", "hello", "correct", {})
            )
            self.assertTrue(wrong_version.aborted)

            accepted = FakeSocket()
            host.socket = accepted  # type: ignore[assignment]
            host._handle_message(
                ProtocolMessage(PROTOCOL_VERSION, "3", "hello", "correct", {})
            )
            messages = FrameDecoder().feed(accepted.written)
            self.assertEqual(messages[0].message_type, "hello_ack")
            self.assertTrue(host._authenticated)
            source = make_source(root)
            snapshot = host.snapshot_builder.build(source, "AE", ["AESER"])
            host.snapshots.add(snapshot)
            host.socket = None
            host._process_finished()
            self.assertEqual(len(host.snapshots), 0)
            self.assertFalse(snapshot.root.exists())
            application.processEvents()

    def test_local_socket_handshake_snapshot_and_release_round_trip(self) -> None:
        from PySide6.QtNetwork import QLocalSocket
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication([])

        def spin_until(predicate, timeout: float = 2.0) -> None:
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.001)
            self.assertTrue(predicate())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "sidecar"
            executable.touch()
            manifest = P21SidecarManifest(
                root / "manifest.json", executable, (), PROTOCOL_VERSION, "0" * 64
            )
            manager = TempManager(root / "temp")
            source = make_source(root)
            host = P21HostController(
                manifest,
                manager,
                lambda domain: source if domain.casefold() == "ae" else None,
                dict,
                lambda _kind, _payload: {},
            )
            host.server_name = f"p21-test-{os.getpid()}-{time.monotonic_ns()}"
            host.session_token = "round-trip-token"
            if not host.server.listen(host.server_name):
                self.skipTest(
                    "This execution sandbox does not permit local sockets: "
                    + host.server.errorString()
                )
            socket = QLocalSocket()
            socket.connectToServer(host.server_name)
            spin_until(
                lambda: socket.state() == QLocalSocket.LocalSocketState.ConnectedState
            )

            hello = ProtocolMessage(
                PROTOCOL_VERSION, "hello-1", "hello", host.session_token, {}
            )
            socket.write(encode_message(hello))
            socket.flush()
            spin_until(lambda: socket.bytesAvailable() > 0)
            response = FrameDecoder().feed(bytes(socket.readAll()))
            self.assertEqual(response[0].message_type, "hello_ack")

            request = ProtocolMessage(
                PROTOCOL_VERSION,
                "snapshot-1",
                "request_dataset_snapshot",
                host.session_token,
                {"domain": "AE", "variables": ["AESER"]},
            )
            socket.write(encode_message(request))
            socket.flush()
            spin_until(lambda: socket.bytesAvailable() > 0)
            response = FrameDecoder().feed(bytes(socket.readAll()))[0]
            self.assertEqual(response.message_type, "snapshot_ready")
            snapshot_root = Path(response.payload["path"])
            self.assertTrue((snapshot_root / "manifest.json").is_file())
            self.assertEqual(len(host.snapshots), 1)

            release = ProtocolMessage(
                PROTOCOL_VERSION,
                "release-1",
                "release_snapshot",
                host.session_token,
                {"snapshot_id": response.payload["snapshot_id"]},
            )
            socket.write(encode_message(release))
            socket.flush()
            spin_until(
                lambda: (
                    host.snapshots.lease_count(str(response.payload["snapshot_id"]))
                    == 0
                )
            )
            self.assertTrue(snapshot_root.exists())
            second_request = ProtocolMessage(
                PROTOCOL_VERSION,
                "snapshot-2",
                "request_dataset_snapshot",
                host.session_token,
                {"domain": "AE", "variables": ["AESER"]},
            )
            socket.write(encode_message(second_request))
            socket.flush()
            spin_until(lambda: socket.bytesAvailable() > 0)
            second_response = FrameDecoder().feed(bytes(socket.readAll()))[0]
            self.assertEqual(
                second_response.payload["snapshot_id"], response.payload["snapshot_id"]
            )
            self.assertTrue(second_response.payload["cache_hit"])
            socket.write(
                encode_message(
                    ProtocolMessage(
                        PROTOCOL_VERSION,
                        "release-2",
                        "release_snapshot",
                        host.session_token,
                        {"snapshot_id": second_response.payload["snapshot_id"]},
                    )
                )
            )
            socket.flush()
            spin_until(
                lambda: (
                    host.snapshots.lease_count(
                        str(second_response.payload["snapshot_id"])
                    )
                    == 0
                )
            )
            host.snapshots.clear()
            self.assertFalse(snapshot_root.exists())
            socket.abort()
            host._reset_transport()

    def test_qprocess_launches_authenticates_and_stops_sidecar(self) -> None:
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication([])

        def spin_until(predicate, timeout: float = 5.0) -> bool:
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.002)
            return bool(predicate())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packaged = os.environ.get("P21_PACKAGED_EXECUTABLE")
            if packaged:
                executable = Path(packaged).resolve()
                self.assertTrue(executable.is_file())
                arguments: tuple[str, ...] = ()
                startup_timeout = 60.0
            else:
                sidecar_source = (
                    Path(__file__).parents[1]
                    / "sidecars"
                    / "p21-issue-explorer"
                    / "src"
                )
                bootstrap = (
                    "import sys;"
                    f"sys.path.insert(0, {str(sidecar_source)!r});"
                    "from p21_issue_explorer.app import main;"
                    "raise SystemExit(main())"
                )
                executable = Path(sys.executable)
                arguments = ("-c", bootstrap)
                startup_timeout = 5.0
            manifest = P21SidecarManifest(
                root / "manifest.json",
                executable,
                arguments,
                PROTOCOL_VERSION,
                "0" * 64,
            )
            manager = TempManager(root / "temp")
            host = P21HostController(
                manifest,
                manager,
                lambda _domain: None,
                dict,
                lambda _kind, _payload: {},
            )
            failures: list[str] = []
            host.launch_failed.connect(failures.append)
            host.launch()
            if not host.server.isListening():
                self.skipTest(
                    "This execution sandbox does not permit local sockets: "
                    + (failures[0] if failures else host.server.errorString())
                )
            self.assertTrue(
                spin_until(lambda: host._authenticated, startup_timeout),
                failures[0] if failures else host.process.errorString(),
            )
            self.assertTrue(host.running)
            host.shutdown()
            self.assertFalse(host.running)
            self.assertFalse(host.server.isListening())

    def test_snapshot_generation_is_dispatched_without_blocking_viewer(self) -> None:
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance() or QApplication([])

        class FakeSocket:
            def __init__(self) -> None:
                self.written = bytearray()

            def write(self, value: bytes) -> int:
                self.written.extend(value)
                return len(value)

            def flush(self) -> bool:
                return True

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "sidecar"
            executable.touch()
            source = make_source(root)
            host = P21HostController(
                P21SidecarManifest(
                    root / "manifest.json",
                    executable,
                    (),
                    PROTOCOL_VERSION,
                    "0" * 64,
                ),
                TempManager(root / "temp"),
                lambda _domain: source,
                dict,
                lambda _kind, _payload: {},
            )
            real_builder = host.snapshot_builder

            class SlowSnapshotBuilder:
                source_fingerprint = staticmethod(real_builder.source_fingerprint)
                select_variables = staticmethod(real_builder.select_variables)

                def build(self, *args, **kwargs):
                    time.sleep(0.2)
                    return real_builder.build(*args, **kwargs)

            host.snapshot_builder = SlowSnapshotBuilder()
            socket = FakeSocket()
            host.socket = socket  # type: ignore[assignment]
            host._authenticated = True
            host.session_token = "token"
            request = ProtocolMessage(
                PROTOCOL_VERSION,
                "async-snapshot",
                "request_dataset_snapshot",
                "token",
                {"domain": "AE", "variables": ["AESER"]},
            )
            started = time.monotonic()
            host._snapshot_requested(request)
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertFalse(socket.written)
            deadline = time.monotonic() + 3
            while not socket.written and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.005)
            self.assertTrue(socket.written)
            response = FrameDecoder().feed(socket.written)[0]
            self.assertEqual(response.message_type, "snapshot_ready")
            host.socket = None
            host.snapshots.clear()


class P21ManifestTests(unittest.TestCase):
    def test_manifest_requires_matching_executable_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "p21-sidecar"
            executable.write_bytes(b"sidecar")
            manifest = root / "p21-sidecar-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "executable": executable.name,
                        "arguments": ["--standalone-test"],
                        "sha256": hashlib.sha256(b"sidecar").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            parsed = P21SidecarManifest.read(manifest)
            self.assertEqual(parsed.executable, executable.resolve())
            self.assertEqual(parsed.arguments, ("--standalone-test",))
            executable.write_bytes(b"damaged")
            with self.assertRaises(ManifestError):
                P21SidecarManifest.read(manifest)
            self.assertIsNone(discover_sidecar_manifest(root))


class P21SnapshotTests(unittest.TestCase):
    def test_cancelled_snapshot_removes_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TempManager(root / "temp")
            source = make_large_source(root, 5_000)
            with self.assertRaises(SnapshotCancelled):
                SnapshotBuilder(manager).build(
                    source,
                    "ADLB",
                    ["PARAMCD"],
                    cancelled=lambda: True,
                )
            snapshot_root = manager.session_directory / "p21-snapshots"
            self.assertFalse(snapshot_root.exists() and any(snapshot_root.iterdir()))

    def test_snapshot_contract_contains_only_requested_and_stable_variables(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TempManager(root / "temp")
            source = make_source(root)
            snapshot = SnapshotBuilder(manager).build(source, "ae", ["AESER", "NOPE"])
            manifest = json.loads(snapshot.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["snapshot_schema_version"], 1)
            self.assertEqual(manifest["domain"], "AE")
            self.assertEqual(
                [item["name"] for item in manifest["variables"]],
                ["AESER", "STUDYID", "USUBJID"],
            )
            with closing(sqlite3.connect(snapshot.database_path)) as connection:
                columns = [
                    row[1] for row in connection.execute("PRAGMA table_info(records)")
                ]
                self.assertEqual(
                    columns, ["_source_row", "AESER", "STUDYID", "USUBJID"]
                )
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM records").fetchone()[0], 3
                )
                indexes = list(connection.execute("PRAGMA index_list(records)"))
                self.assertTrue(any(row[2] for row in indexes))
            registry = SnapshotRegistry(manager)
            registry.add(snapshot)
            registry.release(snapshot.snapshot_id)
            self.assertTrue(snapshot.root.exists())
            acquired = registry.acquire(
                "AE", snapshot.source_fingerprint, snapshot.variables
            )
            self.assertIs(acquired, snapshot)
            registry.release(snapshot.snapshot_id)
            registry.invalidate_changed_source("AE", "changed-source-fingerprint")
            self.assertFalse(snapshot.root.exists())

    def test_result_builder_deduplicates_source_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TempManager(root / "temp")
            source = make_source(root)
            result = P21ResultBuilder(manager).build(
                source,
                P21ResultSpec("AE", "RULE", (2, 2, 1), (), 'AESER = "Y"'),
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    "SELECT _source_row FROM dataset ORDER BY _source_row"
                ).fetchall()
            self.assertEqual(rows, [(1,), (2,)])

    def test_25k_snapshot_and_result_materialization_performance_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TempManager(root / "temp")
            source = make_large_source(root)
            started = time.monotonic()
            snapshot = SnapshotBuilder(manager).build(
                source, "ADLB", ["PARAMCD", "AVAL"]
            )
            snapshot_seconds = time.monotonic() - started
            with closing(sqlite3.connect(snapshot.database_path)) as connection:
                indexes = list(connection.execute("PRAGMA index_list(records)"))
            self.assertGreaterEqual(len(indexes), 5)
            self.assertLess(snapshot_seconds, 10.0)

            started = time.monotonic()
            result = P21ResultBuilder(manager).build(
                source,
                P21ResultSpec("ADLB", "RULE-PERF", tuple(range(1, 25_001)), ()),
            )
            result_seconds = time.monotonic() - started
            self.assertEqual(result.metadata.row_count, 25_000)
            self.assertLess(result_seconds, 10.0)


@unittest.skipUnless(os.environ.get("QT_QPA_PLATFORM") == "offscreen", "Qt UI test")
class P21ResultViewTests(unittest.TestCase):
    def test_large_result_build_is_dispatched_without_blocking_viewer(self) -> None:
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.filter_history import FilterHistory
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.ui.main_window import MainWindow

        class TestSettings(AppSettings):
            def save(self, path=None):
                return None

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TempManager(root / "temp")
            window = MainWindow(
                TestSettings(), manager, FilterHistory(root / "history.sqlite")
            )
            source_tab = window._make_dataset_tab(make_source(root))
            window.tabs.addTab(source_tab, "AE")
            source_tab.start()
            real_builder = window._p21_result_builder

            class SlowBuilder:
                def build(self, source, spec, **options):
                    time.sleep(0.2)
                    return real_builder.build(source, spec, **options)

            window._p21_result_builder = SlowBuilder()
            payload = {
                "domain": "AE",
                "rule_id": "RULE-ASYNC",
                "issue_id": "issue-async",
                "status": "Matched – Exact",
                "source_rows": [1],
                "row_styles": [{"source_row": 1, "status": "Matched – Exact"}],
                "where_text": 'USUBJID = "01"',
                "expected_where": 'USUBJID = "01"',
            }
            started = time.monotonic()
            response = window._handle_p21_result_request("open_result_view", payload)
            dispatch_seconds = time.monotonic() - started
            self.assertTrue(response["accepted"])
            self.assertLess(dispatch_seconds, 0.1)
            self.assertIsNone(window._p21_result_tab)
            deadline = time.monotonic() + 3
            while window._p21_result_tab is None and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.005)
            self.assertIsNotNone(window._p21_result_tab)
            window.close()
            application.processEvents()

    def test_issue_and_rule_requests_reuse_one_result_tab_and_preserve_source(
        self,
    ) -> None:
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.filter_history import FilterHistory
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.ui.main_window import MainWindow

        class TestSettings(AppSettings):
            def save(self, path=None):
                return None

        application = QApplication.instance() or QApplication([])

        def spin_until(predicate, timeout: float = 3.0) -> None:
            deadline = time.monotonic() + timeout
            while not predicate() and time.monotonic() < deadline:
                application.processEvents()
                time.sleep(0.001)
            self.assertTrue(predicate())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = TempManager(root / "temp")
            window = MainWindow(
                TestSettings(), manager, FilterHistory(root / "history.sqlite")
            )
            self.assertIsNone(window.p21_issue_explorer_action)
            source_tab = window._make_dataset_tab(make_source(root))
            window.tabs.addTab(source_tab, "AE")
            source_tab.start()
            original_where = source_tab.where_editor.toPlainText()
            exact = {
                "domain": "AE",
                "rule_id": "RULE1",
                "issue_id": "issue-1",
                "status": "Matched – Exact",
                "source_rows": [1],
                "row_styles": [{"source_row": 1, "status": "Matched – Exact"}],
                "where_text": 'USUBJID = "01"',
                "expected_where": 'USUBJID = "01"',
            }
            window._handle_p21_result_request("open_result_view", exact)
            spin_until(lambda: window._p21_result_tab is not None)
            result_tab = window._p21_result_tab
            self.assertIsNotNone(result_tab)
            assert result_tab is not None
            self.assertEqual(result_tab.where_editor.toPlainText(), 'USUBJID = "01"')
            self.assertEqual(source_tab.where_editor.toPlainText(), original_where)
            self.assertEqual(window.tabs.count(), 2)
            self.assertEqual(
                result_tab.model.source_row_highlights[1].name(), "#dff3e5"
            )

            summary = {
                "domain": "AE",
                "rule_id": "RULE1",
                "source_rows": [1, 1, 2],
                "row_styles": [
                    {"source_row": 1, "status": "Matched – Exact"},
                    {"source_row": 2, "status": "Review – Partial/Lenient Match"},
                ],
                "p21_issue_count": 3,
                "exact_record_count": 1,
                "review_record_count": 1,
                "selection_description": "Materialized P21 candidate selection",
            }
            window._handle_p21_result_request("update_result_view", summary)
            spin_until(
                lambda: (
                    window._p21_result_tab is not None
                    and window._p21_result_tab is not result_tab
                )
            )
            self.assertEqual(window.tabs.count(), 2)
            self.assertIsNot(window._p21_result_tab, result_tab)
            assert window._p21_result_tab is not None
            self.assertTrue(window._p21_result_tab.where_editor.isReadOnly())
            self.assertEqual(window._p21_result_tab.handle.metadata.row_count, 2)

            not_found = dict(exact)
            not_found.update(
                {
                    "status": "Not Found in Current Data",
                    "source_rows": [],
                    "row_styles": [],
                }
            )
            previous_tab = window._p21_result_tab
            window._handle_p21_result_request("open_result_view", not_found)
            spin_until(
                lambda: (
                    window._p21_result_tab is not None
                    and window._p21_result_tab is not previous_tab
                )
            )
            self.assertEqual(window.tabs.count(), 2)
            assert window._p21_result_tab is not None
            self.assertEqual(window._p21_result_tab.handle.metadata.row_count, 0)
            self.assertEqual(source_tab.where_editor.toPlainText(), original_where)
            window.close()
            application.processEvents()

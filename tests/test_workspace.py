from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from clinical_data_viewer.workspace import WorkspaceFavoritesStore, scan_workspace
from clinical_data_viewer.workspace.models import workspace_path_key

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


class WorkspaceScannerTests(unittest.TestCase):
    def test_scans_only_first_level_sas_datasets_with_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "b.XPT").touch()
            (root / "A.sas7bdat").touch()
            (root / "notes.txt").touch()
            nested = root / "nested"
            nested.mkdir()
            (nested / "hidden.sas7bdat").touch()

            snapshot = scan_workspace(root)

        self.assertEqual(snapshot.root, root.resolve())
        self.assertEqual(
            [entry.name for entry in snapshot.entries], ["A.sas7bdat", "b.XPT"]
        )

    def test_empty_and_invalid_workspace_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(scan_workspace(root).entries, ())
            file_path = root / "not-a-folder.sas7bdat"
            file_path.touch()
            with self.assertRaises(NotADirectoryError):
                scan_workspace(file_path)
            with self.assertRaises(FileNotFoundError):
                scan_workspace(root / "missing")

    def test_unicode_spaces_quotes_and_hash_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="临床 data #' ") as directory:
            root = Path(directory)
            expected = root / "中文 AE #1.sas7bdat"
            expected.touch()
            snapshot = scan_workspace(root)
        self.assertEqual(snapshot.entries[0].path, expected.resolve())


class WorkspaceFavoritesStoreTests(unittest.TestCase):
    def test_crud_upsert_and_unicode_round_trip_are_incremental(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "workspace_favorites.sqlite"
            first = WorkspaceFavoritesStore(database)
            second = WorkspaceFavoritesStore(database)
            workspace = root / "ADaM 数据"

            created = first.upsert_favorite(workspace, "生产 ADaM")
            self.assertEqual(second.list_favorites()[0].label, "生产 ADaM")
            second.rename_favorite(created.path_key, "QC ADaM")
            self.assertEqual(first.list_favorites()[0].label, "QC ADaM")
            updated = first.upsert_favorite(workspace, "Updated")
            self.assertEqual(len(second.list_favorites()), 1)
            self.assertEqual(updated.path_key, workspace_path_key(workspace))
            second.remove_favorite(updated.path_key)
            self.assertEqual(first.list_favorites(), ())

    def test_empty_label_falls_back_and_missing_folder_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFavoritesStore(root / "favorites.sqlite")
            missing = root / "offline-drive"
            favorite = store.upsert_favorite(missing, "   ")
            self.assertEqual(favorite.label, "offline-drive")
            self.assertEqual(store.list_favorites()[0].path, missing.resolve())

    def test_limit_blocks_only_new_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorkspaceFavoritesStore(root / "favorites.sqlite", limit=1)
            first = root / "one"
            store.upsert_favorite(first, "One")
            store.upsert_favorite(first, "One updated")
            with self.assertRaisesRegex(ValueError, "maximum of 1"):
                store.upsert_favorite(root / "two", "Two")
            self.assertEqual(store.list_favorites()[0].label, "One updated")


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class WorkspaceControllerTests(unittest.TestCase):
    def test_late_scan_result_cannot_replace_new_workspace(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.controllers.workspace_controller import (
            WorkspaceController,
        )
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.ui.workspace_panel import WorkspacePanel
        from clinical_data_viewer.workspace.models import WorkspaceSnapshot

        class Settings(AppSettings):
            def save(self, path=None):
                return None

        class Host:
            def __init__(self):
                self.tasks = []

            def show_folder_panel(self):
                return None

            def submit_workspace_task(self, owner, function, completed, failed):
                self.tasks.append((function, completed, failed))

            def open_or_focus_paths(self, paths):
                self.opened = paths

            def show_workspace_error(self, title, message, details=""):
                self.error = (title, message, details)

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            host = Host()
            panel = WorkspacePanel()
            controller = WorkspaceController(
                host,
                panel,
                WorkspaceFavoritesStore(root / "favorites.sqlite"),
                Settings(),
                panel,
            )
            controller.open_workspace(first)
            controller.open_workspace(second)
            host.tasks[1][1](WorkspaceSnapshot(second.resolve(), ()))
            host.tasks[0][1](WorkspaceSnapshot(first.resolve(), ()))
            application.processEvents()
            self.assertEqual(panel.root, second.resolve())

    def test_panel_shows_only_names_and_preserves_snapshot_when_hidden(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.ui.workspace_panel import WorkspacePanel
        from clinical_data_viewer.workspace.models import (
            WorkspaceEntry,
            WorkspaceSnapshot,
        )

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = root / "adsl.sas7bdat"
            second = root / "transport.xpt"
            panel = WorkspacePanel()
            panel.set_snapshot(
                WorkspaceSnapshot(
                    root,
                    (
                        WorkspaceEntry(first, first.name),
                        WorkspaceEntry(second, second.name),
                    ),
                )
            )
            self.assertEqual(panel.file_list.item(0).text(), "adsl.sas7bdat")
            self.assertEqual(panel.file_list.item(0).toolTip(), str(first))
            panel.search.setText("transport")
            self.assertTrue(panel.file_list.item(0).isHidden())
            self.assertFalse(panel.file_list.item(1).isHidden())
            panel.file_list.item(1).setSelected(True)
            opened = []
            panel.open_requested.connect(opened.append)
            panel.file_list.itemActivated.emit(panel.file_list.item(1))
            self.assertEqual(opened, [(second,)])
            panel.hide()
            panel.show()
            application.processEvents()
            self.assertEqual(panel.root, root)
            self.assertEqual(panel.search.text(), "transport")
            self.assertEqual(panel.file_list.item(1).data(Qt.UserRole), second)

    def test_main_window_wires_folder_panel_without_replacing_open_lifecycle(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QToolButton

        from clinical_data_viewer.filter_history import FilterHistory
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.main_window import LoadingPage, MainWindow

        class Settings(AppSettings):
            def save(self, path=None):
                return None

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(folder_panel_visible=False)
            window = MainWindow(
                settings,
                TempManager(root / "temp"),
                FilterHistory(root / "history.sqlite"),
                WorkspaceFavoritesStore(root / "favorites.sqlite"),
            )
            self.assertEqual(window.folder_action.text(), "Folder Panel")
            self.assertEqual(window.open_action.text(), "Open Dataset(s)…")
            self.assertEqual(
                window.open_tool_button.popupMode(), QToolButton.MenuButtonPopup
            )
            self.assertFalse(window.folder_dock.isVisible())
            window.show_folder_panel()
            self.assertFalse(window.variables_dock.isHidden())

            existing = (root / "existing.sas7bdat").resolve()
            loading = LoadingPage(existing)
            window.tabs.addTab(loading, existing.name)
            window._open_path = Mock()
            window.open_or_focus_paths((existing,))
            self.assertIs(window.tabs.currentWidget(), loading)
            window._open_path.assert_not_called()
            new_path = (root / "new.xpt").resolve()
            window.open_or_focus_paths((new_path,))
            window._open_path.assert_called_once_with(new_path)
            window.close()
            application.processEvents()

    def test_main_window_workspace_surface_contains_no_scanner_or_sqlite_logic(self) -> None:
        import inspect

        from clinical_data_viewer.ui.main_window import MainWindow

        source = inspect.getsource(MainWindow)
        self.assertNotIn("scan_workspace", source)
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("workspace_favorites (", source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu, QMessageBox, QWidget

from ..settings import AppSettings
from ..ui.workspace_panel import WorkspacePanel
from ..workspace import WorkspaceFavoritesStore, scan_workspace
from ..workspace.models import default_workspace_label
from ..workspace_metadata_exporter import (
    WorkspaceMetadataExporter,
    WorkspaceMetadataExportResult,
)


class WorkspaceControllerHost(Protocol):
    def submit_workspace_task(self, owner, function, completed, failed) -> None: ...

    def open_or_focus_paths(self, paths: tuple[Path, ...]) -> None: ...

    def show_folder_panel(self) -> None: ...

    def show_workspace_error(
        self, title: str, message: str, details: str = ""
    ) -> None: ...


class WorkspaceController:
    """Coordinate Workspace UI, background discovery, and favorite persistence."""

    def __init__(
        self,
        host: WorkspaceControllerHost,
        panel: WorkspacePanel,
        store: WorkspaceFavoritesStore,
        settings: AppSettings,
        parent: QWidget,
        metadata_exporter: WorkspaceMetadataExporter,
    ) -> None:
        self.host = host
        self.panel = panel
        self.store = store
        self.settings = settings
        self.parent = parent
        self.metadata_exporter = metadata_exporter
        self._generation = 0
        self._favorite_menus: list[QMenu] = []

        panel.browse_requested.connect(self.browse)
        panel.refresh_requested.connect(self.refresh)
        panel.workspace_selected.connect(self.open_workspace)
        panel.open_requested.connect(self._open_paths)
        panel.favorite_requested.connect(self.toggle_favorite)
        panel.remove_favorite_requested.connect(self.remove_current_favorite)
        panel.rename_favorite_requested.connect(self.rename_current_favorite)
        panel.close_workspace_requested.connect(self.close_workspace)
        panel.export_metadata_requested.connect(self.export_metadata)
        panel.shown.connect(self.refresh_favorites)
        self.refresh_favorites()

    def register_favorites_menu(self, menu: QMenu) -> None:
        self._favorite_menus.append(menu)
        menu.aboutToShow.connect(
            lambda current=menu: self.populate_favorites_menu(current)
        )
        self.populate_favorites_menu(menu)

    def populate_favorites_menu(self, menu: QMenu) -> None:
        menu.clear()
        try:
            favorites = self.store.list_favorites()
        except Exception as error:  # noqa: BLE001 - persistence boundary
            action = menu.addAction("Favorites unavailable")
            action.setEnabled(False)
            action.setToolTip(str(error))
            return
        if not favorites:
            action = menu.addAction("No favorite workspaces")
            action.setEnabled(False)
        else:
            for favorite in favorites:
                action = menu.addAction(favorite.label)
                action.setToolTip(str(favorite.path))
                action.triggered.connect(
                    lambda _checked=False, path=favorite.path: self.open_workspace(path)
                )
        menu.addSeparator()
        manage = menu.addAction("Manage Favorites…")
        manage.triggered.connect(self.host.show_folder_panel)

    def refresh_favorites(self) -> None:
        try:
            favorites = self.store.list_favorites()
        except Exception as error:  # noqa: BLE001 - persistence boundary
            self.host.show_workspace_error(
                "Favorite Workspaces", str(error), type(error).__name__
            )
            favorites = ()
        self.panel.set_favorites(favorites)
        for menu in self._favorite_menus:
            self.populate_favorites_menu(menu)

    def browse(self) -> None:
        initial = self.settings.last_workspace_directory or str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self.parent,
            "Open Folder as Workspace",
            initial,
            QFileDialog.ShowDirsOnly,
        )
        if folder:
            self.open_workspace(Path(folder))

    def open_workspace(self, root: Path) -> None:
        directory = Path(root).expanduser().resolve(strict=False)
        self.host.show_folder_panel()
        self._generation += 1
        generation = self._generation
        self.panel.set_loading(directory)

        def completed(snapshot) -> None:
            if generation != self._generation:
                return
            self.settings.last_workspace_directory = str(snapshot.root)
            self.settings.save()
            self.panel.set_snapshot(snapshot)
            self.refresh_favorites()

        def failed(message: str, details: str) -> None:
            if generation != self._generation:
                return
            self.panel.set_unavailable(directory, message)
            self.host.show_workspace_error("Open Workspace Failed", message, details)

        self.host.submit_workspace_task(
            self.panel,
            lambda _worker: scan_workspace(directory),
            completed,
            failed,
        )

    def refresh(self) -> None:
        if self.panel.root is not None:
            self.open_workspace(self.panel.root)

    def close_workspace(self) -> None:
        self._generation += 1
        self.panel.clear_workspace()
        self.refresh_favorites()

    def export_metadata(self) -> None:
        root = self.panel.root
        paths = self.panel.workspace_paths()
        if root is None or not paths:
            return
        initial = Path(self.settings.last_export_directory or str(root))
        suggested = initial / f"{root.name or 'workspace'}_metadata.xlsx"
        destination, _filter = QFileDialog.getSaveFileName(
            self.parent,
            "Export Workspace Metadata",
            str(suggested),
            "Excel files (*.xlsx)",
        )
        if not destination:
            return
        output = Path(destination)
        if output.suffix.lower() != ".xlsx":
            output = output.with_suffix(".xlsx")
        self.settings.last_export_directory = str(output.parent)
        self.settings.save()
        # Freeze the current snapshot. Refreshing or searching the panel while
        # the worker runs must not alter this export.
        frozen_paths = tuple(paths)
        self.panel.set_exporting(True)

        def completed(result: WorkspaceMetadataExportResult) -> None:
            self.panel.set_exporting(False)
            if result.failures:
                QMessageBox.warning(
                    self.parent,
                    "Workspace Metadata Export Complete",
                    f"Exported {result.dataset_count - len(result.failures):,} of "
                    f"{result.dataset_count:,} datasets to:\n{output}\n\n"
                    f"{len(result.failures):,} file(s) could not be read. "
                    "Their worksheets contain error details.",
                )
            else:
                QMessageBox.information(
                    self.parent,
                    "Workspace Metadata Export Complete",
                    f"Exported metadata for {result.dataset_count:,} datasets "
                    f"({result.variable_count:,} variables) to:\n{output}",
                )

        def failed(message: str, details: str) -> None:
            self.panel.set_exporting(False)
            self.host.show_workspace_error(
                "Workspace Metadata Export Failed", message, details
            )

        self.host.submit_workspace_task(
            self.panel,
            lambda worker: self.metadata_exporter.export(
                frozen_paths, output, worker.report
            ),
            completed,
            failed,
        )

    def toggle_favorite(self) -> None:
        root = self.panel.root
        if root is None:
            return
        try:
            existing = self.store.favorite_for_path(root)
            if existing is not None:
                self.store.remove_favorite(existing.path_key)
            else:
                default = default_workspace_label(root)
                label, accepted = QInputDialog.getText(
                    self.parent,
                    "Add Favorite Workspace",
                    f"Folder\n{root}\n\nName",
                    text=default,
                )
                if not accepted:
                    return
                self.store.upsert_favorite(root, label)
        except Exception as error:  # noqa: BLE001 - UI persistence boundary
            QMessageBox.warning(self.parent, "Favorite Workspace", str(error))
        self.refresh_favorites()

    def rename_current_favorite(self) -> None:
        root = self.panel.root
        if root is None:
            return
        favorite = self.store.favorite_for_path(root)
        if favorite is None:
            return
        label, accepted = QInputDialog.getText(
            self.parent,
            "Rename Favorite Workspace",
            "Name",
            text=favorite.label,
        )
        if not accepted:
            return
        try:
            self.store.rename_favorite(favorite.path_key, label)
        except Exception as error:  # noqa: BLE001 - UI persistence boundary
            QMessageBox.warning(self.parent, "Favorite Workspace", str(error))
        self.refresh_favorites()

    def remove_current_favorite(self) -> None:
        root = self.panel.root
        if root is None:
            return
        favorite = self.store.favorite_for_path(root)
        if favorite is None:
            return
        try:
            self.store.remove_favorite(favorite.path_key)
        except Exception as error:  # noqa: BLE001 - UI persistence boundary
            QMessageBox.warning(self.parent, "Favorite Workspace", str(error))
        self.refresh_favorites()

    def _open_paths(self, paths: tuple[Path, ...]) -> None:
        self.host.open_or_focus_paths(paths)

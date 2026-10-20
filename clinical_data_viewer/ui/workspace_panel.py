from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..workspace.models import (
    WorkspaceFavorite,
    WorkspaceSnapshot,
    workspace_path_key,
)


class WorkspacePanel(QWidget):
    """Presentation-only Folder Panel for one active workspace snapshot."""

    browse_requested = Signal()
    refresh_requested = Signal()
    workspace_selected = Signal(object)
    open_requested = Signal(object)
    favorite_requested = Signal()
    remove_favorite_requested = Signal()
    rename_favorite_requested = Signal()
    close_workspace_requested = Signal()
    export_metadata_requested = Signal()
    collapse_requested = Signal()
    shown = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("workspacePanel")
        self._root: Path | None = None
        self._favorites: tuple[WorkspaceFavorite, ...] = ()
        self._updating = False
        self._exporting = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel("Folder Panel")
        title.setObjectName("panelTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        collapse = QToolButton()
        collapse.setText("«")
        collapse.setToolTip("Hide Folder panel")
        collapse.clicked.connect(self.collapse_requested)
        title_row.addWidget(collapse)
        layout.addLayout(title_row)

        workspace_row = QHBoxLayout()
        self.workspace_selector = QComboBox()
        self.workspace_selector.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.workspace_selector.setMinimumContentsLength(12)
        self.workspace_selector.currentIndexChanged.connect(self._workspace_changed)
        workspace_row.addWidget(self.workspace_selector, 1)
        self.favorite_button = QToolButton()
        self.favorite_button.setText("☆")
        self.favorite_button.setToolTip("Add current workspace to Favorites")
        self.favorite_button.clicked.connect(self.favorite_requested)
        workspace_row.addWidget(self.favorite_button)
        self.more_button = QToolButton()
        self.more_button.setText("…")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self.more_button)
        browse_action = more_menu.addAction("Open Folder as Workspace…")
        browse_action.triggered.connect(self.browse_requested)
        self.rename_action = more_menu.addAction("Rename Favorite…")
        self.rename_action.triggered.connect(self.rename_favorite_requested)
        self.remove_action = more_menu.addAction("Remove from Favorites")
        self.remove_action.triggered.connect(self.remove_favorite_requested)
        more_menu.addSeparator()
        self.export_metadata_action = more_menu.addAction("Export Workspace Metadata…")
        self.export_metadata_action.triggered.connect(self.export_metadata_requested)
        more_menu.addSeparator()
        close_action = more_menu.addAction("Close Workspace")
        close_action.triggered.connect(self.close_workspace_requested)
        self.more_button.setMenu(more_menu)
        workspace_row.addWidget(self.more_button)
        layout.addLayout(workspace_row)

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search files…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_search)
        search_row.addWidget(self.search, 1)
        self.refresh_button = QToolButton()
        self.refresh_button.setText("↻")
        self.refresh_button.setToolTip("Refresh workspace")
        self.refresh_button.clicked.connect(self.refresh_requested)
        search_row.addWidget(self.refresh_button)
        layout.addLayout(search_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        layout.addWidget(separator)

        self.message_label = QLabel("Open a folder to view its SAS datasets.")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        self.open_folder_button = QPushButton("Open Folder as Workspace…")
        self.open_folder_button.clicked.connect(self.browse_requested)
        layout.addWidget(self.open_folder_button)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_context_menu)
        self.file_list.itemDoubleClicked.connect(lambda _item: self._emit_open())
        self.file_list.itemActivated.connect(lambda _item: self._emit_open())
        layout.addWidget(self.file_list, 1)

        self.open_button = QPushButton("Open Selected")
        self.open_button.clicked.connect(self._emit_open)
        layout.addWidget(self.open_button)
        self._sync_controls()

    @property
    def root(self) -> Path | None:
        return self._root

    def set_favorites(self, favorites: tuple[WorkspaceFavorite, ...]) -> None:
        self._favorites = favorites
        current_key = workspace_path_key(self._root) if self._root else None
        self._updating = True
        self.workspace_selector.clear()
        self.workspace_selector.addItem("Choose workspace…", None)
        favorite_keys = {item.path_key for item in favorites}
        if self._root is not None and current_key not in favorite_keys:
            self.workspace_selector.addItem(
                self._root.name or str(self._root), self._root
            )
            self.workspace_selector.setItemData(1, str(self._root), Qt.ToolTipRole)
        for favorite in favorites:
            self.workspace_selector.addItem(favorite.label, favorite.path)
            index = self.workspace_selector.count() - 1
            self.workspace_selector.setItemData(
                index, str(favorite.path), Qt.ToolTipRole
            )
        target = 0
        if current_key is not None:
            for index in range(1, self.workspace_selector.count()):
                value = self.workspace_selector.itemData(index)
                if value is not None and workspace_path_key(value) == current_key:
                    target = index
                    break
        self.workspace_selector.setCurrentIndex(target)
        self._updating = False
        self._sync_controls()

    def set_loading(self, root: Path) -> None:
        self._root = root
        self.file_list.clear()
        self.message_label.setText("Scanning workspace…")
        self.file_list.setToolTip("")
        self.set_favorites(self._favorites)
        self._sync_controls(busy=True)

    def set_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        self._root = snapshot.root
        self.file_list.clear()
        for entry in snapshot.entries:
            item = QListWidgetItem(entry.name)
            item.setData(Qt.UserRole, entry.path)
            item.setToolTip(str(entry.path))
            self.file_list.addItem(item)
        self.message_label.setText(
            "" if snapshot.entries else "No SAS datasets were found in this folder."
        )
        if snapshot.warnings:
            self.file_list.setToolTip("\n".join(snapshot.warnings))
        else:
            self.file_list.setToolTip("")
        self.set_favorites(self._favorites)
        self._apply_search(self.search.text())
        self._sync_controls()

    def set_unavailable(self, root: Path, message: str) -> None:
        self._root = root
        self.file_list.clear()
        self.message_label.setText(
            "This workspace folder is unavailable.\n"
            "Reconnect the drive or choose another folder."
        )
        self.message_label.setToolTip(message)
        self.set_favorites(self._favorites)
        self._sync_controls()

    def clear_workspace(self) -> None:
        self._root = None
        self.file_list.clear()
        self.search.clear()
        self.message_label.setText("Open a folder to view its SAS datasets.")
        self.message_label.setToolTip("")
        self.file_list.setToolTip("")
        self.set_favorites(self._favorites)
        self._sync_controls()

    def selected_paths(self) -> tuple[Path, ...]:
        return tuple(item.data(Qt.UserRole) for item in self.file_list.selectedItems())

    def workspace_paths(self) -> tuple[Path, ...]:
        """Return the complete snapshot, including search-hidden entries."""

        return tuple(
            Path(self.file_list.item(index).data(Qt.UserRole))
            for index in range(self.file_list.count())
        )

    def set_exporting(self, exporting: bool) -> None:
        self._exporting = exporting
        self._sync_controls()

    def _workspace_changed(self, index: int) -> None:
        if self._updating or index <= 0:
            return
        path = self.workspace_selector.itemData(index)
        if path is not None and (
            self._root is None
            or workspace_path_key(path) != workspace_path_key(self._root)
        ):
            self.workspace_selected.emit(Path(path))

    def _apply_search(self, text: str) -> None:
        needle = text.strip().casefold()
        for index in range(self.file_list.count()):
            item = self.file_list.item(index)
            item.setHidden(bool(needle and needle not in item.text().casefold()))

    def _emit_open(self) -> None:
        paths = self.selected_paths()
        if paths:
            self.open_requested.emit(paths)

    def _show_context_menu(self, position) -> None:
        item = self.file_list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.file_list.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        containing_action = menu.addAction("Open Containing Folder")
        copy_action = menu.addAction("Copy Full Path")
        chosen: QAction | None = menu.exec(self.file_list.mapToGlobal(position))
        path = Path(item.data(Qt.UserRole))
        if chosen is open_action:
            self._emit_open()
        elif chosen is containing_action:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        elif chosen is copy_action:
            QApplication.clipboard().setText(str(path))

    def _sync_controls(self, *, busy: bool = False) -> None:
        favorite = None
        if self._root is not None:
            key = workspace_path_key(self._root)
            favorite = next(
                (item for item in self._favorites if item.path_key == key), None
            )
        self.favorite_button.setText("★" if favorite else "☆")
        self.favorite_button.setToolTip(
            "Remove current workspace from Favorites"
            if favorite
            else "Add current workspace to Favorites"
        )
        has_root = self._root is not None
        self.favorite_button.setEnabled(has_root and not busy)
        self.refresh_button.setEnabled(has_root and not busy)
        self.rename_action.setEnabled(favorite is not None and not busy)
        self.remove_action.setEnabled(favorite is not None and not busy)
        self.open_button.setEnabled(self.file_list.count() > 0 and not busy)
        self.export_metadata_action.setEnabled(
            self.file_list.count() > 0 and not busy and not self._exporting
        )
        self.open_folder_button.setVisible(not has_root)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.shown.emit()

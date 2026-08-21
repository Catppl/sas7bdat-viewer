from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetMetadata


class VariablesPanel(QWidget):
    visibility_changed = Signal(list)
    variable_activated = Signal(str)
    collapse_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("variablesPanel")
        self._updating = False
        self._metadata: DatasetMetadata | None = None
        self._visible: list[str] = []
        self._search_text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(6)
        title_row = QHBoxLayout()
        title = QLabel("Variables")
        title.setObjectName("panelTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        collapse = QToolButton()
        collapse.setText("«")
        collapse.setToolTip("Hide Variables panel")
        collapse.clicked.connect(self.collapse_requested)
        title_row.addWidget(collapse)
        layout.addLayout(title_row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter variables")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.set_search)
        layout.addWidget(self.search)
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        layout.addWidget(separator)
        self.displayed_label = QLabel("Displayed Columns")
        layout.addWidget(self.displayed_label)
        self.select_all = QCheckBox("Select All")
        self.select_all.setChecked(True)
        self.select_all.clicked.connect(self._toggle_all_selection)
        layout.addWidget(self.select_all)
        self.displayed_tree = QTreeWidget()
        self.displayed_tree.setHeaderHidden(True)
        self.displayed_tree.setRootIsDecorated(False)
        self.displayed_tree.setUniformRowHeights(True)
        self.displayed_tree.itemChanged.connect(self._displayed_changed)
        self.displayed_tree.itemClicked.connect(
            lambda item, _column: self.variable_activated.emit(
                item.data(0, Qt.UserRole)
            )
        )
        layout.addWidget(self.displayed_tree, 1)
        self.all_toggle = QToolButton()
        self.all_toggle.setCheckable(True)
        self.all_toggle.setChecked(False)
        self.all_toggle.setArrowType(Qt.RightArrow)
        self.all_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.all_toggle.clicked.connect(self._toggle_all_variables)
        layout.addWidget(self.all_toggle)
        self.all_tree = QTreeWidget()
        self.all_tree.setHeaderLabels(["Variable", "Label", "Type", "Length", "Format"])
        self.all_tree.setRootIsDecorated(False)
        self.all_tree.setAlternatingRowColors(True)
        self.all_tree.setUniformRowHeights(True)
        self.all_tree.setVisible(False)
        self.all_tree.itemChanged.connect(self._all_changed)
        self.all_tree.itemClicked.connect(
            lambda item, _column: self.variable_activated.emit(
                item.data(0, Qt.UserRole)
            )
        )
        header = self.all_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for section in range(1, 5):
            header.setSectionResizeMode(section, QHeaderView.Interactive)
        layout.addWidget(self.all_tree, 1)
        self._update_labels()

    def set_dataset(
        self, metadata: DatasetMetadata | None, visible: list[str] | None = None
    ) -> None:
        self._metadata = metadata
        self._visible = list(visible or ()) if metadata else []
        if metadata and visible is None:
            self._visible = [variable.name for variable in metadata.variables]
        self._rebuild()

    def _rebuild(self) -> None:
        self._updating = True
        self.displayed_tree.clear()
        self.all_tree.clear()
        if self._metadata:
            visible_set = set(self._visible)
            by_name = {variable.name: variable for variable in self._metadata.variables}
            for name in self._visible:
                variable = by_name.get(name)
                if not variable:
                    continue
                kind_marker = "A" if variable.kind == "character" else "#"
                item = QTreeWidgetItem([f"{kind_marker}    {variable.name}"])
                item.setData(0, Qt.UserRole, variable.name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(0, Qt.Checked)
                item.setToolTip(0, self._metadata_tooltip(variable))
                self.displayed_tree.addTopLevelItem(item)
            for variable in self._metadata.variables:
                item = QTreeWidgetItem(
                    [
                        variable.name,
                        variable.label,
                        variable.kind.title(),
                        "" if variable.length is None else str(variable.length),
                        variable.format,
                    ]
                )
                item.setData(0, Qt.UserRole, variable.name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(
                    0, Qt.Checked if variable.name in visible_set else Qt.Unchecked
                )
                item.setToolTip(0, self._metadata_tooltip(variable))
                self.all_tree.addTopLevelItem(item)
        self._updating = False
        self._sync_select_all()
        self._update_labels()
        self._apply_search()

    @staticmethod
    def _metadata_tooltip(variable) -> str:
        return (
            f"Variable: {variable.name}\nLabel: {variable.label or '-'}\nType: {variable.kind.title()}\n"
            f"Length: {variable.length if variable.length is not None else '-'}\nFormat: {variable.format or '-'}"
        )

    def set_search(self, text: str) -> None:
        self._search_text = text.strip().casefold()
        if self.search.text() != text:
            self.search.blockSignals(True)
            self.search.setText(text)
            self.search.blockSignals(False)
        self._apply_search()

    def _apply_search(self) -> None:
        needle = self._search_text
        for tree in (self.displayed_tree, self.all_tree):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                haystack = " ".join(
                    item.text(column) for column in range(tree.columnCount())
                ).casefold()
                item.setHidden(bool(needle and needle not in haystack))

    def visible_variables(self) -> list[str]:
        return list(self._visible)

    def _toggle_all_selection(self, _checked: bool) -> None:
        if self._updating or not self._metadata:
            return
        total = len(self._metadata.variables)
        self._visible = (
            []
            if total and len(self._visible) == total
            else [variable.name for variable in self._metadata.variables]
        )
        self._rebuild()
        self.visibility_changed.emit(self.visible_variables())

    def _displayed_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0 or item.checkState(0) == Qt.Checked:
            return
        name = item.data(0, Qt.UserRole)
        self._visible = [entry for entry in self._visible if entry != name]
        self.visibility_changed.emit(self.visible_variables())
        QTimer.singleShot(0, self._rebuild)

    def _all_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        name = item.data(0, Qt.UserRole)
        if item.checkState(0) == Qt.Checked and name not in self._visible:
            self._visible.append(name)
        elif item.checkState(0) == Qt.Unchecked and name in self._visible:
            self._visible.remove(name)
        self.visibility_changed.emit(self.visible_variables())
        QTimer.singleShot(0, self._rebuild)

    def _toggle_all_variables(self, checked: bool) -> None:
        self.all_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.all_tree.setVisible(checked)

    def _sync_select_all(self) -> None:
        self.select_all.blockSignals(True)
        total = len(self._metadata.variables) if self._metadata else 0
        if total and len(self._visible) == total:
            self.select_all.setCheckState(Qt.Checked)
        elif self._visible:
            self.select_all.setCheckState(Qt.PartiallyChecked)
        else:
            self.select_all.setCheckState(Qt.Unchecked)
        self.select_all.blockSignals(False)

    def _update_labels(self) -> None:
        total = len(self._metadata.variables) if self._metadata else 0
        self.displayed_label.setText(
            f"Displayed Columns ({len(self._visible)} of {total})"
        )
        self.all_toggle.setText(f"All Variables ({total})")

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetMetadata, RowComparisonResult
from ..settings import PROC_MEANS_STATISTICS
from ..statistics import StatisticsResult
from .proc_means_builder import ProcMeansBuilder


class AnalysisPanel(QWidget):
    locate_variable_requested = Signal(str)
    recalculate_requested = Signal()
    settings_requested = Signal()
    clear_comparison_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._create_statistics_tab()
        self._create_builder_tab()
        self._create_comparison_tab()

    def _create_statistics_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.statistics_scope = QLabel("Run PROC MEANS from a numeric column.")
        self.statistics_scope.setWordWrap(True)
        layout.addWidget(self.statistics_scope)
        self.statistics_table = QTableWidget(0, 2)
        self.statistics_table.setHorizontalHeaderLabels(["Statistic", "Value"])
        self.statistics_table.horizontalHeader().setStretchLastSection(True)
        self.statistics_table.setColumnWidth(0, 150)
        self.statistics_table.verticalHeader().hide()
        self.statistics_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.statistics_table, 1)
        buttons = QHBoxLayout()
        recalculate = QPushButton("Recalculate")
        recalculate.clicked.connect(self.recalculate_requested)
        settings = QPushButton("Settings…")
        settings.clicked.connect(self.settings_requested)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: self._copy_table(self.statistics_table))
        buttons.addWidget(recalculate)
        buttons.addWidget(settings)
        buttons.addWidget(copy)
        layout.addLayout(buttons)
        self.statistics_index = self.tabs.addTab(page, "PROC MEANS (Simple)")

    def _create_builder_tab(self) -> None:
        self.builder = ProcMeansBuilder()
        self.builder_index = self.tabs.addTab(self.builder, "PROC MEANS Builder")

    def _create_comparison_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.comparison_scope = QLabel("Select at least two rows to compare.")
        self.comparison_scope.setWordWrap(True)
        layout.addWidget(self.comparison_scope)
        self.comparison_table = QTableWidget(0, 0)
        self.comparison_table.verticalHeader().hide()
        self.comparison_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.comparison_table.cellDoubleClicked.connect(self._comparison_activated)
        layout.addWidget(self.comparison_table, 1)
        buttons = QHBoxLayout()
        clear = QPushButton("Clear Comparison")
        clear.clicked.connect(self.clear_comparison_requested)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: self._copy_table(self.comparison_table))
        buttons.addWidget(clear)
        buttons.addWidget(copy)
        layout.addLayout(buttons)
        self.comparison_index = self.tabs.addTab(page, "Row Comparison")

    @staticmethod
    def _format(value: float | None, decimals: int) -> str:
        if value is None:
            return "—"
        if isinstance(value, int):
            return f"{value:,}"
        quantum = Decimal(1).scaleb(-decimals)
        rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{rounded:,.{decimals}f}"

    def show_statistics(
        self,
        result: StatisticsResult,
        statistic_keys: list[str],
        decimal_offsets: dict[str, int],
        filter_description: str,
    ) -> None:
        labels = dict(PROC_MEANS_STATISTICS)
        self.statistics_scope.setText(
            f"{result.variable} — {result.label or 'Numeric'}\n"
            f"Scope: {result.filtered_rows:,} filtered rows; {filter_description}\n"
            f"Observed base decimals: {result.base_decimals}; maximum output: 4\n"
            f"Mean CI: {result.confidence * 100:g}% Student-t"
        )
        self.statistics_table.setRowCount(len(statistic_keys))
        for row, key in enumerate(statistic_keys):
            self.statistics_table.setItem(row, 0, QTableWidgetItem(labels[key]))
            self.statistics_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    self._format(
                        result.values.get(key),
                        min(4, result.base_decimals + decimal_offsets.get(key, 0)),
                    )
                ),
            )
        self.tabs.setCurrentIndex(self.statistics_index)

    def mark_statistics_stale(self) -> None:
        if self.statistics_table.rowCount():
            self.statistics_scope.setText(
                "The filter, sort, or dataset changed. Recalculate to refresh this result."
            )

    def clear_statistics(self) -> None:
        self.statistics_table.setRowCount(0)
        self.statistics_scope.setText("Run PROC MEANS from a numeric column.")

    def show_comparison(
        self, result: RowComparisonResult, metadata: DatasetMetadata
    ) -> None:
        by_name = {variable.name: variable for variable in metadata.variables}
        columns = ["Variable", "Label", "Type"] + [
            f"Row {row.view_row + 1}" for row in result.rows
        ]
        self.comparison_table.setColumnCount(len(columns))
        self.comparison_table.setHorizontalHeaderLabels(columns)
        self.comparison_table.setRowCount(len(result.differing_variables))
        positions = {
            variable.name: index for index, variable in enumerate(metadata.variables)
        }
        for table_row, name in enumerate(result.differing_variables):
            variable = by_name[name]
            self.comparison_table.setItem(table_row, 0, QTableWidgetItem(name))
            self.comparison_table.setItem(
                table_row, 1, QTableWidgetItem(variable.label)
            )
            self.comparison_table.setItem(
                table_row, 2, QTableWidgetItem(variable.kind.title())
            )
            value_index = positions[name]
            for result_column, row in enumerate(result.rows, start=3):
                value = row.values[value_index]
                self.comparison_table.setItem(
                    table_row,
                    result_column,
                    QTableWidgetItem("(Missing)" if value is None else str(value)),
                )
        self.comparison_scope.setText(
            f"Compared {len(result.rows)} rows; "
            f"{len(result.differing_variables)} variables differ. "
            "Double-click a variable to locate its visible column."
        )
        self.comparison_table.resizeColumnsToContents()
        self.tabs.setCurrentIndex(self.comparison_index)

    def clear_comparison(self) -> None:
        self.comparison_table.setRowCount(0)
        self.comparison_table.setColumnCount(0)
        self.comparison_scope.setText("Select at least two rows to compare.")

    def _comparison_activated(self, row: int, _column: int) -> None:
        item = self.comparison_table.item(row, 0)
        if item:
            self.locate_variable_requested.emit(item.text())

    @staticmethod
    def _copy_table(table: QTableWidget) -> None:
        lines = [
            "\t".join(
                table.horizontalHeaderItem(column).text()
                for column in range(table.columnCount())
            )
        ]
        for row in range(table.rowCount()):
            lines.append(
                "\t".join(
                    table.item(row, column).text() if table.item(row, column) else ""
                    for column in range(table.columnCount())
                )
            )
        QApplication.clipboard().setText("\n".join(lines))

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..models import ComparisonResult, IssueMatchResult, IssueOpenRequest


class IssueDetailWidget(QGroupBox):
    open_issue_requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__("Selected Issue", parent)
        self.setObjectName("issueDetail")
        self.result: IssueMatchResult | None = None
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.status_label = QLabel("Select an issue to inspect candidates.")
        self.status_label.setObjectName("selectedIssueStatus")
        form.addRow("Status", self.status_label)
        self.diagnostics_label = QLabel("—")
        self.diagnostics_label.setObjectName("selectedIssueDiagnostics")
        self.diagnostics_label.setWordWrap(True)
        form.addRow("Diagnostics", self.diagnostics_label)
        layout.addLayout(form)

        self.issue_text = QPlainTextEdit()
        self.issue_text.setObjectName("selectedIssueText")
        self.issue_text.setReadOnly(True)
        self.issue_text.setMinimumHeight(55)
        self.issue_text.setMaximumHeight(90)
        self.issue_text.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        layout.addWidget(self.issue_text)

        self.candidate_table = QTableWidget(0, 7)
        self.candidate_table.setObjectName("candidateDetailTable")
        self.candidate_table.setHorizontalHeaderLabels(
            (
                "Source Row",
                "Mapping",
                "Variable",
                "Result",
                "Expected",
                "Actual",
                "Reason",
            )
        )
        self.candidate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.candidate_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.candidate_table.verticalHeader().setVisible(False)
        self.candidate_table.setMinimumHeight(90)
        self.candidate_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        layout.addWidget(self.candidate_table)

        where_form = QFormLayout()
        self.expected_where = QPlainTextEdit()
        self.expected_where.setObjectName("expectedWhere")
        self.expected_where.setReadOnly(True)
        self.expected_where.setMinimumHeight(45)
        self.expected_where.setMaximumHeight(60)
        self.expected_where.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        where_form.addRow("Expected WHERE", self.expected_where)
        self.applied_where = QPlainTextEdit()
        self.applied_where.setObjectName("appliedWhere")
        self.applied_where.setReadOnly(True)
        self.applied_where.setMinimumHeight(45)
        self.applied_where.setMaximumHeight(60)
        self.applied_where.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        where_form.addRow("Applied WHERE", self.applied_where)
        layout.addLayout(where_form)

        self.open_button = QPushButton("Open Matched Records in SAS Viewer")
        self.open_button.setObjectName("openIssueInViewerButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._emit_request)
        layout.addWidget(self.open_button)

    def clear(self) -> None:
        self.result = None
        self.status_label.setText("Select an issue to inspect candidates.")
        self.diagnostics_label.setText("—")
        self.issue_text.clear()
        self.candidate_table.setRowCount(0)
        self.expected_where.clear()
        self.applied_where.clear()
        self.open_button.setEnabled(False)

    def set_result(self, result: IssueMatchResult) -> None:
        self.result = result
        self.status_label.setText(
            f"{result.status.value} · {result.current_match_count} current candidate(s)"
        )
        self.diagnostics_label.setText("; ".join(result.diagnostics) or "None")
        issue = result.issue
        self.issue_text.setPlainText(
            f"Rule ID: {issue.rule_id}\n"
            f"Record (P21 reference only): {issue.record}\n"
            f"Variables: {issue.variables}\n"
            f"Values: {issue.values}\n"
            f"Message: {issue.message}"
        )
        rows = [
            (candidate, path, comparison)
            for candidate in result.candidates
            for path in candidate.paths
            for comparison in path.comparisons
        ]
        self.candidate_table.setRowCount(len(rows))
        for row, (candidate, path, comparison) in enumerate(rows):
            values = (
                candidate.source_row,
                path.mapping_index + 1,
                comparison.variable,
                comparison.result.value,
                comparison.expected,
                "<missing>" if comparison.actual is None else comparison.actual,
                comparison.reason,
            )
            background = None
            if comparison.result is ComparisonResult.LENIENT:
                background = QColor("#fef3c7")
            elif comparison.result is ComparisonResult.MISMATCH:
                background = QColor("#fee2e2")
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if background is not None:
                    item.setBackground(background)
                self.candidate_table.setItem(row, column, item)
        self.candidate_table.resizeColumnsToContents()
        self.expected_where.setPlainText(result.expected_where)
        self.applied_where.setPlainText(result.applied_where)
        request = IssueOpenRequest.from_result(result)
        self.open_button.setEnabled(bool(request.where_text or request.source_rows))

    def _emit_request(self) -> None:
        if self.result is not None:
            self.open_issue_requested.emit(IssueOpenRequest.from_result(self.result))

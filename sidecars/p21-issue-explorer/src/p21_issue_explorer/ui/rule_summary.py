from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout

from ..models import RuleMatchSummary


class RuleSummaryWidget(QGroupBox):
    filter_all_requested = Signal()

    METRICS = (
        ("p21IssueCount", "P21 Issue Count", "p21_issue_count"),
        ("currentMatchCount", "Current Match Count", "current_candidate_record_count"),
        ("matchedIssueCount", "Matched Issue Count", "matched_issue_count"),
        (
            "partialReviewIssueCount",
            "Partial Review Issue Count",
            "partial_review_issue_count",
        ),
        ("notFoundIssueCount", "Not Found Issue Count", "not_found_issue_count"),
        (
            "cannotEvaluateIssueCount",
            "Cannot Evaluate Issue Count",
            "cannot_evaluate_issue_count",
        ),
        (
            "currentExactRecordCount",
            "Current Exact Record Count",
            "current_exact_record_count",
        ),
        (
            "currentReviewRecordCount",
            "Current Review Record Count",
            "current_review_record_count",
        ),
    )

    def __init__(self, parent=None) -> None:
        super().__init__("Rule ID Summary", parent)
        self.setObjectName("ruleSummary")
        outer = QVBoxLayout(self)
        grid = QGridLayout()
        self.value_labels: dict[str, QLabel] = {}
        for index, (object_name, title, _field) in enumerate(self.METRICS):
            row, column = divmod(index, 4)
            card = QGroupBox(title)
            card_layout = QVBoxLayout(card)
            value = QLabel("—")
            value.setObjectName(object_name)
            value.setStyleSheet("font-size: 15pt; font-weight: 600; color: #174b73;")
            card_layout.addWidget(value)
            grid.addWidget(card, row, column)
            self.value_labels[object_name] = value
        outer.addLayout(grid)
        self.filter_all_button = QPushButton("Filter All Matched Records")
        self.filter_all_button.setObjectName("filterAllMatchedRecordsButton")
        self.filter_all_button.setEnabled(False)
        self.filter_all_button.clicked.connect(self.filter_all_requested)
        outer.addWidget(self.filter_all_button)
        self.summary: RuleMatchSummary | None = None

    def clear(self) -> None:
        self.summary = None
        for label in self.value_labels.values():
            label.setText("—")
        self.filter_all_button.setEnabled(False)

    def set_summary(self, summary: RuleMatchSummary) -> None:
        self.summary = summary
        for object_name, _title, field in self.METRICS:
            self.value_labels[object_name].setText(str(getattr(summary, field)))
        self.filter_all_button.setEnabled(summary.current_candidate_record_count > 0)

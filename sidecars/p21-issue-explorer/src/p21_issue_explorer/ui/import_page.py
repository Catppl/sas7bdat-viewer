from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import P21Report
from ..report import P21ReportReader


class _LoadSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _ReportLoadTask(QRunnable):
    def __init__(
        self,
        reader: P21ReportReader,
        source_path: Path,
    ) -> None:
        super().__init__()
        self.reader = reader
        self.source_path = source_path
        self.signals = _LoadSignals()

    @Slot()
    def run(self) -> None:
        try:
            report = self.reader.read(self.source_path)
        # The task boundary must convert both expected import errors and unforeseen
        # parser defects into a visible error instead of terminating the GUI thread.
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(str(exc))
        else:
            self.signals.succeeded.emit(report)
        finally:
            self.signals.finished.emit()


class ImportPage(QWidget):
    report_loaded = Signal(object)
    import_failed = Signal(str)

    def __init__(
        self,
        *,
        reader: P21ReportReader | None = None,
        thread_pool: QThreadPool | None = None,
        file_picker: Callable[[QWidget], Path | None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.reader = reader or P21ReportReader()
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.file_picker = file_picker or self._pick_report
        self.report: P21Report | None = None
        self._generation = 0
        self._active_tasks: set[_ReportLoadTask] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("P21 Issue Explorer")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        description = QLabel(
            'Import the latest P21 report. Version 1 reads only "Issue Details" '
            "rows where Count is blank and Record is populated."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        import_row = QHBoxLayout()
        self.import_button = QPushButton("Import P21 Report…")
        self.import_button.setObjectName("importP21ReportButton")
        self.import_button.clicked.connect(self.choose_report)
        import_row.addWidget(self.import_button)
        import_row.addStretch(1)
        layout.addLayout(import_row)

        self.progress = QProgressBar()
        self.progress.setObjectName("importProgress")
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        form = QFormLayout()
        self.report_label = QLabel("No report imported")
        self.report_label.setObjectName("reportNameLabel")
        form.addRow("Report", self.report_label)
        self.status_label = QLabel("Choose the latest P21 .xlsx report.")
        self.status_label.setObjectName("importStatusLabel")
        self.status_label.setWordWrap(True)
        form.addRow("Status", self.status_label)
        self.domain_combo = QComboBox()
        self.domain_combo.setObjectName("domainCombo")
        self.domain_combo.setEnabled(False)
        form.addRow("Domain", self.domain_combo)
        layout.addLayout(form)
        layout.addStretch(1)

    @Slot()
    def choose_report(self) -> None:
        selected = self.file_picker(self)
        if selected is not None:
            self.load_report(selected)

    def load_report(self, source_path: str | Path) -> None:
        path = Path(source_path).expanduser().resolve()
        self._generation += 1
        generation = self._generation
        self._set_busy(True)
        self.report = None
        self.report_label.setText(path.name)
        self.status_label.setText("Importing Issue Details…")
        self.domain_combo.clear()
        self.domain_combo.setEnabled(False)

        task = _ReportLoadTask(self.reader, path)
        self._active_tasks.add(task)
        task.signals.succeeded.connect(
            lambda report, current=generation: self._handle_success(current, report)
        )
        task.signals.failed.connect(
            lambda message, current=generation: self._handle_failure(current, message)
        )
        task.signals.finished.connect(
            lambda current=generation, current_task=task: self._handle_finished(
                current, current_task
            )
        )
        self.thread_pool.start(task)

    def apply_report(self, report: P21Report) -> None:
        """Populate the Phase 1 UI from an already parsed report."""

        self.report = report
        self.report_label.setText(report.source_path.name)
        self.domain_combo.clear()
        self.domain_combo.addItems(report.domains)
        self.domain_combo.setEnabled(bool(report.domains))
        diagnostics = report.diagnostics
        self.status_label.setText(
            f"Imported {diagnostics.eligible_issue_rows} eligible issues from "
            f"worksheet row {diagnostics.header_row}. "
            f"Ignored {diagnostics.ignored_count_present_rows} rows with Count and "
            f"{diagnostics.ignored_record_blank_rows} rows without Record."
        )
        self.report_loaded.emit(report)

    def _handle_success(self, generation: int, report: P21Report) -> None:
        if generation == self._generation:
            self.apply_report(report)

    def _handle_failure(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self.report = None
        self.domain_combo.clear()
        self.domain_combo.setEnabled(False)
        self.status_label.setText(message)
        self.import_failed.emit(message)

    def _handle_finished(self, generation: int, task: _ReportLoadTask) -> None:
        self._active_tasks.discard(task)
        if generation == self._generation:
            self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.import_button.setEnabled(not busy)
        self.progress.setVisible(busy)

    @staticmethod
    def _pick_report(parent: QWidget) -> Path | None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            parent,
            "Import latest P21 report",
            "",
            "Excel workbooks (*.xlsx)",
        )
        return Path(filename) if filename else None

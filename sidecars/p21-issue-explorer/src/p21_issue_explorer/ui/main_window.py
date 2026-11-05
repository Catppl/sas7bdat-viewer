from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea

from ..integration import MappingSnapshotProvider, SnapshotProvider
from .explorer_page import ExplorerPage


class P21IssueExplorerWindow(QMainWindow):
    def __init__(
        self,
        *,
        initial_report: Path | None = None,
        snapshot_provider: SnapshotProvider | None = None,
        snapshot_paths: dict[str, Path] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("P21 Issue Explorer")
        self.resize(1420, 920)
        if snapshot_provider is None and snapshot_paths:
            snapshot_provider = MappingSnapshotProvider(snapshot_paths)
        self.explorer_page = ExplorerPage(snapshot_provider=snapshot_provider)
        # Compatibility alias for P0-P1 callers and tests.
        self.import_page = self.explorer_page
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("explorerScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(self.explorer_page)
        self.setCentralWidget(self.scroll_area)
        self.explorer_page.open_issue_requested.connect(
            lambda request: self.statusBar().showMessage(
                f"Viewer integration pending (P4): {request.domain} / {request.rule_id}"
            )
        )
        self.explorer_page.filter_all_requested.connect(
            lambda request: self.statusBar().showMessage(
                f"Viewer integration pending (P4): {request.domain} / "
                f"{request.rule_id} / {len(request.source_rows)} records"
            )
        )
        if initial_report is not None:
            self.explorer_page.load_report(initial_report)

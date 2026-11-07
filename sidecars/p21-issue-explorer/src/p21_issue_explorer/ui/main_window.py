from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFrame, QMainWindow, QScrollArea

from ..integration import MappingSnapshotProvider, SnapshotProvider
from ..ipc_client import P21IpcClient
from .explorer_page import ExplorerPage


class P21IssueExplorerWindow(QMainWindow):
    def __init__(
        self,
        *,
        initial_report: Path | None = None,
        snapshot_provider: SnapshotProvider | None = None,
        snapshot_paths: dict[str, Path] | None = None,
        ipc_client: P21IpcClient | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("P21 Issue Explorer")
        self.resize(1420, 920)
        self.ipc_client = ipc_client
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
        if ipc_client is not None:
            self.explorer_page.open_issue_requested.connect(
                ipc_client.send_issue_request
            )
            self.explorer_page.filter_all_requested.connect(
                ipc_client.send_rule_request
            )
            self.explorer_page.export_domain_requested.connect(
                ipc_client.send_domain_export_request
            )
            ipc_client.connected_to_viewer.connect(
                lambda: self.statusBar().showMessage("Connected to SASDataViewer")
            )
            ipc_client.disconnected_from_viewer.connect(
                lambda message: self.statusBar().showMessage(
                    f"Viewer connection unavailable: {message}"
                )
            )
        else:
            self.explorer_page.open_issue_requested.connect(
                lambda request: self.statusBar().showMessage(
                    f"Standalone mode: {request.domain} / {request.rule_id}"
                )
            )
        if initial_report is not None:
            self.explorer_page.load_report(initial_report)

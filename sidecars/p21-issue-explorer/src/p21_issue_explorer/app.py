from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import __version__
from .ui import P21IssueExplorerWindow

STYLE = """
QWidget { font-family: "Segoe UI"; font-size: 10pt; color: #1f2937; }
QMainWindow { background: #f4f8fc; }
QLabel#pageTitle { color: #174b73; font-size: 16pt; font-weight: 600; }
QPushButton { padding: 6px 12px; }
QComboBox { min-height: 24px; }
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P21 Issue Explorer side-car")
    parser.add_argument("--report", type=Path, help="Import a report after startup")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("P21 Issue Explorer")
    application.setStyleSheet(STYLE)
    window = P21IssueExplorerWindow(initial_report=arguments.report)
    window.show()
    return application.exec()

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class CodePreviewDialog(QDialog):
    def __init__(
        self,
        code: str,
        source_text: str,
        suggested_filename: str,
        *,
        language: str,
        extension: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.code = code
        self.suggested_filename = suggested_filename
        self.language = language
        self.extension = extension
        self.setWindowTitle(f"{language} Code Generator")
        self.resize(900, 680)
        layout = QVBoxLayout(self)
        source = QLabel(f"Source: {source_text}")
        source.setWordWrap(True)
        layout.addWidget(source)
        notice = QLabel(
            "Preview only. The generated program reads the original dataset; "
            f"SASDataViewer does not execute {language} code."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.editor.setPlainText(code)
        layout.addWidget(self.editor, 1)
        buttons = QHBoxLayout()
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self.copy_code)
        buttons.addWidget(copy_button)
        save_button = QPushButton("Save As…")
        save_button.clicked.connect(self.save_as)
        buttons.addWidget(save_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def copy_code(self) -> None:
        QApplication.clipboard().setText(self.code)

    def save_as(self) -> None:
        path_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            f"Save {self.language} Program",
            self.suggested_filename,
            f"{self.language} Programs (*.{self.extension});;All Files (*)",
        )
        if not path_text:
            return
        path = Path(path_text)
        if not path.suffix:
            path = path.with_suffix(f".{self.extension}")
        try:
            path.write_text(self.code, encoding="utf-8-sig")
        except OSError as error:
            QMessageBox.critical(self, "Save Failed", str(error))


class SasCodeDialog(CodePreviewDialog):
    def __init__(
        self, code: str, source_text: str, suggested_filename: str, parent=None
    ):
        super().__init__(
            code,
            source_text,
            suggested_filename,
            language="SAS",
            extension="sas",
            parent=parent,
        )


class RCodeDialog(CodePreviewDialog):
    def __init__(
        self, code: str, source_text: str, suggested_filename: str, parent=None
    ):
        super().__init__(
            code,
            source_text,
            suggested_filename,
            language="R",
            extension="R",
            parent=parent,
        )

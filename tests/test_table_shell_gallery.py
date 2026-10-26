from __future__ import annotations

import importlib.util
import os
import unittest


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class TableShellGalleryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_gallery_has_only_table_builders_and_filters(self) -> None:
        from clinical_data_viewer.ui.table_shell_gallery import TableShellGalleryDialog

        gallery = TableShellGalleryDialog()
        self.assertEqual(
            gallery.visible_shell_keys(),
            (
                "proc_means_vertical",
                "change_from_baseline",
                "categorical",
                "rule_based",
                "ae_table",
            ),
        )
        gallery.search.setText("baseline")
        self.assertEqual(gallery.visible_shell_keys(), ("change_from_baseline",))
        gallery.search.clear()
        gallery.category_buttons["Safety"].click()
        self.assertEqual(gallery.visible_shell_keys(), ("ae_table",))
        gallery.close()
        self.application.processEvents()

    def test_card_click_opens_preview_and_preview_confirms_builder(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton

        from clinical_data_viewer.ui.table_shell_gallery import (
            TableShellGalleryDialog,
        )

        gallery = TableShellGalleryDialog()
        requests: list[str] = []
        gallery.builder_requested.connect(requests.append)
        card = gallery._cards[0]
        QTest.mouseClick(card, Qt.LeftButton)
        self.assertIsNotNone(gallery._preview_dialog)
        assert gallery._preview_dialog is not None
        buttons = gallery._preview_dialog.findChildren(QPushButton)
        open_button = next(button for button in buttons if button.text() == "Open Builder")
        open_button.click()
        self.assertEqual(requests, ["proc_means_vertical"])
        gallery.close()
        self.application.processEvents()

    def test_missing_preview_uses_placeholder_and_existing_preview_loads(self) -> None:
        from clinical_data_viewer.ui.table_shell_gallery import TableShellGalleryDialog

        gallery = TableShellGalleryDialog()
        self.assertIsNotNone(gallery._cards[0].preview.pixmap())
        self.assertIsNotNone(gallery._cards[1].preview.pixmap())
        assert gallery._cards[0].preview.pixmap() is not None
        assert gallery._cards[1].preview.pixmap() is not None
        self.assertFalse(gallery._cards[0].preview.pixmap().isNull())
        self.assertFalse(gallery._cards[1].preview.pixmap().isNull())
        self.assertTrue(gallery._cards[0].shell.preview_path.endswith("proc_means_vertical.png"))
        gallery.close()
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()

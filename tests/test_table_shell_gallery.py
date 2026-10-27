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
                "baseline_characteristics",
                "change_from_baseline",
                "categorical",
                "rule_based",
                "ae_table",
            ),
        )
        gallery.search.setText("baseline")
        self.assertEqual(
            gallery.visible_shell_keys(),
            ("baseline_characteristics", "change_from_baseline"),
        )
        gallery.search.clear()
        gallery.category_buttons["Safety"].click()
        self.assertEqual(gallery.visible_shell_keys(), ("ae_table",))
        gallery.category_buttons["Categorical"].click()
        self.assertEqual(
            gallery.visible_shell_keys(),
            ("baseline_characteristics", "categorical", "rule_based", "ae_table"),
        )
        gallery.category_buttons["Continuous"].click()
        self.assertEqual(
            gallery.visible_shell_keys(),
            (
                "proc_means_vertical",
                "baseline_characteristics",
                "change_from_baseline",
            ),
        )
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
        open_button = next(
            button for button in buttons if button.text() == "Open Builder"
        )
        open_button.click()
        self.assertEqual(requests, ["proc_means_vertical"])
        gallery.close()
        self.application.processEvents()

    def test_baseline_characteristics_shell_preview_opens_its_builder(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton

        from clinical_data_viewer.ui.table_shell_gallery import (
            TableShellGalleryDialog,
        )

        gallery = TableShellGalleryDialog()
        requests: list[str] = []
        gallery.builder_requested.connect(requests.append)
        card = next(
            card
            for card in gallery._cards
            if card.shell.key == "baseline_characteristics"
        )
        self.assertTrue(
            card.shell.preview_path.endswith("baseline_characteristics.png")
        )
        QTest.mouseClick(card, Qt.LeftButton)
        self.assertIsNotNone(gallery._preview_dialog)
        assert gallery._preview_dialog is not None
        self.assertFalse(gallery._preview_dialog._source_pixmap.isNull())
        open_button = next(
            button
            for button in gallery._preview_dialog.findChildren(QPushButton)
            if button.text() == "Open Builder"
        )
        open_button.click()
        self.assertEqual(requests, ["baseline_characteristics"])
        gallery.close()
        self.application.processEvents()

    def test_preview_fits_image_to_window_without_replacing_source(self) -> None:
        from clinical_data_viewer.ui.table_shell_gallery import (
            TABLE_SHELLS,
            _shell_pixmap,
            _ShellPreviewDialog,
        )

        preview = _ShellPreviewDialog(TABLE_SHELLS[0])
        source_size = _shell_pixmap(TABLE_SHELLS[0]).size()
        self.assertLess(source_size.height(), 1233)
        preview.resize(420, 320)
        preview.show()
        self.application.processEvents()

        small_display = preview._image.pixmap()
        self.assertIsNotNone(small_display)
        assert small_display is not None
        self.assertLessEqual(small_display.width(), preview._scroll.viewport().width())
        self.assertLessEqual(
            small_display.height(), preview._scroll.viewport().height()
        )
        self.assertEqual(preview._source_pixmap.size(), source_size)

        small_size = small_display.size()
        preview.resize(1200, 900)
        self.application.processEvents()
        large_display = preview._image.pixmap()
        self.assertIsNotNone(large_display)
        assert large_display is not None
        self.assertGreater(large_display.width(), small_size.width())
        self.assertGreater(large_display.height(), small_size.height())
        self.assertEqual(preview._source_pixmap.size(), source_size)
        preview.close()
        self.application.processEvents()

    def test_vertical_preview_navigates_between_shell_pages(self) -> None:
        from clinical_data_viewer.ui.table_shell_gallery import (
            TABLE_SHELLS,
            _ShellPreviewDialog,
        )

        preview = _ShellPreviewDialog(TABLE_SHELLS[0])
        self.assertEqual(len(preview._source_pixmaps), 2)
        self.assertEqual(preview._page_label.text(), "Page 1 of 2")
        self.assertFalse(preview._previous_button.isEnabled())
        self.assertTrue(preview._next_button.isEnabled())

        first_size = preview._source_pixmap.size()
        preview._next_button.click()
        self.assertEqual(preview._page_label.text(), "Page 2 of 2")
        self.assertTrue(preview._previous_button.isEnabled())
        self.assertFalse(preview._next_button.isEnabled())
        self.assertNotEqual(preview._source_pixmap.size(), first_size)

        preview._previous_button.click()
        self.assertEqual(preview._page_label.text(), "Page 1 of 2")
        self.assertFalse(preview._previous_button.isEnabled())
        preview.close()
        preview.deleteLater()
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
        self.assertTrue(
            gallery._cards[0].shell.preview_path.endswith("proc_means_vertical.png")
        )
        gallery.close()
        gallery.deleteLater()
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()

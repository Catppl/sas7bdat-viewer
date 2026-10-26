from __future__ import annotations

import importlib.util
import os
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class AeTableBuilderUiTests(unittest.TestCase):
    def test_compact_width_keeps_buttons_visible_and_clickable(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.ae_table_builder import AeTableBuilder

        application = QApplication.instance() or QApplication([])
        builder = AeTableBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADAE",
                1,
                tuple(
                    VariableMetadata(name)
                    for name in ("USUBJID", "TRT01A", "AEBODSYS", "AEDECOD")
                ),
            ),
            "ADAE.sas7bdat",
        )
        builder.resize(210, 500)
        builder.show()
        application.processEvents()

        scroll = builder.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertGreater(scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
        application.processEvents()

        buttons = {
            button.text(): button
            for button in builder.findChildren(QPushButton)
        }
        for text in ("Clear", "SAS Code Generator…", "Run AE Table"):
            self.assertIn(text, buttons)
            self.assertTrue(buttons[text].isVisible())
            self.assertTrue(buttons[text].isEnabled())
            self.assertEqual(buttons[text].width(), builder.width())

        run_requests = []
        sas_requests = []
        browse_requests = []
        builder.run_requested.connect(run_requests.append)
        builder.sas_code_requested.connect(sas_requests.append)
        builder.browse_adsl_requested.connect(lambda: browse_requests.append(True))
        builder.dataset_filter.setPlainText('TRT01A = "A"')
        buttons["Run AE Table"].click()
        buttons["SAS Code Generator…"].click()
        buttons["Browse…"].click()
        self.assertEqual(len(run_requests), 1)
        self.assertEqual(len(sas_requests), 1)
        self.assertEqual(len(browse_requests), 1)

        buttons["Clear"].click()
        self.assertEqual(builder.current_filter_text(), "")
        builder.close()
        application.processEvents()


if __name__ == "__main__":
    unittest.main()

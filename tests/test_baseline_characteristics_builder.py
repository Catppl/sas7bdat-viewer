from __future__ import annotations

import importlib.util
import os
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class BaselineCharacteristicsBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_role_is_user_selected_while_storage_type_comes_from_metadata(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.baseline_characteristics_builder import (
            BaselineCharacteristicsBuilder,
        )

        builder = BaselineCharacteristicsBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADSL",
                2,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("SEX"),
                    VariableMetadata("AGE", kind="numeric"),
                ),
            ),
            "adsl.sas7bdat",
        )
        errors: list[str] = []
        builder.validation_error.connect(errors.append)
        builder.add_item("categorical", "AGE")
        builder.add_item("continuous", "SEX")
        self.assertEqual(builder.items.item(0, 0).text(), "Categorical")
        self.assertEqual(builder.items.item(0, 2).text(), "Numeric")
        self.assertIn("must use a numeric", errors[-1])
        builder.add_item("continuous", "AGE")
        self.assertIn("already selected", errors[-1])

    def test_clear_resets_items_filter_and_header(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.baseline_characteristics_builder import (
            BaselineCharacteristicsBuilder,
        )

        builder = BaselineCharacteristicsBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADSL", 1, (VariableMetadata("USUBJID"), VariableMetadata("SEX"))
            ),
            "adsl.sas7bdat",
        )
        builder.add_item("categorical", "SEX")
        builder.filter_editor.setText('SEX = "F"')
        builder.header_label.setText("All Subjects")
        builder.clear()
        self.assertEqual(builder.items.rowCount(), 0)
        self.assertEqual(builder.current_filter_text(), "")
        self.assertEqual(builder.header_label.text(), "Overall")


if __name__ == "__main__":
    unittest.main()

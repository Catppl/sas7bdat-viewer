from __future__ import annotations

import importlib.util
import os
import unittest
from types import SimpleNamespace

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class ListingBuilderStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_source_temporarily_unavailable_does_not_clear_listing_inputs(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.listing_builder import ListingBuilder

        metadata = DatasetMetadata(
            "ADAE",
            2,
            (VariableMetadata("USUBJID", "Subject"), VariableMetadata("AETERM")),
        )
        reloaded = DatasetMetadata("ADAE", 2, metadata.variables)
        builder = ListingBuilder()
        builder.set_dataset(metadata, "adae.sas7bdat", 'TRTEMFL = "Y"')
        builder._add_variable()
        builder.data_filter.setPlainText('TRTEMFL = "Y" and AESER = "Y"')

        builder.set_dataset(None, "")
        self.assertEqual(builder.table.rowCount(), 1)
        self.assertEqual(
            builder.data_filter.toPlainText(), 'TRTEMFL = "Y" and AESER = "Y"'
        )
        self.assertFalse(builder.run.isEnabled())

        builder.set_dataset(reloaded, "adae.sas7bdat", 'TRTEMFL = "N"')
        self.assertEqual(builder.table.rowCount(), 1)
        self.assertEqual(
            builder.data_filter.toPlainText(), 'TRTEMFL = "Y" and AESER = "Y"'
        )
        builder.clear()
        self.assertEqual(builder.table.rowCount(), 0)
        self.assertEqual(builder.data_filter.toPlainText(), "")

    def test_adsl_duplicate_detection_is_case_insensitive_and_refreshes_auto_map(self):
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.listing_builder import ListingBuilder

        source_metadata = DatasetMetadata(
            "ADAE",
            1,
            (VariableMetadata("USUBJID"), VariableMetadata("AGE", kind="numeric")),
        )
        adsl_metadata = DatasetMetadata(
            "ADSL",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("AGE", kind="numeric"),
                VariableMetadata("SAFFL"),
            ),
        )
        adsl_tab = SimpleNamespace(handle=SimpleNamespace(metadata=adsl_metadata))
        builder = ListingBuilder()
        builder.set_dataset(source_metadata, "adae.sas7bdat")
        builder.set_adsl_sources([(adsl_tab, "ADSL")])
        builder.merge_enabled.setChecked(True)
        builder.duplicate_policy.setCurrentText("Rename ADSL duplicates")
        builder.keep.setText("age")
        self.assertEqual(builder.rename_map.text(), "AGE=AGE_ADSL")

        builder.keep.setText("SAFFL")
        self.assertEqual(builder.rename_map.text(), "")

        builder.rename_map.setText("AGE=CUSTOM_AGE")
        builder.keep.setText("SAFFL")
        self.assertEqual(builder.rename_map.text(), "AGE=CUSTOM_AGE")
        builder.deleteLater()


if __name__ == "__main__":
    unittest.main()

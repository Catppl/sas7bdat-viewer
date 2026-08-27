from __future__ import annotations

import importlib.util
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class ListingBuilderStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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


if __name__ == "__main__":
    unittest.main()

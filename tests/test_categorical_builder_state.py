from __future__ import annotations

import importlib.util
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class CategoricalBuilderStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_result_lifecycle_preserves_configuration_until_clear(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.categorical_builder import CategoricalBuilder

        metadata = DatasetMetadata(
            "ADAE",
            2,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("RACE"),
            ),
        )
        # A reloaded/source-tab metadata object is intentionally a different
        # object, as it is after a result tab is opened and closed.
        reloaded_metadata = DatasetMetadata("ADAE", 2, metadata.variables)
        builder = CategoricalBuilder()
        builder.set_dataset(metadata, "adae.sas7bdat", 'TRT = "A"')
        builder.items.editor.setText("RACE")
        builder.items._add()
        builder.numerator_where.setPlainText('TRT = "A" and RACE = "WHITE"')
        builder.population_where.setText('SAFFL = "Y"')

        # The active result tab has no source metadata. This must disable the
        # builder without destroying its pending configuration.
        builder.set_dataset(None, "")
        self.assertEqual(builder.current_filter_text(), 'TRT = "A" and RACE = "WHITE"')
        self.assertEqual(tuple(item.variable for item in builder.items.selected_items()), ("RACE",))

        # Returning to the source (including a reload/new metadata object)
        # also preserves the configuration.
        builder.set_dataset(reloaded_metadata, "adae.sas7bdat", 'TRT = "B"')
        self.assertEqual(builder.current_filter_text(), 'TRT = "A" and RACE = "WHITE"')
        self.assertEqual(tuple(item.variable for item in builder.items.selected_items()), ("RACE",))
        self.assertEqual(builder.population_where.text(), 'SAFFL = "Y"')

        builder.clear()
        self.assertEqual(builder.current_filter_text(), "")
        self.assertEqual(builder.items.selected_items(), ())
        self.assertEqual(builder.population_where.text(), "")


if __name__ == "__main__":
    unittest.main()

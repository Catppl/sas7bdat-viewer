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

    def test_source_reload_refreshes_schema_without_overwriting_builder_filter(self) -> None:
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
        reloaded = DatasetMetadata(
            "ADAE",
            2,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("RACE"),
                VariableMetadata("PARAMCD"),
            ),
        )
        builder = CategoricalBuilder()
        builder.set_dataset(metadata, "adae.sas7bdat", 'TRT = "A"')
        builder.numerator_where.setPlainText('TRT = "A" and RACE = "WHITE"')
        builder.set_source_reloading(True)
        self.assertFalse(builder.run_button.isEnabled())
        builder.set_source_reloading(False)
        builder.set_dataset(
            reloaded,
            "adae.sas7bdat",
            'TRT = "B"',
            inherit_filter=False,
        )
        self.assertEqual(
            builder.current_filter_text(), 'TRT = "A" and RACE = "WHITE"'
        )
        self.assertIn(
            "PARAMCD",
            [builder.treatment.itemText(i) for i in range(builder.treatment.count())],
        )
        builder.deleteLater()
        self.application.processEvents()

    def test_reload_prunes_removed_item_and_reports_it(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.categorical_builder import CategoricalBuilder

        metadata = DatasetMetadata(
            "ADAE",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("RACE"),
            ),
        )
        reloaded = DatasetMetadata(
            "ADAE", 1, (VariableMetadata("USUBJID"), VariableMetadata("TRT"))
        )
        builder = CategoricalBuilder()
        builder.set_dataset(metadata, "adae.sas7bdat")
        builder.items.editor.setText("RACE")
        builder.items._add()
        removed = builder.set_dataset(reloaded, "adae.sas7bdat", inherit_filter=False)
        self.assertIn("RACE", removed)
        self.assertEqual(builder.items.selected_items(), ())
        builder.deleteLater()
        self.application.processEvents()

    def test_population_treatment_selector_uses_adsl_schema(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.categorical_builder import CategoricalBuilder

        class Tab:
            def __init__(self) -> None:
                self.handle = type("Handle", (), {})()
                self.handle.metadata = DatasetMetadata(
                    "ADSL",
                    1,
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("TRT01A"),
                        VariableMetadata("SAFFL"),
                    ),
                )

        builder = CategoricalBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADAE",
                1,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRTA"),
                    VariableMetadata("RACE"),
                ),
            ),
            "adae.sas7bdat",
        )
        builder.treatment.setCurrentText("TRTA")
        adsl = Tab()
        builder.set_adsl_sources([(adsl, "ADSL — adsl.sas7bdat")])
        self.assertEqual(builder.population_treatment.currentText(), "TRT01A")
        builder.deleteLater()
        self.application.processEvents()

    def test_sas_codegen_button_tracks_source_kind_and_busy_state(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.categorical_builder import CategoricalBuilder

        metadata = DatasetMetadata(
            "ADAE",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRTA"),
                VariableMetadata("RACE"),
            ),
        )
        builder = CategoricalBuilder()
        builder.set_dataset(metadata, "adae.sas7bdat", source_kind="merge")
        self.assertFalse(builder.sas_code_button.isEnabled())
        self.assertIn("merged Categorical", builder.sas_code_button.toolTip())

        builder.set_dataset(metadata, "adae.sas7bdat", source_kind="sas")
        self.assertTrue(builder.sas_code_button.isEnabled())
        builder.set_busy(True)
        self.assertFalse(builder.sas_code_button.isEnabled())
        builder.set_busy(False)
        self.assertTrue(builder.sas_code_button.isEnabled())
        builder.set_dataset(None, "")
        builder.set_busy(True)
        builder.set_busy(False)
        self.assertFalse(builder.sas_code_button.isEnabled())
        builder.deleteLater()
        self.application.processEvents()

    def test_sas_codegen_emits_the_current_builder_snapshot(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.categorical_builder import CategoricalBuilder

        metadata = DatasetMetadata(
            "ADAE",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRTA"),
                VariableMetadata("RACE"),
                VariableMetadata("AVAL", kind="numeric"),
            ),
        )
        builder = CategoricalBuilder()
        builder.set_dataset(metadata, "adae.sas7bdat", source_kind="sas")
        builder.items.editor.setText("RACE")
        builder.items._add()
        builder.treatment.setCurrentText("TRTA")
        builder.subject.setCurrentText("USUBJID")
        builder.denominator_type.setCurrentIndex(
            builder.denominator_type.findData("nonmissing")
        )
        builder.nonmissing_value.setCurrentText("AVAL")
        builder.numerator_where.setPlainText('TRTA = "A"')
        emitted = []
        builder.sas_code_requested.connect(emitted.append)

        builder.sas_code_button.click()

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].items[0].variable, "RACE")
        self.assertEqual(emitted[0].numerator_filter_text, 'TRTA = "A"')
        self.assertEqual(emitted[0].denominator_type, "nonmissing")
        builder.deleteLater()
        self.application.processEvents()


if __name__ == "__main__":
    unittest.main()

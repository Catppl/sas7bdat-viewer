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

    def test_source_unavailability_does_not_clear_saved_builder_configuration(
        self,
    ) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.baseline_characteristics_builder import (
            BaselineCharacteristicsBuilder,
        )

        metadata = DatasetMetadata(
            "ADSL", 1, (VariableMetadata("USUBJID"), VariableMetadata("SEX"))
        )
        builder = BaselineCharacteristicsBuilder()
        builder.set_dataset(metadata, "adsl.sas7bdat", 'SEX = "F"')
        builder.add_item("categorical", "SEX")
        builder.set_dataset(None, "")
        builder.set_dataset(metadata, "adsl-reloaded.sas7bdat", 'SEX = "M"')
        self.assertEqual(builder.items.rowCount(), 1)
        self.assertEqual(builder.current_filter_text(), 'SEX = "F"')

    def test_population_dataset_and_filter_are_part_of_selection(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.baseline_characteristics_builder import (
            BaselineCharacteristicsBuilder,
        )

        builder = BaselineCharacteristicsBuilder()
        source_metadata = DatasetMetadata(
            "ADLB",
            1,
            (VariableMetadata("USUBJID"), VariableMetadata("SEX")),
        )
        population_metadata = DatasetMetadata(
            "ADSL",
            1,
            (VariableMetadata("USUBJID"), VariableMetadata("TRT01A")),
        )
        builder.set_dataset(source_metadata, "adlb.sas7bdat")
        builder.set_population_dataset(
            population_metadata, "adsl.sas7bdat", 'SAFFL = "Y"'
        )
        builder.population_treatment_variable.setCurrentIndex(
            builder.population_treatment_variable.findData("TRT01A")
        )
        builder.population_filter_editor.setText('SAFFL = "Y"')
        selection = builder.selection()
        self.assertIsNone(selection.population_dataset)
        self.assertEqual(selection.population_treatment_variable, "TRT01A")
        self.assertEqual(selection.population_filter_text, 'SAFFL = "Y"')

    def test_include_total_is_available_only_with_treatment_grouping(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.baseline_characteristics_builder import (
            BaselineCharacteristicsBuilder,
        )

        builder = BaselineCharacteristicsBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADSL",
                1,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRT"),
                    VariableMetadata("SEX"),
                ),
            ),
            "adsl.sas7bdat",
        )
        self.assertFalse(builder.include_total.isEnabled())
        builder.treatment_variable.setCurrentIndex(
            builder.treatment_variable.findData("TRT")
        )
        self.assertTrue(builder.include_total.isEnabled())
        builder.include_total.setChecked(True)
        builder.add_item("categorical", "SEX")
        self.assertTrue(builder.selection().include_total)
        self.assertTrue(builder.sas_code_button.isEnabled())
        builder.treatment_variable.setCurrentIndex(0)
        self.assertFalse(builder.include_total.isEnabled())
        self.assertFalse(builder.include_total.isChecked())


if __name__ == "__main__":
    unittest.main()

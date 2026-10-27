from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.baseline_characteristics import (
    BaselineCategoricalItem,
    BaselineCharacteristicsConfig,
    BaselineContinuousItem,
    build_baseline_characteristics_configuration,
)
from clinical_data_viewer.codegen.sas import SasBaselineCharacteristicsGenerator
from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine


class BaselineCharacteristicsSasCodegenTests(unittest.TestCase):
    def configuration(self):
        variables = (
            VariableMetadata("USUBJID", "Subject"),
            VariableMetadata("SAFFL", "Safety flag"),
            VariableMetadata("SEX", "Sex"),
            VariableMetadata("AGE", "Age", "numeric"),
        )
        root = Path(self.temporary.name)
        source = root / "adsl.sas7bdat"
        source.touch()
        handle = DatasetHandle(
            source,
            root / "copy.sas7bdat",
            root / "dataset.sqlite",
            DatasetMetadata("ADSL", 3, variables),
        )
        filter_text = 'SAFFL = "Y"'
        config = BaselineCharacteristicsConfig(
            (
                BaselineCategoricalItem("SEX", "Sex", "custom", ("F", "M"), 1),
                BaselineContinuousItem("AGE", "Age", "years", (("mean", 1),)),
            ),
            FilterEngine(variables).compile(filter_text),
            filter_text,
        )
        return build_baseline_characteristics_configuration(handle, config)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_renders_filter_subject_count_and_fixed_continuous_contract(self) -> None:
        code = SasBaselineCharacteristicsGenerator().generate(self.configuration())
        folded = code.casefold()
        self.assertIn("where saffl = 'y'", folded)
        self.assertIn("count(distinct", folded)
        self.assertIn("proc means", folded)
        self.assertIn("vardef=df qntldef=5", folded)
        self.assertIn("work.baseline_characteristics_long", folded)
        self.assertIn("work.baseline_characteristics", folded)
        self.assertNotIn("delete baseline_characteristics", folded)

    def test_rejects_merge_and_character_continuous_item(self) -> None:
        configuration = self.configuration()
        merged = copy.deepcopy(configuration)
        merged["input"]["kind"] = "merge"
        with self.assertRaisesRegex(ValueError, "merged"):
            SasBaselineCharacteristicsGenerator().generate(merged)

        invalid = copy.deepcopy(configuration)
        invalid["items"][1]["variable"] = "SEX"
        invalid["items"][1]["storage_type"] = "character"
        with self.assertRaisesRegex(ValueError, "must use a numeric"):
            SasBaselineCharacteristicsGenerator().generate(invalid)


if __name__ == "__main__":
    unittest.main()

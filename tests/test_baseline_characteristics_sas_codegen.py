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
        self.assertIn("if sex='f' then sort_rank=1", folded)
        self.assertIn("maximum observed source precision", folded)
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

    def test_treatment_columns_use_population_n_by_group(self) -> None:
        variables = (
            VariableMetadata("USUBJID", "Subject"),
            VariableMetadata("SAFFL", "Safety flag"),
            VariableMetadata("TRT", "Treatment"),
            VariableMetadata("SEX", "Sex"),
            VariableMetadata("AGE", "Age", "numeric"),
        )
        root = Path(self.temporary.name)
        source_path = root / "adsl.sas7bdat"
        population_path = root / "population.sas7bdat"
        source_path.touch()
        population_path.touch()
        source = DatasetHandle(
            source_path,
            root / "source-copy.sas7bdat",
            root / "source.sqlite",
            DatasetMetadata("ADSL", 3, variables),
        )
        population = DatasetHandle(
            population_path,
            root / "population-copy.sas7bdat",
            root / "population.sqlite",
            DatasetMetadata("ADSL", 3, variables),
        )
        filter_text = 'SAFFL = "Y"'
        config = BaselineCharacteristicsConfig(
            (BaselineContinuousItem("AGE", "Age", statistics=("n", "mean")),),
            FilterEngine(variables).compile(filter_text),
            filter_text,
            treatment_variable="TRT",
            population_treatment_variable="TRT",
            population_filter=FilterEngine(variables).compile(filter_text),
            population_filter_text=filter_text,
        )
        configuration = build_baseline_characteristics_configuration(
            source, config, population=population
        )
        configuration["_runtime"] = {"population_levels": ["Placebo", "Drug A"]}
        code = SasBaselineCharacteristicsGenerator().generate(configuration)
        folded = code.casefold()
        self.assertIn("into :n1 trimmed", folded)
        self.assertIn("into :n2 trimmed", folded)
        self.assertIn("count(distinct usubjid)", folded)
        self.assertIn("into :nt trimmed", folded)
        self.assertIn("%do _i=1 %to &_bc_ntrt", folded)
        self.assertIn("as trt_&_i", folded)
        self.assertIn("population n follows the same n1, n2", folded)
        self.assertIn("big_n=&n1", folded)
        self.assertIn("big_n=&n2", folded)
        self.assertIn("from analysis.adsl", folded)
        self.assertNotIn("_bc_population", folded)
        self.assertNotIn("treatment columns is not available", folded)

    def test_population_dataset_is_used_for_header_n(self) -> None:
        variables = (
            VariableMetadata("USUBJID", "Subject"),
            VariableMetadata("SAFFL", "Safety flag"),
            VariableMetadata("SEX", "Sex"),
            VariableMetadata("AGE", "Age", "numeric"),
        )
        root = Path(self.temporary.name)
        population_path = root / "population.sas7bdat"
        population_path.touch()
        population = DatasetHandle(
            population_path,
            root / "population-copy.sas7bdat",
            root / "population.sqlite",
            DatasetMetadata("ADSL", 3, variables),
        )
        source = root / "adlb.sas7bdat"
        source.touch()
        source_handle = DatasetHandle(
            source,
            root / "adlb-copy.sas7bdat",
            root / "dataset.sqlite",
            DatasetMetadata("ADLB", 3, variables),
        )
        filter_text = 'SAFFL = "Y"'
        config = BaselineCharacteristicsConfig(
            (BaselineCategoricalItem("SEX", "Sex"),),
            FilterEngine(variables).compile(filter_text),
            filter_text,
            population_filter=FilterEngine(variables).compile(filter_text),
            population_filter_text=filter_text,
        )
        configuration = build_baseline_characteristics_configuration(
            source_handle, config, population=population
        )
        code = SasBaselineCharacteristicsGenerator().generate(configuration)
        self.assertIn("from analysis.adsl where saffl = 'y';", code.casefold())
        self.assertNotIn("_bc_population", code.casefold())


if __name__ == "__main__":
    unittest.main()

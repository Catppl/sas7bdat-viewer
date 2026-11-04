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
        self.assertNotIn("\n\n\n", code)
        self.assertIn("where saffl = 'y'", folded)
        self.assertIn("count(distinct", folded)
        self.assertIn("proc means", folded)
        self.assertIn("put(n,best.)", folded)
        self.assertIn("vardef=df qntldef=5", folded)
        self.assertIn("if sex='f' then sort_rank=1", folded)
        self.assertIn("if ^missing(sex);", folded)
        self.assertNotIn("if missing(sex) then", folded)
        self.assertNotIn("vvalue(", folded)
        self.assertNotIn("putn(", folded)
        self.assertIn("put(raw_value_1,8.&mean_decimals.)", folded)
        self.assertIn("select usubjid into :duplicate_subject_ids separated by ', '", folded)
        self.assertIn("duplicate usubjid values: &duplicate_subject_ids.", folded)
        self.assertIn("missing usubjid at source observation", folded)
        for legacy_length in ("$12", "$16", "$32", "$80", "$256", "$320", "$400"):
            self.assertNotIn(legacy_length, folded)
        self.assertNotIn("10**(-", folded)
        self.assertIn('if missing(raw_value_1) then row_value="-";', folded)
        self.assertNotIn("display_value", folded)
        self.assertNotIn("ifc(missing(raw_value_1)", folded)
        self.assertNotIn("  _value_1=", folded)
        self.assertIn("cnt1", folded)
        self.assertNotIn("then delete;", folded)
        self.assertNotIn("libname ", folded)
        self.assertIn("work.baseline_characteristics_long", folded)
        self.assertIn("work.baseline_characteristics", folded)
        self.assertNotIn("delete baseline_characteristics", folded)
        self.assertNotIn("proc append", folded)
        self.assertNotIn("stop;", folded)
        self.assertIn(
            "set\n    work.adsl_item_1\n    work.adsl_item_2",
            folded,
        )

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
            (
                BaselineContinuousItem(
                    "AGE",
                    "Age",
                    statistic_decimals=(("mean", 1),),
                    statistics=("n", "mean"),
                ),
            ),
            FilterEngine(variables).compile(filter_text),
            filter_text,
            treatment_variable="TRT",
            include_total=True,
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
        self.assertNotIn("\n\n\n", code)
        self.assertIn("into :n1 trimmed", folded)
        self.assertIn("into :n2 trimmed", folded)
        self.assertIn("count(distinct usubjid)", folded)
        self.assertIn("into :nt trimmed", folded)
        self.assertIn('trt_1="placebo (n=&n1)"', folded)
        self.assertIn('trt_2="drug a (n=&n2)"', folded)
        self.assertIn('trt_3="total (n=&nt)"', folded)
        self.assertIn("where saffl = 'y' and ^missing(trt);", folded)
        self.assertNotIn("if ^missing(trt);", folded)
        self.assertIn("noprint nway missing", folded)
        self.assertIn("n=n", folded)
        self.assertIn("%macro baseline_continuous(", folded)
        self.assertIn("%baseline_continuous(", folded)
        self.assertIn("include_total=1", folded)
        self.assertIn("select usubjid into :duplicate_subject_ids separated by ', '", folded)
        self.assertIn("duplicate usubjid values: &duplicate_subject_ids.", folded)
        for legacy_length in ("$12", "$16", "$32", "$80", "$256", "$320", "$400"):
            self.assertNotIn(legacy_length, folded)
        self.assertIn("data _null_;", folded)
        self.assertNotIn("vvalue(", folded)
        self.assertNotIn("putn(", folded)
        self.assertIn("put(mean,8.&mean_decimals.)", folded)
        self.assertNotIn("_bc_", folded)
        self.assertIn("big_n=&n1", folded)
        self.assertIn("big_n=&n2", folded)
        self.assertIn('"total"', folded)
        self.assertIn("&work_prefix._stat_total_&token.", folded)
        self.assertIn("&work_prefix._stat_all_&token.", folded)
        self.assertIn('"total"', folded)
        self.assertNotIn("proc append", folded)
        self.assertNotIn("stop;", folded)
        self.assertIn(
            "set\n    work.adsl_item_1",
            folded,
        )
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

    def test_rejects_xpt_source(self) -> None:
        configuration = self.configuration()
        configuration["input"]["format"] = "xpt"
        with self.assertRaisesRegex(ValueError, "requires a sas7bdat source"):
            SasBaselineCharacteristicsGenerator().generate(configuration)

    def test_numeric_categorical_item_uses_put_without_vvalue(self) -> None:
        variables = (
            VariableMetadata("USUBJID", "Subject"),
            VariableMetadata("SAFFL", "Safety flag"),
            VariableMetadata("GRADE", "Grade", "numeric"),
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
            (BaselineCategoricalItem("GRADE", "Grade"),),
            FilterEngine(variables).compile(filter_text),
            filter_text,
        )
        code = SasBaselineCharacteristicsGenerator().generate(
            build_baseline_characteristics_configuration(handle, config)
        )
        folded = code.casefold()
        self.assertIn("level_value=strip(put(grade,best32.));", folded)
        self.assertNotIn("vvalue(", folded)

    def test_include_missing_item_uses_missing_branch(self) -> None:
        variables = (
            VariableMetadata("USUBJID", "Subject"),
            VariableMetadata("SAFFL", "Safety flag"),
            VariableMetadata("SEX", "Sex"),
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
            (BaselineCategoricalItem("SEX", "Sex", include_missing_level=True),),
            FilterEngine(variables).compile(filter_text),
            filter_text,
        )
        code = SasBaselineCharacteristicsGenerator().generate(
            build_baseline_characteristics_configuration(handle, config)
        )
        folded = code.casefold()
        self.assertIn('if missing(sex) then level_value="missing";', folded)
        self.assertNotIn("if ^missing(sex);", folded)

    def test_grouped_custom_levels_emit_user_labels(self) -> None:
        variables = (
            VariableMetadata("USUBJID", "Subject"),
            VariableMetadata("SAFFL", "Safety flag"),
            VariableMetadata("TRT", "Treatment"),
            VariableMetadata("SEX", "Sex"),
        )
        root = Path(self.temporary.name)
        source_path = root / "adsl.sas7bdat"
        source_path.touch()
        source = DatasetHandle(
            source_path,
            root / "copy.sas7bdat",
            root / "dataset.sqlite",
            DatasetMetadata("ADSL", 3, variables),
        )
        filter_text = 'SAFFL = "Y"'
        config = BaselineCharacteristicsConfig(
            (
                BaselineCategoricalItem(
                    "SEX",
                    "Sex",
                    "custom",
                    ("F", "M", None),
                    1,
                    (("F", "Female"), ("M", "Male"), (None, "Missing")),
                    True,
                ),
            ),
            FilterEngine(variables).compile(filter_text),
            filter_text,
            treatment_variable="TRT",
            include_total=True,
            population_treatment_variable="TRT",
        )
        configuration = build_baseline_characteristics_configuration(source, config)
        configuration["_runtime"] = {"population_levels": ["Placebo", "Drug A"]}

        code = SasBaselineCharacteristicsGenerator().generate(configuration)

        self.assertIn("display_level=level_value;", code)
        self.assertIn("if level_value='F' then display_level='Female';", code)
        self.assertIn("if level_value='M' then display_level='Male';", code)
        self.assertIn(
            'if level_value="Missing" then display_level=\'Missing\';',
            code,
        )


if __name__ == "__main__":
    unittest.main()

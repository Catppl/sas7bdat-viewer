from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.categorical import (
    CategoricalConfig,
    CategoricalItem,
    DenominatorConfig,
)
from clinical_data_viewer.dataset_utils import is_analysis_dataset
from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.merge_datasets import (
    MergeDatasetsConfig,
    MergeDatasetsEngine,
    MergeSortItem,
)
from clinical_data_viewer.proc_means import ProcMeansConfig, ProcMeansEngine
from clinical_data_viewer.rule_based import RuleBasedConfig, RuleBasedRow
from clinical_data_viewer.temp_manager import TempManager


def _write_source(path: Path, variables, rows) -> None:
    connection = sqlite3.connect(path)
    definitions = ", ".join(
        f'"{name}" {"REAL" if kind == "numeric" else "TEXT"}'
        for name, kind in variables
    )
    columns = ", ".join(f'"{name}"' for name, _kind in variables)
    connection.execute(
        f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {definitions})"
    )
    placeholders = ", ".join("?" for _ in variables)
    connection.executemany(
        f"INSERT INTO dataset (_source_row, {columns}) VALUES (?, {placeholders})",
        ((index, *row) for index, row in enumerate(rows, 1)),
    )
    connection.execute(
        "CREATE TABLE cache_info (cached_rows INTEGER, total_rows INTEGER, complete INTEGER)"
    )
    connection.execute(
        "INSERT INTO cache_info VALUES (?, ?, 1)", (len(rows), len(rows))
    )
    connection.commit()
    connection.close()


def _handle(root: Path, name: str, variables, rows) -> DatasetHandle:
    database = root / f"{name}.sqlite"
    _write_source(database, variables, rows)
    metadata = DatasetMetadata(
        name,
        len(rows),
        tuple(VariableMetadata(variable, kind=kind) for variable, kind in variables),
    )
    source = root / f"{name}.sas7bdat"
    return DatasetHandle(source, source, database, metadata, len(rows), True)


class MergeDatasetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.temp_manager = TempManager(self.root / "session")
        variables = (("USUBJID", "character"), ("AGE", "numeric"))
        self.left = _handle(
            self.root,
            "left",
            variables,
            (("A", 10), ("B", 20), ("C", 30)),
        )
        right_variables = (
            ("USUBJID", "character"),
            ("AGE", "numeric"),
            ("TRT", "character"),
        )
        self.right = _handle(
            self.root,
            "right",
            right_variables,
            (("A", 11, "X"), ("B", 22, "Y"), ("D", 44, "Z")),
        )
        self.engine = MergeDatasetsEngine(self.temp_manager)

    def tearDown(self) -> None:
        self.temp_manager.cleanup()
        self.temp_directory.cleanup()

    def _rows(self, result):
        connection = sqlite3.connect(result.handle.database_path)
        try:
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(dataset)")
            ]
            return columns, connection.execute(
                "SELECT * FROM dataset ORDER BY _source_row"
            ).fetchall()
        finally:
            connection.close()

    def test_left_right_inner_and_full_join_statuses(self) -> None:
        expected = {
            "left": (3, 2, 1, 0),
            "right": (3, 2, 0, 1),
            "inner": (2, 2, 0, 0),
            "full": (4, 2, 1, 1),
        }
        for join_type, (row_count, matched, left_only, right_only) in expected.items():
            result = self.engine.run(
                self.left,
                self.right,
                MergeDatasetsConfig(("USUBJID",), join_type),
            )
            self.assertEqual(result.summary.merged_rows, row_count)
            self.assertEqual(result.summary.matched_rows, matched)
            self.assertEqual(result.summary.left_only_rows, left_only)
            self.assertEqual(result.summary.right_only_rows, right_only)
            columns, rows = self._rows(result)
            self.assertIn("_MERGE_STATUS", columns)
            self.assertIn("_LEFT_SOURCE_ROW", columns)
            self.assertIn("_RIGHT_SOURCE_ROW", columns)
            self.assertEqual(len(rows), row_count)

    def test_duplicate_non_by_columns_get_stable_right_suffix(self) -> None:
        result = self.engine.run(
            self.left,
            self.right,
            MergeDatasetsConfig(("USUBJID",), "left"),
        )
        columns, _rows = self._rows(result)
        self.assertIn("AGE", columns)
        self.assertIn("AGE_RIGHT", columns)
        self.assertEqual(len({column.casefold() for column in columns}), len(columns))

    def test_many_to_many_is_detected_without_changing_sources(self) -> None:
        variables = (("USUBJID", "character"), ("VALUE", "numeric"))
        left = _handle(self.root, "many_left", variables, (("A", 1), ("A", 2)))
        right = _handle(
            self.root, "many_right", variables, (("A", 3), ("A", 4), ("A", 5))
        )
        config = MergeDatasetsConfig(("USUBJID",), "left")
        summary = self.engine.inspect(left, right, config)
        self.assertTrue(summary.many_to_many)
        self.assertEqual(summary.left_duplicate_keys, 1)
        self.assertEqual(summary.right_duplicate_keys, 1)
        result = self.engine.run(left, right, config)
        self.assertEqual(result.summary.merged_rows, 6)
        for handle, expected in ((left, 2), (right, 3)):
            connection = sqlite3.connect(handle.database_path)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM dataset").fetchone()[0],
                expected,
            )
            connection.close()

    def test_missing_character_keys_do_not_match(self) -> None:
        variables = (("KEY", "character"), ("VALUE", "numeric"))
        left = _handle(
            self.root, "missing_left", variables, ((None, 1), ("", 2), ("A", 3))
        )
        right = _handle(
            self.root, "missing_right", variables, ((None, 4), ("", 5), ("A", 6))
        )
        result = self.engine.run(left, right, MergeDatasetsConfig(("KEY",), "full"))
        self.assertEqual(result.summary.matched_rows, 1)
        self.assertEqual(result.summary.merged_rows, 5)

    def test_multi_key_and_type_mismatch_validation(self) -> None:
        variables = (
            ("USUBJID", "character"),
            ("PARAMCD", "character"),
            ("VALUE", "numeric"),
        )
        left = _handle(
            self.root, "multi_left", variables, (("A", "ALT", 1), ("A", "AST", 2))
        )
        right = _handle(
            self.root, "multi_right", variables, (("A", "ALT", 3), ("A", "OTHER", 4))
        )
        result = self.engine.run(
            left, right, MergeDatasetsConfig(("USUBJID", "PARAMCD"), "inner")
        )
        self.assertEqual(result.summary.matched_rows, 1)
        mismatch = _handle(
            self.root,
            "mismatch",
            (("USUBJID", "numeric"), ("VALUE", "numeric")),
            ((1, 2),),
        )
        with self.assertRaisesRegex(ValueError, "incompatible types"):
            self.engine.validate(self.left, mismatch, MergeDatasetsConfig(("USUBJID",)))

    def test_user_sort_and_stable_tie_breaker(self) -> None:
        result = self.engine.run(
            self.left,
            self.right,
            MergeDatasetsConfig(
                ("USUBJID",),
                "left",
                (MergeSortItem("AGE", "DESC"),),
            ),
        )
        _columns, rows = self._rows(result)
        age_index = _columns.index("AGE")
        left_row_index = _columns.index("_LEFT_SOURCE_ROW")
        ages = [row[age_index] for row in rows]
        self.assertEqual(ages, sorted(ages, reverse=True))
        self.assertEqual(
            [row[left_row_index] for row in rows],
            [3, 2, 1],
        )

    def test_default_order_is_stable_for_all_join_types_and_many_to_many(self) -> None:
        expected = {
            "left": [(1, 1), (2, 2), (3, None)],
            "right": [(1, 1), (2, 2), (None, 3)],
            "inner": [(1, 1), (2, 2)],
            "full": [(1, 1), (2, 2), (3, None), (None, 3)],
        }
        for join_type, lineage in expected.items():
            result = self.engine.run(
                self.left,
                self.right,
                MergeDatasetsConfig(("USUBJID",), join_type),
            )
            columns, rows = self._rows(result)
            pairs = [
                (
                    row[columns.index("_LEFT_SOURCE_ROW")],
                    row[columns.index("_RIGHT_SOURCE_ROW")],
                )
                for row in rows
            ]
            self.assertEqual(pairs, [(float(a) if a else None, float(b) if b else None)
                                     for a, b in lineage])

        variables = (("USUBJID", "character"), ("VALUE", "numeric"))
        many_left = _handle(self.root, "order_many_left", variables, (("A", 1), ("A", 2)))
        many_right = _handle(self.root, "order_many_right", variables, (("A", 3), ("A", 4)))
        result = self.engine.run(
            many_left,
            many_right,
            MergeDatasetsConfig(("USUBJID",), "inner"),
        )
        columns, rows = self._rows(result)
        self.assertEqual(
            [
                (row[columns.index("_LEFT_SOURCE_ROW")], row[columns.index("_RIGHT_SOURCE_ROW")])
                for row in rows
            ],
            [(1.0, 1.0), (1.0, 2.0), (2.0, 1.0), (2.0, 2.0)],
        )

    def test_multiple_sort_variables_keep_priority(self) -> None:
        variables = (("USUBJID", "character"), ("VALUE", "numeric"))
        left = _handle(self.root, "priority_left", variables, (("A", 1), ("B", 1), ("C", 2)))
        right = _handle(self.root, "priority_right", variables, (("A", 1), ("B", 1), ("C", 2)))
        result = self.engine.run(
            left,
            right,
            MergeDatasetsConfig(
                ("USUBJID",),
                "inner",
                (MergeSortItem("VALUE", "DESC"), MergeSortItem("USUBJID", "ASC")),
            ),
        )
        columns, rows = self._rows(result)
        self.assertEqual(
            [row[columns.index("USUBJID")] for row in rows], ["C", "A", "B"]
        )

    def test_sort_validation_and_old_config_compatibility(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate sort variable"):
            MergeDatasetsConfig(
                ("USUBJID",),
                sort_by=(MergeSortItem("AGE"), MergeSortItem("age", "DESC")),
            ).validate()
        with self.assertRaisesRegex(ValueError, "Sort direction"):
            MergeDatasetsConfig(
                ("USUBJID",), sort_by=(MergeSortItem("AGE", "DOWN"),)
            ).validate()
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.engine.run(
                self.left,
                self.right,
                MergeDatasetsConfig(
                    ("USUBJID",), sort_by=(MergeSortItem("NOT_A_COLUMN"),)
                ),
            )
        # A config created before sort_by existed remains valid and uses the
        # stable source-row order.
        result = self.engine.run(
            self.left, self.right, MergeDatasetsConfig(("USUBJID",), "left")
        )
        self.assertEqual(result.summary.merged_rows, 3)

    def test_merge_result_is_analysis_source_and_can_be_merged_again(self) -> None:
        first = self.engine.run(
            self.left, self.right, MergeDatasetsConfig(("USUBJID",), "left")
        )
        self.assertEqual(first.handle.kind, "merge")
        self.assertTrue(is_analysis_dataset(first.handle))
        means = ProcMeansEngine(self.temp_manager).run(
            first.handle,
            ProcMeansConfig(("AGE",), statistics=("mean",)),
        )
        self.assertEqual(means.kind, "proc_means")
        RuleBasedConfig(
            (RuleBasedRow("r1", "TRT"),),
            "TRT",
        ).validate(first.handle.metadata)
        CategoricalConfig(
            (CategoricalItem("TRT"),),
            "TRT",
            "USUBJID",
            count_type="record",
            denominator=DenominatorConfig(
                type="nonmissing", analysis_value_variable="AGE"
            ),
        ).validate(first.handle.metadata)
        third = _handle(
            self.root,
            "third",
            (("USUBJID", "character"), ("FLAG", "character")),
            (("A", "Y"), ("D", "N")),
        )
        second = self.engine.run(
            first.handle,
            third,
            MergeDatasetsConfig(("USUBJID",), "left"),
        )
        self.assertEqual(second.handle.kind, "merge")
        self.assertEqual(second.summary.matched_rows, 1)

    def test_non_analysis_result_kinds_are_excluded_by_predicate(self) -> None:
        self.assertTrue(is_analysis_dataset("sas"))
        self.assertTrue(is_analysis_dataset("merge"))
        for kind in ("compare", "proc_means", "categorical", "rule_based", "query"):
            self.assertFalse(is_analysis_dataset(kind))


if __name__ == "__main__":
    unittest.main()

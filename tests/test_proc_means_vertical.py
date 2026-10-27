from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.proc_means_vertical import (
    ProcMeansVerticalConfig,
    ProcMeansVerticalEngine,
)
from clinical_data_viewer.proc_means_vertical.drilldown import (
    build_cell_filter,
    build_cell_where_text,
    lookup_cell,
)
from clinical_data_viewer.proc_means_vertical.engine import _statistic_rows
from clinical_data_viewer.temp_manager import TempManager

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


def make_vertical_handle(root: Path, name: str, rows) -> DatasetHandle:
    variables = (
        VariableMetadata("USUBJID"),
        VariableMetadata("TRT01A"),
        VariableMetadata("AVAL", kind="numeric"),
        VariableMetadata("CHG", kind="numeric"),
    )
    directory = root / name.casefold()
    directory.mkdir()
    database = directory / "dataset.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, USUBJID TEXT, TRT01A TEXT, AVAL REAL, CHG REAL)"
        )
        connection.executemany(
            "INSERT INTO dataset (USUBJID, TRT01A, AVAL, CHG) VALUES (?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    source_path = root / f"{name.casefold()}.sas7bdat"
    source_path.touch()
    return DatasetHandle(
        source_path,
        directory / source_path.name,
        database,
        DatasetMetadata(name, len(rows), variables),
        len(rows),
        True,
    )


def make_vertical_rows_handle(root: Path, name: str, rows) -> DatasetHandle:
    variables = (
        VariableMetadata("USUBJID"),
        VariableMetadata("PARAMCD"),
        VariableMetadata("AVISIT"),
        VariableMetadata("TRT01A"),
        VariableMetadata("AVAL", kind="numeric"),
    )
    directory = root / name.casefold()
    directory.mkdir()
    database = directory / "dataset.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE dataset ("
            "_source_row INTEGER PRIMARY KEY, USUBJID TEXT, PARAMCD TEXT, "
            "AVISIT TEXT, TRT01A TEXT, AVAL REAL)"
        )
        connection.executemany(
            "INSERT INTO dataset (USUBJID, PARAMCD, AVISIT, TRT01A, AVAL) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    source_path = root / f"{name.casefold()}.sas7bdat"
    source_path.touch()
    return DatasetHandle(
        source_path,
        directory / source_path.name,
        database,
        DatasetMetadata(name, len(rows), variables),
        len(rows),
        True,
    )


class ProcMeansVerticalTests(unittest.TestCase):
    def test_groups_as_rows_materialize_context_as_result_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(
                root,
                "ADLB",
                (
                    ("S1", "Baseline", 1.0, 1.0),
                    ("S2", "Baseline", 2.0, 2.0),
                    ("S3", "Week 1", 3.0, 3.0),
                ),
            )
            config = ProcMeansVerticalConfig(
                analysis_variables=("AVAL",),
                row_group_variables=("TRT01A",),
                statistics=("n", "mean"),
                output_layout="rows",
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "GROUP", "ITEM", "STATISTIC", "TOTAL" '
                    "FROM dataset ORDER BY _source_row"
                ).fetchall()
            self.assertEqual(
                rows,
                [
                    ("Baseline", "AVAL", "n", "2"),
                    ("Baseline", "AVAL", "Mean", "2"),
                    ("Week 1", "AVAL", "n", "1"),
                    ("Week 1", "AVAL", "Mean", "3"),
                ],
            )
            cell = lookup_cell(result, 1, "TOTAL")
            self.assertIsNotNone(cell)
            assert cell is not None
            self.assertEqual(cell.group_values, ("Baseline",))
            self.assertIn(
                'TRT01A = "Baseline"',
                build_cell_where_text(source.metadata, config, cell),
            )
            assert result.configuration_path is not None
            configuration = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual(configuration["display"]["layout"], "rows")
            self.assertEqual(configuration["variables"]["row_group"], ["TRT01A"])

    def test_rows_layout_keeps_group_values_as_result_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_rows_handle(
                root,
                "ADLB",
                (
                    ("S1", "ALB", "Baseline", "A", 1.0),
                    ("S2", "ALB", "Baseline", "A", 3.0),
                    ("S3", "ALB", "Week 1", "B", 4.0),
                    ("S4", "ALB", "Week 1", "A", 5.0),
                ),
            )
            config = ProcMeansVerticalConfig(
                analysis_variables=("AVAL",),
                group_variables=("TRT01A",),
                treatment_variable="TRT01A",
                row_group_variables=("PARAMCD", "AVISIT"),
                statistics=("n", "mean"),
                output_layout="rows",
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "GROUP", "ITEM", "STATISTIC", "COL1", "COL2", "TOTAL" '
                    "FROM dataset ORDER BY _source_row"
                ).fetchall()
            self.assertEqual(
                rows,
                [
                    ("ALB / Baseline", "AVAL", "n", "2", "0", "2"),
                    ("ALB / Baseline", "AVAL", "Mean", "2", "—", "2"),
                    ("ALB / Week 1", "AVAL", "n", "1", "1", "2"),
                    ("ALB / Week 1", "AVAL", "Mean", "5", "4", "5"),
                ],
            )

    def test_result_cells_map_to_group_and_total_source_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(
                root,
                "ADLB",
                (
                    ("S1", "A", 1.0, 1.0),
                    ("S2", "A", 2.0, 2.0),
                    ("S3", "B", 3.0, 3.0),
                ),
            )
            config = ProcMeansVerticalConfig(
                analysis_variables=("AVAL",),
                group_variables=("TRT01A",),
                treatment_variable="TRT01A",
                population_treatment_variable="TRT01A",
                statistics=("mean",),
                compiled_filter=FilterEngine(source.metadata.variables).compile(
                    "AVAL >= 2"
                ),
                filter_text="AVAL >= 2",
                population_filter=FilterEngine(source.metadata.variables).compile(""),
                include_total=True,
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                source, config
            )

            self.assertEqual(
                result.metadata.proc_means_vertical_drilldown_columns,
                ("COL1", "COL2", "TOTAL"),
            )
            group_cell = lookup_cell(result, 1, "COL1")
            self.assertIsNotNone(group_cell)
            assert group_cell is not None
            self.assertEqual(group_cell.analysis_variable, "AVAL")
            self.assertEqual(group_cell.statistic_key, "mean")
            self.assertEqual(group_cell.group_values, ("A",))
            compiled = build_cell_filter(source.metadata, config, group_cell)
            where_text = build_cell_where_text(source.metadata, config, group_cell)
            with closing(sqlite3.connect(source.database_path)) as connection:
                subjects = connection.execute(
                    f'SELECT "USUBJID" FROM dataset WHERE {compiled.sql} '
                    "ORDER BY _source_row",
                    compiled.parameters,
                ).fetchall()
            self.assertEqual(subjects, [("S2",)])
            self.assertEqual(
                where_text,
                '(AVAL >= 2) and TRT01A = "A" and not missing(AVAL)',
            )

            total_cell = lookup_cell(result, 1, "TOTAL")
            self.assertIsNotNone(total_cell)
            assert total_cell is not None
            self.assertIsNone(total_cell.group_values)
            total_where = build_cell_where_text(source.metadata, config, total_cell)
            self.assertNotIn("TRT01A", total_where)
            self.assertEqual(total_where, "(AVAL >= 2) and not missing(AVAL)")

    def test_nmiss_cell_drills_to_missing_analysis_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(
                root,
                "ADLB",
                (("S1", "A", None, 1.0), ("S2", "A", 2.0, 2.0)),
            )
            config = ProcMeansVerticalConfig(
                analysis_variables=("AVAL",),
                group_variables=("TRT01A",),
                treatment_variable="TRT01A",
                population_treatment_variable="TRT01A",
                statistics=("nmiss",),
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                source, config
            )
            cell = lookup_cell(result, 1, "COL1")
            self.assertIsNotNone(cell)
            assert cell is not None
            compiled = build_cell_filter(source.metadata, config, cell)
            with closing(sqlite3.connect(source.database_path)) as connection:
                subjects = connection.execute(
                    f'SELECT "USUBJID" FROM dataset WHERE {compiled.sql}',
                    compiled.parameters,
                ).fetchall()
            self.assertEqual(subjects, [("S1",)])
            self.assertIn(
                "missing(AVAL)",
                build_cell_where_text(source.metadata, config, cell),
            )

    def test_vertical_result_has_item_statistic_rows_and_population_n_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(
                root,
                "ADLB",
                (("S1", "A", 1.0, 1.0), ("S2", "A", 2.0, 2.0), ("S3", "B", 3.0, 3.0)),
            )
            population = make_vertical_handle(
                root,
                "ADSL",
                (("S1", "A", None, None), ("S2", "A", None, None), ("S3", "B", None, None)),
            )
            config = ProcMeansVerticalConfig(
                ("AVAL", "CHG"),
                ("TRT01A",),
                "TRT01A",
                "TRT01A",
                ("n", "mean", "q1", "q3", "min", "max"),
                FilterEngine(source.metadata.variables).compile(""),
                "",
                FilterEngine(population.metadata.variables).compile(""),
                "",
                (("AVAL", "Total Cholesterol"), ("CHG", "Change")),
                True,
                (("mean", 1),),
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                source, config, population
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "ITEM", "STATISTIC", "COL1", "COL2", "TOTAL" FROM dataset ORDER BY _source_row'
                ).fetchall()
            self.assertEqual(rows[0], ("Total Cholesterol", "n", "2", "1", "3"))
            self.assertIn(("Total Cholesterol", "Min, Max"), [row[:2] for row in rows])
            labels = {variable.name: variable.label for variable in result.metadata.variables}
            self.assertEqual(labels["COL1"], "A (N=2)")
            self.assertEqual(labels["COL2"], "B (N=1)")
            self.assertEqual(labels["TOTAL"], "Total (N=3)")
            with closing(sqlite3.connect(result.database_path)) as connection:
                long_rows = connection.execute(
                    'SELECT "ITEM", "STATISTIC", "GROUP" FROM proc_means_vertical_long ORDER BY _source_row'
                ).fetchall()
            self.assertEqual(long_rows[0], ("Total Cholesterol", "n", "A"))
            self.assertEqual(long_rows[-1], ("Change", "Min, Max", "Total"))
            assert result.configuration_path is not None
            configuration = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual(configuration["type"], "proc_means_vertical")
            self.assertEqual(configuration["display"]["layout"], "vertical")
            self.assertEqual(configuration["item"]["labels"]["aval"], "Total Cholesterol")

    def test_population_missing_treatment_blocks_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(root, "ADLB", (("S1", "A", 1.0, 1.0),))
            population = make_vertical_handle(root, "ADSL", (("S1", None, None, None),))
            config = ProcMeansVerticalConfig(
                ("AVAL",),
                ("TRT01A",),
                "TRT01A",
                "TRT01A",
            )
            with self.assertRaisesRegex(ValueError, "missing treatment"):
                ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                    source, config, population
                )

    def test_statistics_keep_user_order_when_pair_is_selected_in_reverse(self) -> None:
        self.assertEqual(
            [label for label, _keys in _statistic_rows(("q3", "mean", "q1", "max", "min"))],
            ["Q1, Q3", "Mean", "Min, Max"],
        )

    def test_vertical_requires_usubjid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(root, "ADLB", (("S1", "A", 1.0, 1.0),))
            metadata = DatasetMetadata(
                "NO_SUBJECT", 1, tuple(variable for variable in source.metadata.variables if variable.name != "USUBJID")
            )
            config = ProcMeansVerticalConfig(("AVAL",))
            with self.assertRaisesRegex(ValueError, "USUBJID"):
                config.validate(metadata)

    def test_base_decimal_settings_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(root, "ADLB", (("S1", "A", 1.0, 1.0),))
            config = ProcMeansVerticalConfig(
                ("AVAL",),
                base_decimals=(("AVAL", 1), ("aval", 2)),
            )
            with self.assertRaisesRegex(ValueError, "only once"):
                config.validate(source.metadata)

    def test_rows_layout_requires_row_group_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = make_vertical_handle(
                Path(temporary), "ADLB", (("S1", "A", 1.0, 1.0),)
            )
            config = ProcMeansVerticalConfig(
                ("AVAL",), output_layout="rows"
            )
            with self.assertRaisesRegex(ValueError, "Row Group Variable"):
                config.validate(source.metadata)

    def test_columns_layout_rejects_row_group_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = make_vertical_handle(
                Path(temporary), "ADLB", (("S1", "A", 1.0, 1.0),)
            )
            config = ProcMeansVerticalConfig(
                ("AVAL",), row_group_variables=("TRT01A",)
            )
            with self.assertRaisesRegex(ValueError, "Groups as Rows"):
                config.validate(source.metadata)

    def test_total_subject_n_is_recomputed_across_treatment_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(
                root,
                "ADLB",
                (
                    ("S1", "A", 1.0, 1.0),
                    ("S1", "B", 2.0, 2.0),
                    ("S2", "A", 3.0, 3.0),
                ),
            )
            config = ProcMeansVerticalConfig(
                ("AVAL",),
                ("TRT01A",),
                "TRT01A",
                "TRT01A",
                ("subjects",),
                FilterEngine(source.metadata.variables).compile(""),
                "",
                FilterEngine(source.metadata.variables).compile(""),
                "",
                (),
                True,
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT "COL1", "COL2", "TOTAL" FROM dataset'
                ).fetchone()
            self.assertEqual(row, ("2", "1", "2"))

    def test_base_decimal_override_is_applied_and_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(
                root,
                "ADLB",
                (("S1", "A", 1.234, 1.0), ("S2", "A", 2.345, 2.0)),
            )
            config = ProcMeansVerticalConfig(
                ("AVAL",),
                ("TRT01A",),
                "TRT01A",
                "TRT01A",
                ("mean",),
                FilterEngine(source.metadata.variables).compile(""),
                "",
                FilterEngine(source.metadata.variables).compile(""),
                "",
                (),
                True,
                (("mean", 1),),
                0.95,
                (("AVAL", 1),),
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT "COL1" FROM dataset WHERE "STATISTIC" = "Mean"'
                ).fetchone()
            self.assertEqual(row, ("1.79",))
            assert result.configuration_path is not None
            configuration = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual(configuration["display"]["base_decimals"], {"AVAL": 1})


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class ProcMeansVerticalBuilderUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_scroll_area_and_builder_buttons(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton, QScrollArea

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.proc_means_vertical_builder import (
            ProcMeansVerticalBuilder,
        )

        builder = ProcMeansVerticalBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADLB",
                2,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRT01A"),
                    VariableMetadata("AVAL", kind="numeric"),
                ),
            ),
            "adlb.sas7bdat",
        )
        builder.resize(280, 300)
        builder.show()
        self.application.processEvents()
        scroll = builder.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBarPolicy(), Qt.ScrollBarAsNeeded)

        settings_requests: list[bool] = []
        run_requests: list[object] = []
        sas_requests: list[object] = []
        builder.settings_requested.connect(lambda: settings_requests.append(True))
        builder.run_requested.connect(run_requests.append)
        builder.sas_code_requested.connect(sas_requests.append)
        builder.analysis_variables.set_variables(("AVAL",))
        builder.groups_as_rows.click()
        builder.row_group_variables.set_variables(("TRT01A",))
        row_selection = builder.selection()
        self.assertIsNotNone(row_selection)
        assert row_selection is not None
        self.assertEqual(row_selection.output_layout, "rows")
        self.assertEqual(row_selection.row_group_variables, ("TRT01A",))
        self.assertFalse(builder.sas_code_button.isEnabled())
        builder.groups_as_columns.click()
        self.assertTrue(builder.sas_code_button.isEnabled())
        self.assertEqual(builder.base_decimal_table.rowCount(), 1)
        decimal_combo = builder.base_decimal_table.cellWidget(0, 1)
        self.assertIsNotNone(decimal_combo)
        assert decimal_combo is not None
        decimal_combo.setCurrentIndex(decimal_combo.findData(2))
        selection = builder.selection()
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.base_decimals, (("AVAL", 2),))
        buttons = {button.text(): button for button in builder.findChildren(QPushButton)}
        builder.statistics.setCurrentRow(1)
        buttons["Up"].click()
        self.assertEqual(builder.statistics.currentRow(), 0)
        buttons["Down"].click()
        self.assertEqual(builder.statistics.currentRow(), 1)
        buttons["Settings…"].click()
        buttons["Run"].click()
        buttons["SAS Code Generator…"].click()
        self.assertEqual(len(settings_requests), 1)
        self.assertEqual(len(run_requests), 1)
        self.assertEqual(len(sas_requests), 1)
        builder.set_dataset(
            DatasetMetadata("MERGE", 2, (VariableMetadata("USUBJID"), VariableMetadata("AVAL", kind="numeric"))),
            "merge result",
            source_kind="merge",
        )
        self.assertFalse(builder.sas_code_button.isEnabled())
        buttons["Clear"].click()
        self.assertEqual(builder.analysis_variables.selected_variables(), ())
        self.assertEqual(builder.base_decimal_table.rowCount(), 0)
        builder.close()
        self.application.processEvents()

    def test_result_tab_emits_drilldown_for_value_columns(self) -> None:
        from clinical_data_viewer.data_store import DataStore
        from clinical_data_viewer.filter_engine import CompiledFilter
        from clinical_data_viewer.ui.dataset_tab import DatasetTab

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_vertical_handle(
                root,
                "ADLB",
                (("S1", "A", 1.0, 1.0), ("S2", "B", 2.0, 2.0)),
            )
            result = ProcMeansVerticalEngine(TempManager(root / "temp")).run(
                source,
                ProcMeansVerticalConfig(
                    ("AVAL",),
                    ("TRT01A",),
                    "TRT01A",
                    "TRT01A",
                    ("mean",),
                ),
            )
            tab = DatasetTab(result, 500)
            page = DataStore().query_page(
                result.database_path,
                result.metadata,
                tab.visible_columns,
                CompiledFilter("", ()),
                None,
                0,
                100,
            )
            tab.model.set_page(
                0,
                page.rows,
                page.filtered_count,
                source_rows=page.source_rows,
            )
            requests: list[tuple[int, str, str]] = []
            tab.proc_means_vertical_drilldown_requested.connect(
                lambda row, column, display: requests.append(
                    (row, column, display)
                )
            )
            value_index = tab.model.index(0, tab.visible_columns.index("COL1"))
            tab.table.doubleClicked.emit(value_index)
            self.assertEqual(requests, [(0, "COL1", "1")])
            tab.table._context_index = value_index
            self.assertTrue(
                tab.table._context_variable_is_proc_means_vertical_statistic()
            )
            tab.table._context_index = tab.model.index(
                0, tab.visible_columns.index("ITEM")
            )
            self.assertFalse(
                tab.table._context_variable_is_proc_means_vertical_statistic()
            )
            tab.close()
            self.application.processEvents()



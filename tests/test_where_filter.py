from __future__ import annotations

import unittest

from clinical_data_viewer.codegen.sas.filter_renderer import sas_filter_expression
from clinical_data_viewer.domain import VariableMetadata
from clinical_data_viewer.filter_ast import serialize_filter_ast
from clinical_data_viewer.filter_engine import FilterEngine, WhereValidationError
from clinical_data_viewer.where_parser import WhereSyntaxError, parse_where

VARIABLES = (
    VariableMetadata("USUBJID", kind="character", length=20),
    VariableMetadata("AESER", kind="character", length=1),
    VariableMetadata("AGE", kind="numeric", length=8),
    VariableMetadata("AGE2", kind="numeric", length=8),
    VariableMetadata("AEENDTC", kind="character", length=20),
    VariableMetadata("AESTDTC", kind="character", length=20),
    VariableMetadata("ARMCD", kind="character", length=8),
    VariableMetadata("ADT", kind="numeric", length=8, format="YYMMDD10."),
    VariableMetadata("ADTM", kind="numeric", length=8, format="E8601DT19."),
    VariableMetadata("ATM", kind="numeric", length=8, format="TIME8."),
)


class WhereFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FilterEngine(VARIABLES)

    def test_clinical_example_compiles_to_parameters(self) -> None:
        compiled = self.engine.compile(
            'AESER = "Y" and USUBJID = "101-001" and AGE >= 18'
        )
        self.assertIn('"AESER" = ?', compiled.sql)
        self.assertIn('"USUBJID" = ?', compiled.sql)
        self.assertIn('"AGE" >= ?', compiled.sql)
        self.assertEqual(compiled.parameters, ("Y", "101-001", 18))

    def test_supported_in_contains_missing_not_and_parentheses(self) -> None:
        compiled = self.engine.compile(
            '(USUBJID contains "101" OR AGE IN (18, 65)) AND NOT MISSING(AESER) AND AGE NOT IN (19, 20)'
        )
        self.assertIn("instr", compiled.sql)
        self.assertIn('"AGE" IN (?, ?)', compiled.sql)
        self.assertIn("NOT", compiled.sql)
        self.assertIn('"AGE" NOT IN (?, ?)', compiled.sql)
        self.assertEqual(compiled.parameters, ("101", 18, 65, 19, 20))

    def test_caret_not_equal_and_case_insensitive_variable(self) -> None:
        compiled = self.engine.compile("age ^= 21")
        self.assertEqual(compiled.sql, '"AGE" != ?')
        self.assertEqual(compiled.parameters, (21,))

    def test_missing_uses_character_and_numeric_rules(self) -> None:
        character = self.engine.compile("MISSING(AESER)")
        numeric = self.engine.compile("NOT MISSING(AGE)")
        self.assertEqual(character.sql, '("AESER" IS NULL OR "AESER" = \'\')')
        self.assertEqual(numeric.sql, '(NOT (("AGE" IS NULL)))')

    def test_type_and_variable_errors_are_clear(self) -> None:
        with self.assertRaisesRegex(WhereValidationError, "AGE is numeric"):
            self.engine.compile('AGE = "18"')
        with self.assertRaisesRegex(WhereValidationError, "Unknown variable"):
            self.engine.compile("UNKNOWN = 1")
        with self.assertRaisesRegex(WhereValidationError, "CONTAINS"):
            self.engine.compile('AGE CONTAINS "1"')

    def test_syntax_error_has_line_and_column_and_input_is_not_changed(self) -> None:
        text = 'AESER = "Y"\nAND (AGE >= )'
        with self.assertRaises(WhereSyntaxError) as context:
            parse_where(text)
        self.assertIn("line 2", str(context.exception))
        self.assertEqual(text, 'AESER = "Y"\nAND (AGE >= )')

    def test_empty_where_means_no_filter_in_engine(self) -> None:
        self.assertEqual(self.engine.compile("  \n ").sql, "")

    def test_column_to_column_comparisons_and_sas_logical_symbols(self) -> None:
        compiled = self.engine.compile(
            'AESTDTC <= AEENDTC & AGE GT AGE2 | AESER NE "N"'
        )
        self.assertIn('"AESTDTC" <= "AEENDTC"', compiled.sql)
        self.assertIn('"AGE" > "AGE2"', compiled.sql)
        self.assertIn(" AND ", compiled.sql)
        self.assertIn(" OR ", compiled.sql)
        self.assertEqual(compiled.parameters, ("N",))

    def test_question_contains_between_like_is_missing_and_prefix(self) -> None:
        question = self.engine.compile('ARMCD ? "PKO"')
        self.assertIn("instr", question.sql)
        self.assertEqual(question.parameters, ("PKO",))
        between = self.engine.compile("AGE BETWEEN 18 AND 65")
        self.assertIn('"AGE" BETWEEN ? AND ?', between.sql)
        like = self.engine.compile('USUBJID LIKE "101-%"')
        self.assertIn('"USUBJID" LIKE ?', like.sql)
        missing = self.engine.compile("AESER IS NOT MISSING")
        self.assertIn("NOT", missing.sql)
        prefix = self.engine.compile('USUBJID =: "101"')
        self.assertIn("substr", prefix.sql)

    def test_column_comparison_rejects_incompatible_types(self) -> None:
        with self.assertRaisesRegex(WhereValidationError, "Cannot compare"):
            self.engine.compile("AGE = USUBJID")

    def test_sas_date_datetime_and_time_literals_compile_to_raw_values(self) -> None:
        date = self.engine.compile("ADT = '2026-08-27'd")
        self.assertEqual(date.sql, '"ADT" = ?')
        self.assertEqual(date.parameters, (24_345,))
        date_range = self.engine.compile("ADT BETWEEN '2026-08-01'd AND '2026-08-31'd")
        self.assertEqual(date_range.parameters, (24_319, 24_349))
        datetime = self.engine.compile("ADTM >= '2026-08-27T12:34:56'dt")
        self.assertEqual(datetime.parameters, (24_345 * 86_400 + 45_296,))
        clock = self.engine.compile("ATM = '12:34:56't")
        self.assertEqual(clock.parameters, (45_296,))

        sas_style = self.engine.compile("ADT = '27AUG2026'd")
        self.assertEqual(sas_style.parameters, (24_345,))
        date_list = self.engine.compile("ADT IN ('2026-08-27'd, '2026-08-28'd)")
        self.assertEqual(date_list.parameters, (24_345, 24_346))
        not_date_list = self.engine.compile("ADT NOT IN ('2026-08-27'd)")
        self.assertEqual(not_date_list.parameters, (24_345,))

    def test_sas_temporal_literal_requires_matching_metadata(self) -> None:
        with self.assertRaisesRegex(WhereValidationError, "no supported SAS"):
            self.engine.compile("AGE = '2026-08-27'd")
        with self.assertRaisesRegex(WhereValidationError, "uses a SAS date format"):
            self.engine.compile("ADT = '12:34:56't")
        with self.assertRaisesRegex(WhereValidationError, "numeric variable"):
            self.engine.compile("AESER = '2026-08-27'd")

    def test_index_find_upcase_and_lowcase_compile_and_nest(self) -> None:
        index = self.engine.compile('INDEX(USUBJID, "101") > 0')
        self.assertIn("instr", index.sql)
        self.assertEqual(index.parameters, ("101", 0))
        shorthand = self.engine.compile('FIND(USUBJID, "101")')
        self.assertEqual(shorthand.parameters, ("101", 0))
        nested = self.engine.compile(
            'FIND(LOWCASE(ARMCD), "pko") > 0 AND UPCASE(AESER) = "Y"'
        )
        self.assertIn("lower", nested.sql)
        self.assertIn("upper", nested.sql)
        self.assertEqual(nested.parameters, ("pko", 0, "Y"))
        with self.assertRaisesRegex(WhereValidationError, "character"):
            self.engine.compile('INDEX(AGE, "1") > 0')

    def test_temporal_and_function_validation_errors_are_clear(self) -> None:
        with self.assertRaisesRegex(WhereValidationError, "Invalid SAS date literal"):
            self.engine.compile("ADT = '2026-13-99'd")
        with self.assertRaisesRegex(WhereValidationError, "exactly two arguments"):
            self.engine.compile("INDEX(ARMCD)")
        with self.assertRaisesRegex(WhereValidationError, "Unsupported WHERE function"):
            self.engine.compile('BOGUS(ARMCD) = "PKO"')

    def test_function_and_temporal_literals_serialize_for_future_codegen(self) -> None:
        ast = serialize_filter_ast(
            "ADT = '2026-08-27'd AND INDEX(LOWCASE(ARMCD), \"pko\") > 0",
            VARIABLES,
        )
        self.assertEqual(ast["left"]["operand"]["value_type"], "sas_date")
        self.assertEqual(ast["right"]["left"]["type"], "function")
        self.assertEqual(ast["right"]["left"]["name"], "index")
        rendered = sas_filter_expression(ast)
        self.assertIn("ADT = '2026-08-27'd", rendered)
        self.assertIn("INDEX(LOWCASE(ARMCD), 'pko') > 0", rendered)

    def test_filter_ast_is_language_neutral_and_uses_canonical_variables(self) -> None:
        ast = serialize_filter_ast('aeser = "Y" and AGE between 18 and AGE2', VARIABLES)
        self.assertEqual(ast["type"], "boolean")
        self.assertEqual(ast["operator"], "and")
        self.assertEqual(ast["left"]["variable"], "AESER")
        self.assertEqual(ast["left"]["operand"]["value_type"], "character")
        self.assertEqual(ast["right"]["type"], "between")
        self.assertEqual(ast["right"]["upper"], {"type": "variable", "name": "AGE2"})

    def test_empty_filter_has_null_ast(self) -> None:
        self.assertIsNone(serialize_filter_ast("  ", VARIABLES))


if __name__ == "__main__":
    unittest.main()

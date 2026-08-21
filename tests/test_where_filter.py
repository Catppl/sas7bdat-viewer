from __future__ import annotations

import unittest

from clinical_data_viewer.domain import VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine, WhereValidationError
from clinical_data_viewer.where_parser import WhereSyntaxError, parse_where

VARIABLES = (
    VariableMetadata("USUBJID", kind="character", length=20),
    VariableMetadata("AESER", kind="character", length=1),
    VariableMetadata("AGE", kind="numeric", length=8),
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


if __name__ == "__main__":
    unittest.main()

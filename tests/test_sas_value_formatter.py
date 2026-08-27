from __future__ import annotations

import unittest

from clinical_data_viewer.domain import VariableMetadata
from clinical_data_viewer.sas_value_formatter import (
    SasTemporalLiteral,
    format_sas_value,
    parse_sas_temporal_format,
    sas_temporal_literal_value,
)


class SasValueFormatterTests(unittest.TestCase):
    def test_every_requested_temporal_format_family_is_recognized(self) -> None:
        cases = {
            "DATE9.": ("date", "date", 0),
            "DATE7.": ("date", "date", 0),
            "YYMMDD10.": ("date", "yymmdd", 0),
            "YYMMDDN8.": ("date", "yymmddn", 0),
            "YYMMDDP10.": ("date", "yymmdp", 0),
            "YYMMDDS10.": ("date", "yymmds", 0),
            "MMDDYY10.": ("date", "mmddyy", 0),
            "DDMMYY10.": ("date", "ddmmyy", 0),
            "E8601DA10.": ("date", "e8601da", 0),
            "B8601DA8.": ("date", "b8601da", 0),
            "MONYY7.": ("date", "monyy", 0),
            "DATETIME16.": ("datetime", "datetime", 0),
            "DATETIME18.": ("datetime", "datetime", 0),
            "DATETIME20.": ("datetime", "datetime", 0),
            "DATETIME23.3": ("datetime", "datetime", 3),
            "E8601DT19.": ("datetime", "e8601dt", 0),
            "E8601DT23.3": ("datetime", "e8601dt", 3),
            "E8601DT26.6": ("datetime", "e8601dt", 6),
            "B8601DT15.": ("datetime", "b8601dt", 0),
            "B8601DT19.3": ("datetime", "b8601dt", 3),
            "TIME5.": ("time", "time", 0),
            "TIME8.": ("time", "time", 0),
            "TIME10.": ("time", "time", 0),
            "TIME12.3": ("time", "time", 3),
            "HHMM5.": ("time", "hhmm", 0),
            "E8601TM8.": ("time", "e8601tm", 0),
            "E8601TM12.3": ("time", "e8601tm", 3),
            "B8601TM6.": ("time", "b8601tm", 0),
            "B8601TM10.3": ("time", "b8601tm", 3),
        }
        for sas_format, expected in cases.items():
            with self.subTest(sas_format=sas_format):
                parsed = parse_sas_temporal_format(sas_format)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(
                    (parsed.kind, parsed.family, parsed.decimals), expected
                )

    def test_date_formats_follow_metadata_not_variable_name(self) -> None:
        raw = 24_345
        self.assertEqual(
            format_sas_value(
                raw, VariableMetadata("XYZ", kind="numeric", format="DATE9.")
            ),
            "27AUG2026",
        )
        self.assertEqual(
            format_sas_value(
                raw, VariableMetadata("XYZ", kind="numeric", format="YYMMDD10.")
            ),
            "2026-08-27",
        )
        self.assertEqual(
            format_sas_value(
                raw, VariableMetadata("XYZ", kind="numeric", format="YYMMDDN8.")
            ),
            "20260827",
        )
        self.assertEqual(
            format_sas_value(
                raw, VariableMetadata("XYZ", kind="numeric", format="YYMMDDP10.")
            ),
            "2026.08.27",
        )
        self.assertEqual(
            format_sas_value(raw, VariableMetadata("ADT", kind="numeric", format="")),
            None,
        )

    def test_datetime_and_time_formats_keep_raw_values_external(self) -> None:
        self.assertEqual(
            format_sas_value(
                24_345 * 86_400 + 45_296,
                VariableMetadata("WHEN", kind="numeric", format="DATETIME20."),
            ),
            "27AUG2026:12:34:56",
        )
        self.assertEqual(
            format_sas_value(
                45_296.125,
                VariableMetadata("CLOCK", kind="numeric", format="TIME12.3"),
            ),
            "12:34:56.125",
        )
        self.assertEqual(
            format_sas_value(
                45_296.125,
                VariableMetadata("CLOCK", kind="numeric", format="B8601TM10.3"),
            ),
            "123456.125",
        )

    def test_xpt_equivalent_zero_decimal_suffix_is_supported(self) -> None:
        self.assertEqual(
            format_sas_value(
                24_345,
                VariableMetadata("DATE_VALUE", kind="numeric", format="DATE9.0"),
            ),
            "27AUG2026",
        )

    def test_supported_iso_and_calendar_format_families(self) -> None:
        raw_date = 24_345
        date_cases = {
            "DATE7.": "27AUG26",
            "MMDDYY10.": "08/27/2026",
            "DDMMYY10.": "27/08/2026",
            "E8601DA10.": "2026-08-27",
            "B8601DA8.": "20260827",
            "MONYY7.": "AUG2026",
        }
        for sas_format, expected in date_cases.items():
            with self.subTest(sas_format=sas_format):
                self.assertEqual(
                    format_sas_value(
                        raw_date,
                        VariableMetadata("VALUE", kind="numeric", format=sas_format),
                    ),
                    expected,
                )
        raw_datetime = raw_date * 86_400 + 45_296.125
        self.assertEqual(
            format_sas_value(
                raw_datetime,
                VariableMetadata("VALUE", kind="numeric", format="E8601DT23.3"),
            ),
            "2026-08-27T12:34:56.125",
        )
        self.assertEqual(
            format_sas_value(
                raw_datetime,
                VariableMetadata("VALUE", kind="numeric", format="B8601DT19.3"),
            ),
            "20260827T123456.125",
        )
        self.assertEqual(
            format_sas_value(
                45_296,
                VariableMetadata("VALUE", kind="numeric", format="E8601TM8."),
            ),
            "12:34:56",
        )
        self.assertEqual(
            format_sas_value(
                45_296,
                VariableMetadata("VALUE", kind="numeric", format="HHMM5."),
            ),
            "12:34",
        )

    def test_missing_character_and_unknown_formats_are_not_changed(self) -> None:
        date_variable = VariableMetadata("D", kind="numeric", format="DATE9.")
        self.assertIsNone(format_sas_value(None, date_variable))
        self.assertIsNone(format_sas_value(float("nan"), date_variable))
        self.assertIsNone(
            format_sas_value(
                24_350, VariableMetadata("C", kind="character", format="DATE9.")
            )
        )
        self.assertIsNone(
            format_sas_value(
                24_350, VariableMetadata("N", kind="numeric", format="BEST12.")
            )
        )

    def test_sas_temporal_literals_convert_back_to_raw_numeric_values(self) -> None:
        date_variable = VariableMetadata("ADT", kind="numeric", format="YYMMDD10.")
        datetime_variable = VariableMetadata(
            "ADTM", kind="numeric", format="E8601DT19."
        )
        time_variable = VariableMetadata("ATM", kind="numeric", format="TIME8.")
        self.assertEqual(
            sas_temporal_literal_value(
                SasTemporalLiteral("2026-08-27", "date"), date_variable
            ),
            24_345,
        )
        self.assertEqual(
            sas_temporal_literal_value(
                SasTemporalLiteral("2026-08-27T12:34:56", "datetime"), datetime_variable
            ),
            24_345 * 86_400 + 45_296,
        )
        self.assertEqual(
            sas_temporal_literal_value(
                SasTemporalLiteral("12:34:56", "time"), time_variable
            ),
            45_296,
        )

    def test_pre_1960_dates_remain_numeric_and_format_correctly(self) -> None:
        variable = VariableMetadata("HISTDT", kind="numeric", format="DATE9.")
        self.assertEqual(format_sas_value(-1, variable), "31DEC1959")
        self.assertEqual(
            sas_temporal_literal_value(
                SasTemporalLiteral("31DEC1959", "date"), variable
            ),
            -1,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import re
import unittest
from decimal import Decimal

from clinical_data_viewer.codegen.sas import SasListingGenerator


def _variable(
    kind: str, label: str = "", format_text: str = "", length: int = 20
) -> dict[str, object]:
    return {"type": kind, "label": label, "length": length, "format": format_text}


def _variable_expression(name: str, kind: str) -> dict[str, object]:
    return {"type": "variable", "name": name, "kind": kind}


def configuration(*, merge: bool = False) -> dict[str, object]:
    source_variables = {
        "USUBJID": _variable("character", "Subject"),
        "ADY": _variable("numeric", "Day"),
        "AESTDTC": _variable("character", "Date"),
    }
    variables = dict(source_variables)
    merge_block: dict[str, object] = {
        "enabled": merge,
        "by": ["USUBJID"],
        "keep": ["SAFFL"],
        "drop": [],
        "duplicate_policy": "ignore",
        "rename_map": {},
    }
    if merge:
        merge_block.update(
            {
                "input": {
                    "kind": "sas",
                    "format": "sas7bdat",
                    "dataset": "ADSL",
                    "source_path": "C:/data/adsl.sas7bdat",
                    "source_directory": "C:/data",
                },
                "variables": {
                    "USUBJID": source_variables["USUBJID"],
                    "SAFFL": _variable("character", "Flag"),
                },
                "source_variables": source_variables,
            }
        )
        variables["SAFFL"] = _variable("character", "Flag")

    concat = {
        "type": "concat",
        "left": {
            "type": "concat",
            "left": {
                "type": "concat",
                "left": _variable_expression("AESTDTC", "character"),
                "right": {
                    "type": "literal",
                    "value": " / (",
                    "kind": "character",
                },
            },
            "right": _variable_expression("ADY", "numeric"),
        },
        "right": {"type": "literal", "value": ")", "kind": "character"},
    }
    return {
        "type": "listing",
        "version": 1,
        "input": {
            "kind": "sas",
            "format": "sas7bdat",
            "dataset": "ADAE",
            "source_path": "C:/data/adae.sas7bdat",
            "source_directory": "C:/data",
        },
        "variables": variables,
        "merge_adsl": merge_block,
        "data_filter": {"language": "sas_like", "text": "", "ast": None},
        "columns": [
            {
                "expression": {
                    "text": "USUBJID",
                    "ast": _variable_expression("USUBJID", "character"),
                },
                "output_name": "USUBJID",
                "label": "Subject",
                "format": "",
                "sort": {"order": 1, "direction": "asc"},
                "report": {"include": True, "type": "order", "width_percent": 0},
                "post_process": {"division_by_zero": "error"},
            },
            {
                "expression": {
                    "text": "AESTDTC || ' / (' || ADY || ')'",
                    "ast": concat,
                },
                "output_name": "AESTDY",
                "label": "Start / Day",
                "format": "",
                "sort": {"order": None, "direction": "asc"},
                "report": {"include": True, "type": "display", "width_percent": 0},
                "post_process": {"division_by_zero": "error"},
            },
            {
                "expression": {
                    "text": "ADY",
                    "ast": _variable_expression("ADY", "numeric"),
                },
                "output_name": "ADY",
                "label": "",
                "format": "",
                "sort": {"order": 2, "direction": "desc"},
                "report": {"include": False, "type": "display", "width_percent": 0},
                "post_process": {"division_by_zero": "error"},
            },
        ],
        "sort": {"stable_tie_breaker": "_listing_row"},
        "report": {
            "line_size": 132,
            "width_method": "metadata_weighted_visible_columns",
        },
        "calculation": {
            "reference_engine": "python_listing_v1",
            "output_type": "expression_inferred",
            "character_length": "metadata_or_expression_inferred",
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": "adae",
                "output_dataset": "work.listing_sorted",
            }
        },
    }


class SasListingGeneratorTests(unittest.TestCase):
    def test_generates_readable_source_filter_sort_and_report(self) -> None:
        generator = SasListingGenerator()
        code = generator.generate(configuration())
        self.assertIn("/* 1. Prepare source and optional ADSL merge */", code)
        self.assertIn("_listing_row", code)
        self.assertIn("_lst_val3", code)
        self.assertIn("descending _lst_val3", code)
        self.assertIn("column USUBJID ADY AESTDY;", code)
        report = code.split("proc report data=work.listing_sorted", 1)[1]
        self.assertIn("define ADY / order order=data noprint", report)
        self.assertIn("length _lst_val1 $20", code)
        self.assertIn("length _lst_val2 $57", code)
        self.assertNotIn("_lst_out", code)
        self.assertIn("listing.sas.j2", generator.environment.list_templates())

    def test_report_order_data_is_only_used_for_order_and_group(self) -> None:
        code = SasListingGenerator().generate(configuration())
        subject = code.split("define USUBJID /", 1)[1].split(";", 1)[0]
        display = code.split("define AESTDY /", 1)[1].split(";", 1)[0]
        self.assertIn("order=data", subject)
        self.assertNotIn("order=data", display)

        cfg = configuration()
        cfg["columns"][1]["report"]["type"] = "group"
        code = SasListingGenerator().generate(cfg)
        grouped = code.split("define AESTDY /", 1)[1].split(";", 1)[0]
        self.assertIn("group", grouped)
        self.assertIn("order=data", grouped)
        self.assertTrue(
            all(
                line.rstrip().endswith(";")
                for line in code.splitlines()
                if line.strip().startswith(("column ", "define "))
            )
        )

    def test_sort_columns_lead_report_in_priority_order(self) -> None:
        cfg = configuration()
        cfg["columns"][0]["sort"] = {"order": 2, "direction": "asc"}
        cfg["columns"][0]["report"]["type"] = "display"
        cfg["columns"][2]["sort"] = {"order": 1, "direction": "desc"}

        code = SasListingGenerator().generate(cfg)

        self.assertIn(
            "by\n        descending _lst_val3\n"
            "        _lst_val1\n        _listing_row;",
            code,
        )
        self.assertIn("column ADY USUBJID AESTDY;", code)
        self.assertIn("define ADY / order order=data noprint", code)
        self.assertIn("define USUBJID / order order=data", code)
        self.assertNotIn("define USUBJID / order order=data noprint", code)

    def test_final_output_is_not_removed_by_cleanup(self) -> None:
        code = SasListingGenerator().generate(configuration())
        self.assertIn("proc sort data=listing_prep out=_lst_sorted;", code)
        self.assertIn("set _lst_sorted(", code)
        cleanup = code.split("proc datasets lib=work nolist;", 1)[1]
        self.assertIn("_lst_sorted", cleanup)
        self.assertNotIn("listing_sorted;", cleanup)

    def test_adsl_merge_has_left_merge_keep_without_runtime_key_checks(self) -> None:
        code = SasListingGenerator().generate(configuration(merge=True))
        self.assertIn("set adsl.ADSL(", code)
        self.assertIn("keep=USUBJID SAFFL", code)
        self.assertIn("merge lst_main(in=in_main) lst_adsl", code)
        self.assertIn("if in_main;", code)
        self.assertNotIn("if missing(USUBJID) then delete;", code)
        self.assertNotIn("_lst_adsl_dup", code)
        self.assertNotIn("ADSL is not unique", code)

    def test_merge_source_is_rejected(self) -> None:
        cfg = configuration()
        cfg["input"]["kind"] = "merge"
        cfg["input"]["format"] = "merge"
        with self.assertRaisesRegex(ValueError, "merged Listing"):
            SasListingGenerator().generate(cfg)

    def test_numeric_concat_uses_metadata_format_and_division_guard(self) -> None:
        cfg = configuration()
        cfg["variables"]["ADY"]["format"] = "8.2"
        cfg["columns"][1]["expression"]["ast"]["left"]["right"] = {
            "type": "variable",
            "name": "ADY",
            "kind": "numeric",
        }
        cfg["columns"].append(
            {
                "expression": {
                    "text": "ADY / ADY",
                    "ast": {
                        "type": "binary",
                        "operator": "/",
                        "left": _variable_expression("ADY", "numeric"),
                        "right": _variable_expression("ADY", "numeric"),
                    },
                },
                "output_name": "RATIO",
                "label": "Ratio",
                "format": "8.2",
                "sort": {"order": None, "direction": "asc"},
                "report": {"include": True, "type": "display", "width_percent": 0},
                "post_process": {"division_by_zero": "missing"},
            }
        )
        code = SasListingGenerator().generate(cfg)
        self.assertIn("strip(put(ADY, 8.2))", code)
        self.assertIn("if ADY = 0 then _lst_val4 = .;", code)
        self.assertIn("label RATIO = 'Ratio';", code)
        self.assertIn("data work.listing_sorted;", code)

    def test_numeric_report_columns_keep_numeric_type_and_format(self) -> None:
        cfg = configuration()
        cfg["columns"][0] = {
            "expression": {
                "text": "ADY",
                "ast": _variable_expression("ADY", "numeric"),
            },
            "output_name": "ADY_OUT",
            "label": "Study Day",
            "format": "8.2",
            "sort": {"order": 1, "direction": "asc"},
            "report": {"include": True, "type": "display", "width_percent": 0},
            "post_process": {"division_by_zero": "error"},
        }
        code = SasListingGenerator().generate(cfg)
        self.assertIn("_lst_val1 = ADY;", code)
        self.assertNotIn("length _lst_val1 $", code)
        self.assertIn("format ADY_OUT 8.2;", code)
        self.assertIn("format=8.2", code)

    def test_report_column_percentages_total_no_more_than_99(self) -> None:
        cfg = configuration()
        cfg["report"]["line_size"] = 40
        cfg["columns"][2]["report"]["include"] = True
        code = SasListingGenerator().generate(cfg)
        report = code.split("proc report", 1)[1].split("/* 5. Clean up */", 1)[0]
        widths = {
            name: Decimal(value)
            for name, value in re.findall(
                r"define\s+(\w+)\s+/[^\n]*cellwidth=([0-9.]+)%", report
            )
        }
        self.assertEqual(len(widths), 3)
        self.assertLessEqual(sum(widths.values()), Decimal(99))
        self.assertEqual(sum(widths.values()), Decimal(99))
        self.assertGreater(widths["AESTDY"], widths["USUBJID"])
        self.assertGreater(widths["USUBJID"], widths["ADY"])
        self.assertNotRegex(report, r"(?<!cell)width=")

    def test_long_character_metadata_receives_a_wider_percentage(self) -> None:
        cfg = configuration()
        cfg["variables"]["LONGTEXT"] = _variable(
            "character", "Long Narrative", length=500
        )
        cfg["columns"].append(
            {
                "expression": {
                    "text": "LONGTEXT",
                    "ast": _variable_expression("LONGTEXT", "character"),
                },
                "output_name": "LONGTEXT",
                "label": "Long Narrative",
                "format": "",
                "sort": {"order": None, "direction": "asc"},
                "report": {
                    "include": True,
                    "type": "display",
                    "width_percent": 0,
                },
                "post_process": {"division_by_zero": "error"},
            }
        )

        code = SasListingGenerator().generate(cfg)
        widths = {
            name: Decimal(width)
            for name, width in re.findall(
                r"define\s+(\w+)\s+/[^\n]*cellwidth=([0-9.]+)%", code
            )
        }

        self.assertEqual(sum(widths.values()), Decimal(99))
        self.assertGreater(widths["LONGTEXT"], widths["AESTDY"])
        self.assertGreater(widths["LONGTEXT"], widths["USUBJID"])

    def test_six_column_clinical_listing_conversions_merge_and_sort(self) -> None:
        cfg = configuration(merge=True)
        cfg["variables"].update(
            {
                "AETERM": _variable("character", "Reported Term"),
                "AESEQ": _variable("numeric", "Sequence"),
                "ADTM": _variable("numeric", "Analysis Datetime", "DATETIME20."),
                "TRT01A": _variable("character", "Actual Treatment"),
            }
        )
        cfg["merge_adsl"]["variables"]["TRT01A"] = _variable(
            "character", "Actual Treatment"
        )
        cfg["merge_adsl"]["keep"] = ["SAFFL", "TRT01A"]

        def column(
            expression_text,
            expression_ast,
            output,
            *,
            label="",
            format_text="",
            sort_order=None,
            direction="asc",
            include=True,
            report_type="display",
        ):
            return {
                "expression": {"text": expression_text, "ast": expression_ast},
                "output_name": output,
                "label": label,
                "format": format_text,
                "sort": {"order": sort_order, "direction": direction},
                "report": {
                    "include": include,
                    "type": report_type,
                    "width_percent": 0,
                },
                "post_process": {"division_by_zero": "error"},
            }

        cfg["columns"] = [
            column(
                "USUBJID",
                _variable_expression("USUBJID", "character"),
                "USUBJID",
                label="Subject",
                sort_order=1,
                report_type="order",
            ),
            column(
                "PUT(ADY, 8.)",
                {
                    "type": "function",
                    "name": "PUT",
                    "arguments": [
                        _variable_expression("ADY", "numeric"),
                        {"type": "format", "value": "8."},
                    ],
                },
                "ADY_TEXT",
                label="Study Day",
            ),
            column(
                "INPUT(AESTDTC, E8601DA.)",
                {
                    "type": "function",
                    "name": "INPUT",
                    "arguments": [
                        _variable_expression("AESTDTC", "character"),
                        {"type": "format", "value": "E8601DA."},
                    ],
                },
                "AESTDT",
                label="Start Date",
                format_text="DATE9.",
                sort_order=2,
            ),
            column(
                "CATX(' / ', AESTDTC, AETERM)",
                {
                    "type": "function",
                    "name": "CATX",
                    "arguments": [
                        {"type": "literal", "value": " / ", "kind": "character"},
                        _variable_expression("AESTDTC", "character"),
                        _variable_expression("AETERM", "character"),
                    ],
                },
                "EVENT_TEXT",
                label="Date / Event",
            ),
            column(
                "PUT(ADTM, DATETIME20.)",
                {
                    "type": "function",
                    "name": "PUT",
                    "arguments": [
                        _variable_expression("ADTM", "numeric"),
                        {"type": "format", "value": "DATETIME20."},
                    ],
                },
                "ADTM_TEXT",
                label="Analysis Datetime",
            ),
            column(
                "TRT01A",
                _variable_expression("TRT01A", "character"),
                "TRT01A",
                label="Treatment",
            ),
            column(
                "AESEQ",
                _variable_expression("AESEQ", "numeric"),
                "AESEQ",
                sort_order=3,
                direction="desc",
                include=False,
            ),
        ]

        code = SasListingGenerator().generate(cfg)

        self.assertIn("keep=USUBJID SAFFL TRT01A", code)
        self.assertIn("_lst_val2 = strip(put(ADY, 8.));", code)
        self.assertIn("_lst_val3 = input(AESTDTC, E8601DA.);", code)
        self.assertIn("format AESTDT DATE9.;", code)
        self.assertIn("_lst_val4 = catx(' / ', AESTDTC, AETERM);", code)
        self.assertIn("_lst_val5 = strip(put(ADTM, DATETIME20.));", code)
        self.assertIn(
            "by\n        _lst_val1\n        _lst_val3\n"
            "        descending _lst_val7\n        _listing_row;",
            code,
        )
        self.assertIn(
            "column USUBJID AESTDT AESEQ ADY_TEXT EVENT_TEXT ADTM_TEXT TRT01A;",
            code,
        )
        self.assertIn("define AESEQ / order order=data noprint", code)
        widths = [Decimal(value) for value in re.findall(r"cellwidth=([0-9.]+)%", code)]
        self.assertEqual(len(widths), 6)
        self.assertEqual(sum(widths), Decimal(99))

    def test_reserved_output_names_are_rejected(self) -> None:
        cfg = configuration()
        cfg["columns"][0]["output_name"] = "_source_row"
        with self.assertRaisesRegex(ValueError, "reserved"):
            SasListingGenerator().generate(cfg)

from __future__ import annotations

import re
import unittest

from clinical_data_viewer.codegen.sas import SasListingGenerator


def _variable(kind: str, label: str = "", format_text: str = "") -> dict[str, object]:
    return {"type": kind, "label": label, "length": 20, "format": format_text}


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
        "report": {"line_size": 132, "width_method": "equal_visible_columns"},
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
        self.assertIn("column\n        USUBJID\n        AESTDY", code)
        report = code.split("proc report data=work.listing_sorted", 1)[1]
        self.assertNotIn("        ADY\n    ;\n", report)
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

    def test_final_output_is_not_removed_by_cleanup(self) -> None:
        code = SasListingGenerator().generate(configuration())
        self.assertIn("proc sort data=listing_prep out=_lst_sorted;", code)
        self.assertIn("set _lst_sorted(", code)
        cleanup = code.split("proc datasets lib=work nolist;", 1)[1]
        self.assertIn("_lst_sorted", cleanup)
        self.assertNotIn("listing_sorted;", cleanup)

    def test_adsl_merge_has_left_merge_keep_and_missing_key_protection(self) -> None:
        code = SasListingGenerator().generate(configuration(merge=True))
        self.assertIn("set adsl.ADSL(", code)
        self.assertIn("keep=USUBJID SAFFL", code)
        self.assertIn("if missing(USUBJID) then delete;", code)
        self.assertIn("merge lst_main(in=in_main) lst_adsl", code)
        self.assertIn("if in_main;", code)

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

    def test_width_allocation_stays_within_line_size(self) -> None:
        cfg = configuration()
        cfg["report"]["line_size"] = 40
        cfg["columns"][2]["report"]["include"] = True
        code = SasListingGenerator().generate(cfg)
        report = code.split("proc report", 1)[1].split("/* 5. Clean up */", 1)[0]
        widths = [int(value) for value in re.findall(r"width=(\d+)", report)]
        self.assertEqual(len(widths), 3)
        self.assertLessEqual(sum(widths) + len(widths) - 1, 40)

    def test_reserved_output_names_are_rejected(self) -> None:
        cfg = configuration()
        cfg["columns"][0]["output_name"] = "_source_row"
        with self.assertRaisesRegex(ValueError, "reserved"):
            SasListingGenerator().generate(cfg)

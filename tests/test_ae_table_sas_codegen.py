from __future__ import annotations

import copy
import unittest

from clinical_data_viewer.codegen.sas import SasAeTableGenerator


def configuration(*, denominator="same_universe", include_any=True, include_total=True,
                  treatment_type="character", digits=1):
    variables = {
        "USUBJID": {"type": "character", "label": "Subject", "length": 20, "format": ""},
        "TRT01A": {"type": treatment_type, "label": "Treatment", "length": 20, "format": ""},
        "AEBODSYS": {"type": "character", "label": "SOC", "length": 100, "format": ""},
        "AEDECOD": {"type": "character", "label": "PT", "length": 100, "format": ""},
    }
    value = {
        "type": "ae_soc_pt_table", "version": 1,
        "input": {"kind": "sas", "format": "sas7bdat", "dataset": "ADAE",
                  "source_path": r"C:\data\adae.sas7bdat", "source_directory": r"C:\data"},
        "variables": variables,
        "dataset_filter": {"language": "sas_like", "text": "TRTEMFL = \"Y\"", "ast": {
            "type": "comparison", "variable": "TRTEMFL", "operator": "=",
            "operand": {"type": "literal", "value": "Y"}, "prefix": False}},
        "hierarchy": {"type": "soc_pt", "soc_variable": "AEBODSYS", "pt_variable": "AEDECOD",
                       "missing": {"policy": "exclude", "label": "Uncoded"}},
        "count": {"type": "distinct", "variable": "USUBJID"},
        "treatment": {"variable": "TRT01A", "missing_policy": "error", "level_order": "resolved",
                       "resolved_levels": [{"value": "Placebo", "label": "Placebo"}, {"value": "Drug A", "label": "Drug A"}]},
        "denominator": {"type": denominator},
        "rows": {"include_any_ae": include_any, "any_ae_label": "Any AE"},
        "sort": {"soc": {"by": "total_frequency", "direction": "desc", "tie_breaker": "alphabetical"},
                 "pt": {"by": "total_frequency", "direction": "desc", "tie_breaker": "alphabetical"}},
        "resolved_hierarchy": [{"row_type": "soc", "soc": "OLD", "pt": None, "item": "OLD", "indent": 0}],
        "total": {"enabled": include_total, "method": "recompute_distinct_subjects"},
        "calculation": {"reference_engine": "python_ae_soc_pt_v1", "numerator": "distinct_subjects",
                         "soc_count": "recompute_distinct_subjects", "pt_count": "recompute_distinct_subjects",
                         "subject_missing": "exclude", "treatment_missing": "error",
                         "percent_method": "freq_divided_by_denom_times_100", "total_method": "recompute_distinct_subjects"},
        "display": {"percent_digits": digits, "rounding": "half_up", "zero_denominator_display": "0 (—)"},
        "targets": {"sas": {"source_library": "analysis", "source_member": "adae", "output_dataset": "work.ae_soc_pt"}},
    }
    if denominator == "population":
        value["denominator"]["population"] = {
            "input": {"kind": "sas", "format": "sas7bdat", "dataset": "ADSL",
                      "source_path": r"C:\data\adsl.sas7bdat", "source_directory": r"C:\data"},
            "variables": {"USUBJID": variables["USUBJID"], "TRT01A": variables["TRT01A"]},
            "filter": {"language": "sas_like", "text": "SAFFL = \"Y\"", "ast": {
                "type": "comparison", "variable": "SAFFL", "operator": "=",
                "operand": {"type": "literal", "value": "Y"}, "prefix": False}},
        }
    return value


class AeSasGeneratorTests(unittest.TestCase):
    def test_runtime_sort_and_dynamic_columns(self):
        code = SasAeTableGenerator().generate(configuration())
        self.assertIn("proc sort data=soc_total out=soc_order", code)
        self.assertIn("descending freq soc", code)
        self.assertIn("descending p.freq, p.pt", code)
        self.assertIn("as col&_i", code)
        self.assertNotIn("resolved_hierarchy", code)
        self.assertIn("count(distinct a._subjid)", code)

    def test_population_filter_is_independent(self):
        code = SasAeTableGenerator().generate(configuration(denominator="population"))
        self.assertIn("set analysis.adae", code)
        self.assertIn("set population.ADSL", code)
        self.assertIn("if not (SAFFL = 'Y') then delete;", code)
        self.assertIn("from pop0", code)
        self.assertIn("from ae0", code)

    def test_options_and_final_columns(self):
        code = SasAeTableGenerator().generate(configuration(include_any=False, include_total=False, digits=2))
        self.assertNotIn("'Any AE'", code)
        self.assertNotIn("long_total", code)
        self.assertIn("12.2", code)
        self.assertIn("as item", code)

    def test_uncoded_and_xpt(self):
        cfg = configuration()
        cfg["input"]["format"] = "xpt"
        cfg["hierarchy"]["missing"]["policy"] = "uncoded"
        code = SasAeTableGenerator().generate(cfg)
        self.assertIn("libname analysis xport", code)
        self.assertIn("_soc = 'Uncoded'", code)

    def test_rejects_merge_and_invalid_numerator(self):
        cfg = configuration(); cfg["input"]["kind"] = "merge"
        with self.assertRaisesRegex(ValueError, "merged AE"):
            SasAeTableGenerator().generate(cfg)
        cfg = configuration(); cfg["calculation"]["numerator"] = "records"
        with self.assertRaisesRegex(ValueError, "calculation contract: numerator"):
            SasAeTableGenerator().generate(cfg)

    def test_rejects_malformed_contract(self):
        for key in ("sort", "hierarchy", "display", "calculation"):
            cfg = configuration(); del cfg[key]
            with self.assertRaises(ValueError):
                SasAeTableGenerator().generate(cfg)


if __name__ == "__main__":
    unittest.main()

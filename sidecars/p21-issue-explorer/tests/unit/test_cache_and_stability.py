from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast

from p21_issue_explorer.diagnostics import log_rule_match
from p21_issue_explorer.integration import SnapshotReader
from p21_issue_explorer.matching import (
    CachedRuleMatch,
    IssueMatcher,
    MatchCacheKey,
    MatchCancelled,
    RuleMatchCache,
)
from p21_issue_explorer.models import P21Issue
from snapshot_factory import create_snapshot


def _issue() -> P21Issue:
    return P21Issue(
        issue_id="cache-test",
        sheet_row=2,
        dataset="AE",
        record="1",
        count="",
        variables="AETERM",
        values="Headache",
        rule_id="RULE-CACHE",
        message="Synthetic issue",
        impact="Medium",
        issue_type="Synthetic",
    )


class RuleMatchCacheTests(unittest.TestCase):
    def test_key_includes_source_fingerprint_and_lru_is_bounded(self) -> None:
        cache = RuleMatchCache(max_entries=2)
        value = cast(CachedRuleMatch, cast(Any, object()))
        first = MatchCacheKey("report", "AE", "RULE", "source-1")
        changed_source = MatchCacheKey("report", "AE", "RULE", "source-2")
        third = MatchCacheKey("report", "AE", "OTHER", "source-1")
        cache.put(first, value)
        self.assertIs(cache.get(first), value)
        self.assertIsNone(cache.get(changed_source))
        cache.put(changed_source, value)
        cache.put(third, value)
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get(first))

    def test_matcher_honours_cancellation_inside_record_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = create_snapshot(
                Path(temporary),
                "AE",
                {"AETERM": "character"},
                [
                    {"_source_row": index, "AETERM": "Other"}
                    for index in range(1, 2_001)
                ],
            )
            with (
                SnapshotReader().open(root) as snapshot,
                self.assertRaises(MatchCancelled),
            ):
                IssueMatcher().match(_issue(), snapshot, cancelled=lambda: True)

    def test_diagnostics_include_counts_but_not_patient_values(self) -> None:
        with self.assertLogs("p21_issue_explorer", level="INFO") as captured:
            log_rule_match(
                domain="AE",
                rule_id="RULE-CACHE",
                issue_count=2,
                candidate_count=7,
                cache_hit=True,
            )
        message = captured.output[0]
        self.assertIn("domain=AE", message)
        self.assertIn("rule_id=RULE-CACHE", message)
        self.assertIn("issues=2", message)
        self.assertIn("candidates=7", message)
        self.assertNotIn("Headache", message)
        self.assertNotIn("where", message.lower())

    def test_broad_review_keeps_all_12k_candidates_within_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rows = [
                {
                    "_source_row": index,
                    "V1": "A",
                    "V2": "B",
                    "V3": "C",
                    "V4": "D",
                    "V5": "different",
                }
                for index in range(1, 12_001)
            ]
            root = create_snapshot(
                Path(temporary),
                "AE",
                {f"V{index}": "character" for index in range(1, 6)},
                rows,
            )
            broad_issue = P21Issue(
                issue_id="broad-review",
                sheet_row=2,
                dataset="AE",
                record="1",
                count="",
                variables="V1, V2, V3, V4, V5",
                values="A, B, C, D, expected",
                rule_id="RULE-BROAD",
                message="Synthetic broad review",
                impact="Medium",
                issue_type="Synthetic",
            )
            with SnapshotReader().open(root) as snapshot:
                started = time.monotonic()
                result = IssueMatcher().match(broad_issue, snapshot)
                elapsed = time.monotonic() - started
            self.assertEqual(len(result.candidates), 12_000)
            self.assertLess(elapsed, 15.0)


if __name__ == "__main__":
    unittest.main()

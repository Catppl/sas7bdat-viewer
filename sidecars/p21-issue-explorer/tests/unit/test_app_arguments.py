from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from p21_issue_explorer.app import build_parser


class AppArgumentTests(unittest.TestCase):
    def test_repeatable_snapshot_argument_preserves_domain_path_pairs(self) -> None:
        arguments = build_parser().parse_args(
            [
                "--report",
                "/tmp/report.xlsx",
                "--snapshot",
                "ae=/tmp/ae snapshot",
                "--snapshot",
                "LB=/tmp/lb",
            ]
        )
        self.assertEqual(arguments.report, Path("/tmp/report.xlsx"))
        self.assertEqual(
            arguments.snapshot,
            [("AE", Path("/tmp/ae snapshot")), ("LB", Path("/tmp/lb"))],
        )

    def test_snapshot_argument_requires_domain_equals_path(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["--snapshot", "AE"])


if __name__ == "__main__":
    unittest.main()

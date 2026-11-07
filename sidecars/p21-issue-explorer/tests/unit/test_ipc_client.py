from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from p21_issue_explorer.ipc_client import P21IpcClient
from p21_issue_explorer.models import (
    DomainExportRequest,
    ExportCandidateRow,
    ExportRuleGroup,
    IssueStatus,
)
from PySide6.QtWidgets import QApplication


class IpcClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_domain_export_is_chunked_without_deduplicating_rows(self) -> None:
        repeated = ExportCandidateRow(8, IssueStatus.MATCHED_EXACT, "issue-1", 10)
        request = DomainExportRequest(
            "AE",
            "report.xlsx",
            "source-fingerprint",
            (
                ExportRuleGroup(
                    "CT2002",
                    1,
                    tuple(repeated for _index in range(5_001)),
                ),
                ExportRuleGroup("CT2003", 1, ()),
            ),
        )
        client = P21IpcClient("test-server", "test-token")
        calls: list[tuple[str, dict, str | None]] = []

        def capture(message_type, payload, *, request_id=None):
            calls.append((message_type, payload, request_id))
            return request_id or "generated"

        with patch.object(client, "_send", side_effect=capture):
            client.send_domain_export_request(request)

        self.assertEqual(
            [message_type for message_type, _payload, _request_id in calls],
            [
                "begin_domain_export",
                "append_domain_export_rows",
                "append_domain_export_rows",
                "finish_domain_export",
            ],
        )
        request_ids = {request_id for _type, _payload, request_id in calls}
        self.assertEqual(len(request_ids), 1)
        self.assertIsNotNone(next(iter(request_ids)))
        begin_rules = calls[0][1]["rules"]
        self.assertEqual(
            begin_rules,
            [
                {"rule_id": "CT2002", "p21_issue_count": 1},
                {"rule_id": "CT2003", "p21_issue_count": 1},
            ],
        )
        chunk_rows = calls[1][1]["rows"] + calls[2][1]["rows"]
        self.assertEqual(len(chunk_rows), 5_001)
        self.assertTrue(all(row["source_row"] == 8 for row in chunk_rows))


if __name__ == "__main__":
    unittest.main()

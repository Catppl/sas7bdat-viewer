from __future__ import annotations

import logging

LOGGER = logging.getLogger("p21_issue_explorer")


def log_rule_match(
    *,
    domain: str,
    rule_id: str,
    issue_count: int,
    candidate_count: int,
    cache_hit: bool,
) -> None:
    """Log counts and identifiers only; never values, WHERE text, or patient data."""

    LOGGER.info(
        "rule_match domain=%s rule_id=%s issues=%d candidates=%d cache_hit=%s",
        domain,
        rule_id,
        issue_count,
        candidate_count,
        cache_hit,
    )

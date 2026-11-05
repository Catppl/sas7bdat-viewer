from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from ..models import IssueMatchResult, RuleMatchSummary

MATCHER_VERSION = "1"


@dataclass(frozen=True, slots=True)
class MatchCacheKey:
    report_fingerprint: str
    domain: str
    rule_id: str
    source_fingerprint: str
    matcher_version: str = MATCHER_VERSION


@dataclass(frozen=True, slots=True)
class CachedRuleMatch:
    results: tuple[IssueMatchResult, ...]
    summary: RuleMatchSummary


class RuleMatchCache:
    """Small thread-safe LRU cache keyed only by non-PHI fingerprints and IDs."""

    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[MatchCacheKey, CachedRuleMatch] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: MatchCacheKey) -> CachedRuleMatch | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def put(self, key: MatchCacheKey, value: CachedRuleMatch) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

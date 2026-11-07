from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSplitter,
)

from ..diagnostics import log_rule_match
from ..integration import SnapshotProvider
from ..matching import (
    CachedRuleMatch,
    IssueMatcher,
    MatchCacheKey,
    RuleMatchCache,
)
from ..models import (
    DomainExportRequest,
    IssueMatchResult,
    IssueOpenRequest,
    IssueStatus,
    P21Issue,
    P21Report,
    RuleMatchSummary,
    RuleOpenRequest,
    summarize_rule,
)
from .import_page import ImportPage
from .issue_detail import IssueDetailWidget
from .issue_table import IssueTableView
from .rule_summary import RuleSummaryWidget


@dataclass(frozen=True, slots=True)
class _MatchPayload:
    domain: str
    rule_id: str
    results: tuple[IssueMatchResult, ...]
    summary: RuleMatchSummary
    cache_hit: bool = False


CANDIDATE_SOFT_LIMIT = 10_000


class _MatchSignals(QObject):
    progress = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _RuleMatchTask(QRunnable):
    def __init__(
        self,
        provider: SnapshotProvider,
        domain: str,
        rule_id: str,
        issues: tuple[P21Issue, ...],
        report_fingerprint: str,
        cache: RuleMatchCache,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.domain = domain
        self.rule_id = rule_id
        self.issues = issues
        self.report_fingerprint = report_fingerprint
        self.cache = cache
        self.signals = _MatchSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            results: list[IssueMatchResult] = []
            requested_variables = _requested_snapshot_variables(self.issues)
            with self.provider.open_snapshot(
                self.domain, requested_variables
            ) as snapshot:
                cache_key = MatchCacheKey(
                    self.report_fingerprint,
                    self.domain.upper(),
                    self.rule_id,
                    snapshot.manifest.source_fingerprint,
                )
                cached = self.cache.get(cache_key)
                if cached is not None:
                    self.signals.progress.emit(len(self.issues), len(self.issues))
                    self.signals.succeeded.emit(
                        _MatchPayload(
                            self.domain,
                            self.rule_id,
                            cached.results,
                            cached.summary,
                            True,
                        )
                    )
                    return
                total = len(self.issues)
                for index, issue in enumerate(self.issues, start=1):
                    if self._cancelled.is_set():
                        return
                    results.append(
                        IssueMatcher().match(
                            issue,
                            snapshot,
                            cancelled=self._cancelled.is_set,
                        )
                    )
                    self.signals.progress.emit(index, total)
            if self._cancelled.is_set():
                return
            frozen_results = tuple(results)
            summary = summarize_rule(frozen_results)
            self.cache.put(cache_key, CachedRuleMatch(frozen_results, summary))
            self.signals.succeeded.emit(
                _MatchPayload(self.domain, self.rule_id, frozen_results, summary)
            )
        # The background boundary converts snapshot and matcher failures into a
        # visible Cannot Evaluate state instead of terminating the GUI thread.
        except Exception as exc:  # noqa: BLE001
            if not self._cancelled.is_set():
                self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()


class _DomainExportTask(QRunnable):
    def __init__(
        self,
        provider: SnapshotProvider,
        domain: str,
        issues: tuple[P21Issue, ...],
        report_fingerprint: str,
        report_name: str,
        cache: RuleMatchCache,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.domain = domain
        self.issues = issues
        self.report_fingerprint = report_fingerprint
        self.report_name = report_name
        self.cache = cache
        self.signals = _MatchSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            by_rule: dict[str, list[P21Issue]] = {}
            for issue in self.issues:
                by_rule.setdefault(issue.rule_id, []).append(issue)
            grouped_results: list[tuple[str, tuple[IssueMatchResult, ...]]] = []
            completed = 0
            total = len(self.issues)
            with self.provider.open_snapshot(
                self.domain, _requested_snapshot_variables(self.issues)
            ) as snapshot:
                for rule_id in sorted(by_rule, key=str.casefold):
                    if self._cancelled.is_set():
                        return
                    rule_issues = tuple(by_rule[rule_id])
                    key = MatchCacheKey(
                        self.report_fingerprint,
                        self.domain.upper(),
                        rule_id,
                        snapshot.manifest.source_fingerprint,
                    )
                    cached = self.cache.get(key)
                    if cached is not None:
                        results = cached.results
                        completed += len(rule_issues)
                        self.signals.progress.emit(completed, total)
                    else:
                        matched: list[IssueMatchResult] = []
                        for issue in rule_issues:
                            if self._cancelled.is_set():
                                return
                            matched.append(
                                IssueMatcher().match(
                                    issue,
                                    snapshot,
                                    cancelled=self._cancelled.is_set,
                                )
                            )
                            completed += 1
                            self.signals.progress.emit(completed, total)
                        results = tuple(matched)
                        self.cache.put(
                            key,
                            CachedRuleMatch(results, summarize_rule(results)),
                        )
                    grouped_results.append((rule_id, results))
            if not self._cancelled.is_set():
                self.signals.succeeded.emit(
                    DomainExportRequest.from_results(
                        self.domain,
                        self.report_name,
                        snapshot.manifest.source_fingerprint,
                        tuple(grouped_results),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures
            if not self._cancelled.is_set():
                self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()


def _requested_snapshot_variables(
    issues: tuple[P21Issue, ...],
) -> tuple[str, ...]:
    """Return local P21 variables without applying any matching semantics."""

    names: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        for raw_name in issue.variables.split(","):
            name = raw_name.strip()
            if not name or ":" in name or name.casefold().endswith("seq"):
                continue
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                names.append(name)
    return tuple(names)


class ExplorerPage(ImportPage):
    open_issue_requested = Signal(object)
    filter_all_requested = Signal(object)
    export_domain_requested = Signal(object)

    def __init__(
        self,
        *,
        snapshot_provider: SnapshotProvider | None = None,
        parent=None,
        **import_options,
    ) -> None:
        self.snapshot_provider = snapshot_provider
        self._match_generation = 0
        self._current_task: _RuleMatchTask | None = None
        self._current_export_task: _DomainExportTask | None = None
        self._active_match_tasks: set[_RuleMatchTask] = set()
        self._active_export_tasks: set[_DomainExportTask] = set()
        self.current_results: tuple[IssueMatchResult, ...] = ()
        self.current_summary: RuleMatchSummary | None = None
        self.match_cache = RuleMatchCache()
        super().__init__(parent=parent, **import_options)
        self._build_explorer_ui()
        self.import_started.connect(self._reset_for_import)
        self.report_loaded.connect(self._report_ready)
        self.domain_combo.currentTextChanged.connect(self._domain_changed)

    def _build_explorer_ui(self) -> None:
        selection_form = QFormLayout()
        rule_row = QHBoxLayout()
        self.rule_combo = QComboBox()
        self.rule_combo.setObjectName("ruleIdCombo")
        self.rule_combo.setEnabled(False)
        self.rule_combo.currentIndexChanged.connect(self._rule_changed)
        rule_row.addWidget(self.rule_combo, 1)
        self.run_match_button = QPushButton("Run Match")
        self.run_match_button.setObjectName("runMatchButton")
        self.run_match_button.setEnabled(False)
        self.run_match_button.clicked.connect(self._start_selected_rule)
        rule_row.addWidget(self.run_match_button)
        selection_form.addRow("Rule ID", rule_row)
        self.export_domain_button = QPushButton("Export Domain Matched Records…")
        self.export_domain_button.setObjectName("exportDomainMatchedRecordsButton")
        self.export_domain_button.setEnabled(False)
        self.export_domain_button.clicked.connect(self._start_domain_export)
        selection_form.addRow("Excel", self.export_domain_button)
        self.main_layout.addLayout(selection_form)

        progress_row = QHBoxLayout()
        self.match_status = QLabel("Import a report to begin.")
        self.match_status.setObjectName("matchStatusLabel")
        self.match_status.setWordWrap(True)
        progress_row.addWidget(self.match_status, 1)
        self.match_progress = QProgressBar()
        self.match_progress.setObjectName("matchProgress")
        self.match_progress.setMinimumWidth(180)
        self.match_progress.hide()
        progress_row.addWidget(self.match_progress)
        self.cancel_match_button = QPushButton("Cancel")
        self.cancel_match_button.setObjectName("cancelMatchButton")
        self.cancel_match_button.setEnabled(False)
        self.cancel_match_button.clicked.connect(self.cancel_matching)
        progress_row.addWidget(self.cancel_match_button)
        self.main_layout.addLayout(progress_row)

        self.rule_summary = RuleSummaryWidget()
        self.rule_summary.filter_all_requested.connect(self._emit_filter_all_request)
        self.main_layout.addWidget(self.rule_summary)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("explorerSplitter")
        self.issue_table = IssueTableView()
        self.issue_table.result_activated.connect(self._issue_activated)
        splitter.addWidget(self.issue_table)
        self.issue_detail = IssueDetailWidget()
        self.issue_detail.open_issue_requested.connect(self.open_issue_requested)
        splitter.addWidget(self.issue_detail)
        splitter.setSizes((310, 390))
        self.main_layout.addWidget(splitter, 1)

    @Slot(object)
    def _reset_for_import(self, _path: object) -> None:
        self._invalidate_matching()
        self.match_cache.clear()
        self._clear_results()
        with QSignalBlocker(self.rule_combo):
            self.rule_combo.clear()
        self.rule_combo.setEnabled(False)
        self.run_match_button.setEnabled(False)
        self.export_domain_button.setEnabled(False)
        self.match_status.setText("Importing the latest P21 report…")

    @Slot(object)
    def _report_ready(self, report: P21Report) -> None:
        if report.domains:
            self._select_domain(self.domain_combo.currentText())
        else:
            self.match_status.setText("The report has no eligible domains.")

    @Slot(str)
    def _domain_changed(self, domain: str) -> None:
        if self.report is not None and domain:
            self._select_domain(domain)

    def _select_domain(self, domain: str) -> None:
        self._invalidate_matching()
        self._clear_results()
        rule_counts: dict[str, int] = {}
        if self.report is not None:
            for issue in self.report.issues:
                if issue.dataset.casefold() == domain.casefold():
                    rule_counts[issue.rule_id] = rule_counts.get(issue.rule_id, 0) + 1
        with QSignalBlocker(self.rule_combo):
            self.rule_combo.clear()
            for rule_id in sorted(rule_counts, key=str.casefold):
                count = rule_counts[rule_id]
                suffix = "issue" if count == 1 else "issues"
                self.rule_combo.addItem(f"{rule_id} ({count} {suffix})", rule_id)
        self.rule_combo.setEnabled(bool(rule_counts))
        self.run_match_button.setEnabled(bool(rule_counts))
        self.export_domain_button.setEnabled(
            bool(rule_counts) and self.snapshot_provider is not None
        )
        if rule_counts:
            self._start_selected_rule()
        else:
            self.match_status.setText(f"No eligible issues are available for {domain}.")

    @Slot(int)
    def _rule_changed(self, _index: int) -> None:
        if self.rule_combo.isEnabled():
            self._start_selected_rule()

    def _start_selected_rule(self) -> None:
        domain = self.domain_combo.currentText()
        rule_id = self.rule_combo.currentData()
        if self.report is None or not domain or not rule_id:
            return
        issues = tuple(
            issue
            for issue in self.report.issues
            if issue.dataset.casefold() == domain.casefold()
            and issue.rule_id == rule_id
        )
        generation = self._invalidate_matching()
        self._clear_results()
        self._set_matching_busy(True, len(issues))
        self.match_status.setText(
            f"Matching {len(issues)} P21 issue(s) for {domain} / {rule_id}…"
        )
        if self.snapshot_provider is None:
            self._apply_unavailable(
                generation,
                domain,
                rule_id,
                issues,
                f"Current data snapshot is unavailable for domain {domain}.",
            )
            return
        task = _RuleMatchTask(
            self.snapshot_provider,
            domain,
            rule_id,
            issues,
            self.report.fingerprint,
            self.match_cache,
        )
        self._current_task = task
        self._active_match_tasks.add(task)
        task.signals.progress.connect(
            lambda current, total, current_generation=generation: (
                self._match_progressed(current_generation, current, total)
            )
        )
        task.signals.succeeded.connect(
            lambda payload, current_generation=generation: self._match_succeeded(
                current_generation, payload
            )
        )
        task.signals.failed.connect(
            lambda message, current_generation=generation, current_domain=domain, current_rule=rule_id, current_issues=issues: (
                self._apply_unavailable(
                    current_generation,
                    current_domain,
                    current_rule,
                    current_issues,
                    message,
                )
            )
        )
        task.signals.finished.connect(
            lambda current_generation=generation, current_task=task: (
                self._match_finished(current_generation, current_task)
            )
        )
        self.thread_pool.start(task)

    def _start_domain_export(self) -> None:
        domain = self.domain_combo.currentText()
        if self.report is None or not domain:
            return
        if self.snapshot_provider is None:
            self.match_status.setText(
                f"Cannot export {domain}: current data snapshot is unavailable."
            )
            return
        issues = tuple(
            issue
            for issue in self.report.issues
            if issue.dataset.casefold() == domain.casefold()
        )
        if not issues:
            self.match_status.setText(f"No eligible issues are available for {domain}.")
            return
        generation = self._invalidate_matching()
        self._set_matching_busy(True, len(issues))
        self.match_status.setText(
            f"Preparing Excel export for {len(issues)} P21 issue(s) in {domain}…"
        )
        task = _DomainExportTask(
            self.snapshot_provider,
            domain,
            issues,
            self.report.fingerprint,
            self.report.source_path.name,
            self.match_cache,
        )
        self._current_export_task = task
        self._active_export_tasks.add(task)
        task.signals.progress.connect(
            lambda current, total, current_generation=generation: (
                self._match_progressed(current_generation, current, total)
            )
        )
        task.signals.succeeded.connect(
            lambda request, current_generation=generation: self._export_succeeded(
                current_generation, request
            )
        )
        task.signals.failed.connect(
            lambda message, current_generation=generation: self._export_failed(
                current_generation, message
            )
        )
        task.signals.finished.connect(
            lambda current_generation=generation, current_task=task: (
                self._export_finished(current_generation, current_task)
            )
        )
        self.thread_pool.start(task)

    @Slot()
    def cancel_matching(self) -> None:
        if self._current_task is None and self._current_export_task is None:
            return
        self._invalidate_matching()
        self._set_matching_busy(False, 0)
        self.match_status.setText("Operation cancelled.")

    def _invalidate_matching(self) -> int:
        self._match_generation += 1
        if self._current_task is not None:
            self._current_task.cancel()
            self._current_task = None
        if self._current_export_task is not None:
            self._current_export_task.cancel()
            self._current_export_task = None
        return self._match_generation

    def _match_progressed(self, generation: int, current: int, total: int) -> None:
        if generation == self._match_generation:
            self.match_progress.setRange(0, max(total, 1))
            self.match_progress.setValue(current)

    def _match_succeeded(self, generation: int, payload: _MatchPayload) -> None:
        if generation != self._match_generation:
            return
        self._apply_results(payload.results, payload.summary)
        status = (
            f"Matched {payload.summary.p21_issue_count} issue(s); "
            f"found {payload.summary.current_candidate_record_count} current "
            "candidate row(s), preserving repeated matches."
        )
        if payload.cache_hit:
            status += " Cached result reused."
        if payload.summary.current_candidate_record_count > CANDIDATE_SOFT_LIMIT:
            status += (
                f" Review recommended: candidate count exceeds the "
                f"{CANDIDATE_SOFT_LIMIT:,} soft limit; no records were omitted."
            )
        self.match_status.setText(status)
        log_rule_match(
            domain=payload.domain,
            rule_id=payload.rule_id,
            issue_count=payload.summary.p21_issue_count,
            candidate_count=payload.summary.current_candidate_record_count,
            cache_hit=payload.cache_hit,
        )

    def _export_succeeded(self, generation: int, request: DomainExportRequest) -> None:
        if generation != self._match_generation:
            return
        row_count = sum(len(rule.rows) for rule in request.rules)
        matched_rules = sum(bool(rule.rows) for rule in request.rules)
        if row_count == 0:
            self.match_status.setText(
                f"No matched records are available to export for {request.domain}."
            )
            return
        self.match_status.setText(
            f"Prepared {row_count:,} export row(s) across {matched_rules} Rule ID(s). "
            "Choose the Excel destination in SASDataViewer."
        )
        self.export_domain_requested.emit(request)

    def _export_failed(self, generation: int, message: str) -> None:
        if generation == self._match_generation:
            self.match_status.setText(f"Excel export preparation failed: {message}")

    def _apply_unavailable(
        self,
        generation: int,
        domain: str,
        rule_id: str,
        issues: tuple[P21Issue, ...],
        message: str,
    ) -> None:
        if generation != self._match_generation:
            return
        results = tuple(
            IssueMatchResult(
                issue=issue,
                status=IssueStatus.CANNOT_EVALUATE,
                diagnostics=(f"Current data unavailable: {message}",),
            )
            for issue in issues
        )
        if results:
            self._apply_results(results, summarize_rule(results))
        self.match_status.setText(f"Cannot evaluate {domain} / {rule_id}: {message}")
        self._set_matching_busy(False, 0)

    def _apply_results(
        self,
        results: tuple[IssueMatchResult, ...],
        summary: RuleMatchSummary,
    ) -> None:
        self.current_results = results
        self.current_summary = summary
        self.issue_table.set_results(results)
        self.rule_summary.set_summary(summary)
        if results:
            self.issue_detail.set_result(results[0])

    def _match_finished(self, generation: int, task: _RuleMatchTask) -> None:
        self._active_match_tasks.discard(task)
        if generation == self._match_generation:
            if self._current_task is task:
                self._current_task = None
            self._set_matching_busy(False, 0)

    def _export_finished(self, generation: int, task: _DomainExportTask) -> None:
        self._active_export_tasks.discard(task)
        if generation == self._match_generation:
            if self._current_export_task is task:
                self._current_export_task = None
            self._set_matching_busy(False, 0)

    def _set_matching_busy(self, busy: bool, total: int) -> None:
        self.match_progress.setVisible(busy)
        self.match_progress.setRange(0, max(total, 1))
        self.match_progress.setValue(0)
        self.cancel_match_button.setEnabled(busy)
        self.domain_combo.setEnabled(self.domain_combo.count() > 0)
        self.rule_combo.setEnabled(self.rule_combo.count() > 0)
        self.run_match_button.setEnabled(not busy and self.rule_combo.count() > 0)
        self.export_domain_button.setEnabled(
            not busy
            and self.snapshot_provider is not None
            and self.report is not None
            and self.domain_combo.currentText() != ""
        )

    def _clear_results(self) -> None:
        self.current_results = ()
        self.current_summary = None
        if hasattr(self, "issue_table"):
            self.issue_table.set_results(())
        if hasattr(self, "rule_summary"):
            self.rule_summary.clear()
        if hasattr(self, "issue_detail"):
            self.issue_detail.clear()

    def _issue_activated(self, result: IssueMatchResult) -> None:
        self.issue_detail.set_result(result)
        request = IssueOpenRequest.from_result(result)
        if request.where_text or request.source_rows:
            self.open_issue_requested.emit(request)

    def _emit_filter_all_request(self) -> None:
        if self.current_summary is None:
            return
        self.filter_all_requested.emit(
            RuleOpenRequest.from_results(
                self.domain_combo.currentText(),
                self.current_summary,
                self.current_results,
            )
        )

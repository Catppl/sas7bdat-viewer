# P21 Issue Explorer side-car

This package is the optional P21 Issue Explorer process. It remains independent from the SAS Viewer host and currently implements phases P0–P2:

- synthetic report and current-data contracts;
- exact `Issue Details` worksheet selection;
- required-header discovery within the first 100 rows;
- first-version `Count`/`Record` eligibility filtering;
- typed issue and import diagnostics models;
- standalone import/domain UI with background report loading;
- versioned Dataset Snapshot v1 validation and read-only SQLite access;
- type-aware character, numeric, DTC, and missing-value comparison;
- comma-safe Values alignment with ambiguous mapping preservation;
- strict exact and 80% partial/lenient candidate matching;
- Expected/Applied SAS-style WHERE generation and rule summaries.

Host-side Snapshot creation and IPC, the full Explorer issue UI, Viewer result tabs, packaging, and Excel issue export are not implemented in P0–P2.

## Run from the repository

```bash
PYTHONPATH=sidecars/p21-issue-explorer/src \
  .venv/bin/python -m p21_issue_explorer \
  --report sidecars/p21-issue-explorer/tests/fixtures/p21_synthetic_report.xlsx
```

## Test

```bash
PYTHONPATH=sidecars/p21-issue-explorer/src \
QT_QPA_PLATFORM=offscreen \
  .venv/bin/python -m unittest discover \
  -s sidecars/p21-issue-explorer/tests -v
```

The fixtures contain invented identifiers and values only.

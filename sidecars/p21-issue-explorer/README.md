# P21 Issue Explorer side-car

This package is the optional P21 Issue Explorer process. It remains independent from the SAS Viewer host and currently implements phases P0–P4:

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
- Expected/Applied SAS-style WHERE generation and rule summaries;
- independent Explorer UI with domain/Rule ID selection, traceable summary counts,
  colored issue rows, candidate details, cancellation, and stale-result protection;
- injectable Snapshot Provider and typed issue/rule view requests;
- authenticated, versioned `QLocalSocket` IPC with Viewer-provided snapshots;
- automatic snapshot release and issue/rule result-view requests.

The Viewer host, Dataset Snapshot v1 builder, lazy manifest-gated launcher, and reusable P21 Result View are implemented in the main package. P5 performance/cache work, P6 packaging, and deferred Excel issue export are not implemented yet.

## Run from the repository

```bash
PYTHONPATH=sidecars/p21-issue-explorer/src \
  .venv/bin/python -m p21_issue_explorer \
  --report sidecars/p21-issue-explorer/tests/fixtures/p21_synthetic_report.xlsx \
  --snapshot AE=/path/to/ae-snapshot \
  --snapshot LB=/path/to/lb-snapshot
```

`--snapshot DOMAIN=PATH` is a repeatable standalone/fake-host option. Without a
snapshot for the selected domain, issues are shown as `Cannot Evaluate`; they are
never silently treated as `Not Found`.

## Test

```bash
PYTHONPATH=sidecars/p21-issue-explorer/src \
QT_QPA_PLATFORM=offscreen \
  .venv/bin/python -m unittest discover \
  -s sidecars/p21-issue-explorer/tests -v
```

The fixtures contain invented identifiers and values only.

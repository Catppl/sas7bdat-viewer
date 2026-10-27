# Baseline Characteristics Builder Design

## Status and approval boundary

This document freezes the business and display contract derived from the supplied
table-shell photograph for the shell-asset phase.

Approved in the current phase:

- this design document;
- the editable source shell at `docs/baseline-characteristics-shell.docx`;
- its byte-identical packaged DOCX copy;
- the PNG rendered from the final source DOCX.

Not approved in the current phase:

- domain models, Python engine, result store, configuration JSON, drill-down,
  Builder UI, controller, SAS generator, Gallery registration, menus, tests, or
  documentation changes outside these shell assets.

The future implementation must follow
`docs/table-shell-builder-codegen-gallery-execution-playbook.md` and remain an
independent module. Existing Categorical and PROC MEANS business semantics must
not change.

## Module identity

| Field | Contract |
| --- | --- |
| Module key | `baseline_characteristics` |
| UI title | `Baseline Characteristics` |
| Result kind | `baseline_characteristics` |
| Configuration file | `baseline_characteristics_config.json` |
| Python reference engine | `python_baseline_characteristics_v1` |
| SAS output | `work.baseline_characteristics` |
| SAS long output | `work.baseline_characteristics_long` |

## Source and analysis universe

- The module uses one analysis source dataset.
- The Builder owns an independent Dataset Filter. Later changes to the source
  tab's WHERE must not silently change a saved Builder configuration or an
  existing result.
- `USUBJID` is required.
- After the Dataset Filter is applied, the analysis universe must contain one
  row per nonmissing `USUBJID`. Duplicate participant rows are an error because
  the shell defines `N` and `n` as participant counts.
- The first version has one summary column and no treatment/group columns.
- The editable column heading defaults to `Overall` and the header displays the
  analysis-universe count as `N=xx`.
- No separate Total column is produced in the single-column layout.

## Ordered mixed items

The Builder will eventually maintain one ordered, duplicate-free sequence of
items. Each item has a type of `categorical` or `continuous`. Categorical and
continuous items may be interleaved, and Up/Down changes the final table order.

Every item stores:

- source variable;
- display label;
- optional unit;
- item type;
- type-specific display and calculation settings.

The shell asset shows two categorical items followed by two continuous items,
but that sequence is an example rather than a hard-coded runtime definition.

### Variable storage type and analysis role

Variable storage type and analysis role are separate contracts:

- The application reads the physical storage type (`character` or `numeric`)
  from the source dataset's `VariableMetadata.kind`. The user does not enter or
  override this type.
- The user explicitly chooses the analysis role by adding the variable as a
  Categorical Item or a Continuous Item.
- A character variable may only be used as a Categorical Item.
- A numeric variable may be used as either a Categorical Item or a Continuous
  Item. This supports numeric-coded categories such as severity grades or age
  bands.
- Adding a Continuous Item with a nonnumeric source variable is a validation
  error shown before execution.
- Categorical grouping always uses the raw typed value. Numeric categorical
  values must not be converted to formatted display strings for grouping,
  sorting, counting, JSON, SAS generation, or drill-down.
- The UI may recommend Categorical for character variables and Continuous for
  numeric variables, but it must not silently infer or change the analysis
  role from cardinality, labels, formats, or observed values.
- No implicit character-to-numeric or numeric-to-character conversion is
  allowed.

## Categorical item contract

- The item heading is `<label> - n (%)`.
- Each observed nonmissing level is shown as an indented row.
- `n` is the number of distinct participants at that level.
- Percentage is `level participant n / header N * 100`.
- Missing categorical values are excluded and are not displayed as a level.
- Only levels represented in the filtered analysis universe are displayed.
- The default level order is runtime raw-value ascending, with numeric values
  sorted numerically, character values case-insensitively, and missing last.
- Each item may instead define an explicit custom raw-value order. Values not in
  the custom list follow the configured values in runtime ascending order.
- Percentage rounding uses `ROUND_HALF_UP`; the default display has one decimal
  place and allows zero through four decimal places.
- A zero denominator displays `0 (-)` in plain-text exports and the Viewer.

## Continuous item contract

- The item heading is `<label> (<unit>)` when a unit is configured, otherwise
  `<label>`.
- The fixed statistic order is:
  1. `n`
  2. `Mean`
  3. `SD`
  4. `Median`
  5. `Q1, Q3`
  6. `Min, Max`
- `n` is the number of distinct participants with a nonmissing analysis value.
- Mean, SD, median, quartiles, minimum, and maximum use the nonmissing raw
  numeric values in the filtered analysis universe.
- SD is the sample standard deviation with denominator `n - 1`.
- Median and quartiles use the existing SAS-compatible `QNTLDEF=5` logic.
- No `NMISS`, standard error, or confidence-limit row is added.
- `n` is always displayed as an integer.
- Each continuous item stores an explicit `Auto` or zero-through-four decimal
  setting for each displayed statistic. Paired statistics use one setting per
  component when needed to reproduce the shell.
- `Auto` is based on observed raw-value precision and all numeric rounding uses
  `ROUND_HALF_UP`, capped at four decimal places.
- A statistic with no calculable value displays `-`.

## Display contract

- The result is a clinical black-and-white three-line table.
- The first column contains item headings, indented categorical levels, and
  indented continuous statistic labels.
- The second column contains the editable header, `N=xx`, and result values.
- Item headings are visually distinct from child rows without introducing a
  full table grid.
- Categorical values display as `n (percent)`.
- Paired continuous statistics display as `value, value`.
- The shell uses placeholders only. Example variable names, labels, levels,
  units, and `x` values must not be hard-coded in the future engine.
- The authoritative future result also includes a long table with explicit row
  order, item type, raw level or statistic key, participant counts, denominator,
  numeric value, display value, and source-cell mapping.

## Missing, sorting, and duplicate policies

| Topic | Contract |
| --- | --- |
| Missing participant ID | Excluded from participant counts and reported as a validation error when present in the filtered universe |
| Duplicate participant row | Validation error before calculation |
| Missing categorical value | Excluded and not displayed |
| Missing continuous value | Excluded from the statistic; reflected by the continuous `n` |
| Item order | Explicit Builder order |
| Categorical level order | Runtime ascending or per-item custom raw-value order |
| Continuous statistic order | Fixed shell order |

## Drill-down contract for the future module

Drill-down is required because every populated result cell represents a
traceable participant universe.

- A categorical `n (%)` cell offers numerator participants and denominator
  participants.
- A continuous statistic cell opens source rows with a nonmissing value for the
  selected variable. The `n` cell uses the same nonmissing universe.
- Drill-down uses the saved Dataset Filter and raw typed values, never the
  formatted display text.
- Filtering, sorting, or paging the result must not break cell lookup.
- Closed or stale source/result contexts must produce a clear message rather
  than silently querying a different dataset.

## Configuration and code-generation boundary

The future JSON contract must explicitly store:

- source metadata and Filter AST;
- the ordered mixed-item list and each item type;
- participant-count, denominator, missing, duplicate, sort, and decimal rules;
- the editable column heading;
- the Python reference engine identifier;
- display and drill-down contracts;
- SAS target names.

The SAS generator will accept only that JSON-compatible dictionary and render a
Jinja2 template. It must not receive Qt widgets, SQLite SQL, or an Engine object.
Physical SAS7BDAT and XPT sources may generate SAS. A future Python run may
support Merge sources, but SAS generation remains disabled for Merge until a
separate source contract is approved.

## Shell asset contract

The shell photograph is a visual reference rather than an editable source.
Therefore this phase creates a clean, generic source shell with a working title
and neutral placeholders.

| Artifact | Role |
| --- | --- |
| `docs/baseline-characteristics-shell.docx` | Only editable shell source |
| `clinical_data_viewer/assets/shells/baseline_characteristics.docx` | Byte-identical packaged copy |
| `clinical_data_viewer/assets/shells/baseline_characteristics.png` | Gallery and Builder preview rendered from the final source DOCX |

Any later title, header, shell note, or layout revision must first update the
source DOCX, then replace both packaged assets from that source.

## Shell wording

The source shell uses the following neutral wording until an authoritative
original TFL shell is supplied:

- title: `Baseline Characteristics`;
- subtitle: `Analysis Set`;
- first-column header: `Characteristic`;
- summary-column header: `[Header]` and `(N=xx)`;
- no footnote is displayed;
- shell note: `Only categorical levels represented in the analysis are displayed.`

## Acceptance criteria for this phase

1. The design file records Count, Denominator, Missing, Total, Sorting,
   Decimals, Drill-down, and final table structure.
2. The source and packaged DOCX files are byte-identical.
3. The PNG is rendered from the final source DOCX.
4. The rendered page has intact top, header, and bottom rules with no full-grid
   borders.
5. Item hierarchy, indentation, statistic labels, and placeholders match the
   supplied shell's visible structure.
6. The rendered page has no clipping, overlap, missing glyphs, or broken rules.
7. Existing shell assets, including untracked `proc_means_rows` files, remain
   untouched.


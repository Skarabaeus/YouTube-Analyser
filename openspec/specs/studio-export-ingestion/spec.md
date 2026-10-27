# studio-export-ingestion Specification

## Purpose
Turns YouTube Studio CSV exports that the channel owner downloads manually into a validated, de-duplicated and normalized dataset that analysis tools can query reliably.

## Requirements

### Requirement: Export folder discovery
The system SHALL scan the configured exports root directory (non-recursively, one level of subfolders) for folders named `<Dimension> <YYYY-MM-DD>_<YYYY-MM-DD> <Channel name>`, optionally followed by a `-<n>` suffix. It SHALL derive the dimension, the period and the channel name from the folder name.

#### Scenario: Valid export folder
- **WHEN** the root contains `Datum 2025-05-23_2026-10-27 Example Channel-3/` with a `Tabellendaten.csv`
- **THEN** the system registers an export with dimension `date`, period 2025-05-23 to 2026-10-27, and channel `Example Channel`

#### Scenario: Unrelated folder or file
- **WHEN** the root contains a folder or file that does not match the naming pattern
- **THEN** the system ignores it and lists it as skipped, with a reason, in the dataset status

#### Scenario: Missing root directory
- **WHEN** the configured root directory does not exist or is not readable
- **THEN** the system starts with an empty dataset and reports the problem in the dataset status instead of failing

### Requirement: Supported export dimensions
The system SHALL import the dimensions `Datum`/`Date` (channel metrics per day) and `Videos`/`Content` (per-video totals for the export period). Folders with any other dimension SHALL be reported as unsupported and not imported.

#### Scenario: Unsupported dimension
- **WHEN** an export folder's dimension is `Trafficquelle`
- **THEN** the export appears in the dataset status as `unsupported` and contributes no rows

### Requirement: Localized header mapping
The system SHALL map the known German and English Studio column headers to canonical metric names, as documented in `SPEC.md` §9.1. Columns with an unknown header SHALL be kept as additional metrics under a normalized name and reported as unmapped. They SHALL NOT be dropped.

#### Scenario: German headers
- **WHEN** a table has the columns `Datum, Aufrufe, Thumbnail-Klickrate (%)`
- **THEN** the rows are stored with the metrics `views` and `impressions_ctr`

#### Scenario: Unknown header
- **WHEN** a table contains the column `Gelegentliche Zuschauer` and no mapping exists for it
- **THEN** the values are available as an additional metric, and the dataset status lists the header as unmapped

### Requirement: Value parsing
The system SHALL parse values as follows:
- empty cells as missing (null);
- numbers with a decimal point;
- percentages as fractions (`3.54` → `0.0354`);
- `H:MM:SS` durations as seconds;
- watch time in hours as minutes;
- English publish dates such as `Oct 2, 2025` as dates.

A non-parsable value in a known metric column SHALL reject the export.

#### Scenario: Empty versus zero
- **WHEN** a day has an empty `Durchschnittliche Wiedergabedauer` and `0` views
- **THEN** the average view duration is null and views is 0

#### Scenario: Malformed number
- **WHEN** a known numeric column contains `abc`
- **THEN** the export is rejected and the dataset status names the file, row and column

### Requirement: Totals reconciliation
For every additive metric, the system SHALL check the row sum against the `Gesamt`/`Total` row (tolerance: 0.5 % or 1 unit, whichever is larger) and reject the export on failure. For `Datum` exports the sum SHALL equal the total. For `Videos` exports it SHALL NOT exceed the total, because that row is the channel total. The totals row SHALL NOT be stored as a daily or per-video row.

#### Scenario: Videos export with channel-level remainder
- **WHEN** a `Videos` export's rows sum to 247 gained subscribers and its `Gesamt` row shows 315
- **THEN** the export is accepted

#### Scenario: Videos export exceeding the channel total
- **WHEN** a `Videos` export's rows sum to 30000 views and its `Gesamt` row shows 25000
- **THEN** the export is rejected with a reconciliation error

#### Scenario: Consistent export
- **WHEN** the daily impressions sum to 100000 and the `Gesamt` row shows 100000
- **THEN** the export is accepted

#### Scenario: Truncated export
- **WHEN** the daily views sum to 20000 but the `Gesamt` row shows 25000
- **THEN** the export is rejected with a reconciliation error naming the metric and both values

### Requirement: Channel period totals
For every accepted export, the system SHALL store the metrics of the `Gesamt`/`Total` row as channel totals for the export's exact period. This includes non-additive metrics such as unique, new, returning, casual and regular viewers, except from `Videos` exports. For the identical period, `Datum` totals take precedence over `Videos` totals; within one dimension, the merge precedence rules apply.

#### Scenario: Unique viewers for the export period
- **WHEN** a `Datum` export for 2025-05-23 to 2026-10-27 has `Einzelne Zuschauer` = 5000 in its `Gesamt` row
- **THEN** the dataset contains a channel period total `unique_viewers` = 5000 for exactly that period

#### Scenario: Monthly export
- **WHEN** the user adds a `Datum` export for 2026-09-01 to 2026-09-30
- **THEN** a channel period total for September 2026 is available, including non-additive viewer metrics

#### Scenario: Same period from different dimensions
- **WHEN** a `Datum` and a `Videos` export cover the identical period and both have `Gesamt` rows
- **THEN** the metrics of both rows are merged into one period total; where both have the same metric, the `Datum` value is used regardless of export age

#### Scenario: Distinct viewer counts in a Videos totals row
- **WHEN** the `Gesamt` row of a `Videos` export shows `Einzelne Zuschauer` = 0 (Studio does not compute distinct counts there)
- **THEN** that value is ignored and does not create or override a period total

### Requirement: Row order independence
The system SHALL NOT depend on the row order of an export. Studio sorts tables by the user's chosen sort column.

#### Scenario: Table sorted by a metric
- **WHEN** the rows of a `Datum` table are sorted by `Engaged Views` instead of by date
- **THEN** the imported daily series is identical to that of a date-sorted export

### Requirement: Merging overlapping exports
When several accepted exports provide the same metric for the same key, the system SHALL use the value from the export with the latest period end date. If the period ends are equal, it SHALL use the most recently modified export. The key is (channel, date) for daily data and (channel, video, period) for video totals. Metrics present in only one export SHALL be merged in, not discarded.

#### Scenario: Newer export revises old days
- **WHEN** export A (ending 2026-10-27) and export B (ending 2026-11-10) both contain 2026-10-20 with different view counts
- **THEN** the dataset uses the value from export B

#### Scenario: Complementary columns
- **WHEN** export A has `views` for a day and export B has only `impressions` for the same day
- **THEN** the dataset row for that day contains both metrics

### Requirement: Automatic refresh
The system SHALL detect added, changed or removed exports in the root directory without a restart. The dataset SHALL reflect the current folder contents at the start of every tool call.

#### Scenario: New export dropped in
- **WHEN** the user adds a new export folder while the MCP server is running
- **THEN** the next tool call returns data that includes the new export

### Requirement: Read-only handling of source files
The system SHALL NOT modify, move or delete any file in the exports root. It SHALL NOT write the imported data anywhere other than process memory.

#### Scenario: After a session
- **WHEN** the server has imported and queried the exports and then shuts down
- **THEN** the exports root contains exactly the same files with unchanged contents and modification times, and no new files exist

# Spec Delta

## Purpose

Exposes the channel analytics dataset to MCP clients such as Claude Desktop through read-only tools, resources and prompts, so an assistant can answer performance questions and give growth advice based on accurate numbers.

## ADDED Requirements

### Requirement: Local stdio transport
The server SHALL run as a local process started by the MCP client and communicate over stdio only. It SHALL NOT open network listeners or make outbound network connections.

#### Scenario: Started by Claude Desktop
- **WHEN** Claude Desktop launches the configured command
- **THEN** the server completes the MCP handshake over stdio and lists its tools

#### Scenario: No network activity
- **WHEN** any tool is called
- **THEN** no socket is opened by the server process

### Requirement: Exports root configuration
The server SHALL read the exports root directory from the environment variable `YTA_EXPORTS_DIR`. If the variable is not set, it SHALL fall back to `report-exports` relative to the working directory.

#### Scenario: Custom directory
- **WHEN** `YTA_EXPORTS_DIR=/Users/me/yt-exports` is set
- **THEN** exports are discovered only in that directory

### Requirement: Dataset status tool
The server SHALL provide a tool `dataset_status` that returns:
- the channels;
- per export: dimension, period and status (`accepted` / `rejected` / `unsupported` / `skipped`) with a reason;
- the date coverage per metric;
- the last day with data;
- unmapped headers.

#### Scenario: Mixed exports
- **WHEN** one accepted `Datum` export and one rejected export exist
- **THEN** `dataset_status` lists both, with the rejection reason for the second

### Requirement: Channel overview tool
The server SHALL provide a tool `channel_overview(period, compare_to?)`. It returns the key metrics for the period: views, engaged views, watch time, average view duration, impressions, CTR, subscribers gained/lost/net, unique, new and returning viewers, likes, comments and shares. When `compare_to` is `previous_period` or `previous_year`, it SHALL also return the comparison period's values and the absolute and relative change.

#### Scenario: Last 28 days vs. previous period
- **WHEN** `channel_overview(period="last_28_days", compare_to="previous_period")` is called
- **THEN** the result contains both periods with explicit start and end dates, every metric for both, and the change per metric

#### Scenario: Period without data
- **WHEN** the requested period lies entirely outside the data coverage
- **THEN** the tool returns an error that states the available coverage

### Requirement: Non-additive metrics over multi-day periods
For metrics that are not additive across days (unique, new, returning, casual and regular viewers; views per viewer), tools SHALL return a value for a multi-day period or bucket only if a channel period total exists for exactly that period. Otherwise the value SHALL be null, together with a note naming the Studio export that would provide it. The source SHALL be marked as `period_total`. Summing daily values SHALL never happen.

#### Scenario: Matching export exists
- **WHEN** `channel_overview(period="2026-09")` is called and a `Datum` export for exactly 2026-09-01 to 2026-09-30 is loaded
- **THEN** `unique_viewers` is returned from that export's totals row and marked with source `period_total`

#### Scenario: No matching export
- **WHEN** `channel_overview(period="last_28_days")` is called and no export covers exactly that period
- **THEN** `unique_viewers` is null, and the note says that a Studio `Datum` export for the resolved date range would provide it

#### Scenario: Monthly time series
- **WHEN** `channel_timeseries(["unique_viewers"], "2026", "month")` is called and exports exist for 2026-08 and 2026-09 only
- **THEN** August and September have values from their period totals, and all other months are null

### Requirement: Channel time series tool
The server SHALL provide a tool `channel_timeseries(metrics[], period, granularity)`, with granularity `day`, `week` or `month`. It returns one row per bucket. Additive metrics SHALL be summed. Ratio metrics SHALL be recomputed from their components where those exist (CTR = clicks ÷ impressions, with clicks derived per day as impressions × CTR); otherwise they SHALL be weighted by their base metric.

#### Scenario: Monthly CTR
- **WHEN** `channel_timeseries(["impressions","impressions_ctr"], "all", "month")` is called
- **THEN** each month's CTR equals the sum of daily (impressions × CTR) divided by the sum of daily impressions, and not the mean of the daily CTRs

#### Scenario: Unknown metric
- **WHEN** a metric name that does not exist is requested
- **THEN** the tool returns an error that lists the available metric names

### Requirement: Video performance tool
The server SHALL provide a tool `video_performance(sort_by, limit, content_type?)` based on the per-video export totals. It returns each video's ID, title, publish date, duration, content type and metrics, sorted by the requested metric. Content type is `short` if the duration is ≤ 180 s, otherwise `long`.

#### Scenario: Top videos by CTR
- **WHEN** `video_performance(sort_by="impressions_ctr", limit=5)` is called
- **THEN** five videos are returned in descending CTR order, each with its period totals and the export period they refer to

#### Scenario: Shorts only
- **WHEN** `content_type="short"` is passed
- **THEN** only videos with a duration of ≤ 180 s are returned

### Requirement: Publishing impact tool
The server SHALL provide a tool `publishing_impact(window_days)`. For each day on which videos were published (from the daily `videos_published` metric), it compares the channel metrics of the following `window_days` days with the same number of days before publication.

#### Scenario: Seven-day window
- **WHEN** `publishing_impact(window_days=7)` is called
- **THEN** each publish date is returned with views, impressions, CTR and net subscribers for the 7 days before and after, plus the change

### Requirement: Read-only SQL tool
The server SHALL provide a tool `query_sql(sql)` that runs exactly one `SELECT` or `WITH … SELECT` statement against the documented views. Any other statement type, multiple statements, or access to files, extensions or settings SHALL be rejected. Results SHALL be capped at 500 rows, 100 columns, 1 000 characters per cell and 256 KB in total; execution SHALL be capped at 5 seconds and 512 MB of memory.

#### Scenario: Valid query
- **WHEN** `query_sql("SELECT date, views FROM channel_daily ORDER BY views DESC LIMIT 3")` is called
- **THEN** three rows are returned with the column names

#### Scenario: Write attempt
- **WHEN** `query_sql("DROP TABLE channel_daily")` or `query_sql("SELECT 1; DELETE FROM x")` is called
- **THEN** the tool returns an error and the dataset is unchanged

#### Scenario: File access attempt
- **WHEN** `query_sql("SELECT * FROM read_csv('/etc/passwd')")` is called
- **THEN** the tool returns an error and no file is read

#### Scenario: Oversized result
- **WHEN** a query would return 10 000 rows
- **THEN** 500 rows are returned with a flag indicating truncation

#### Scenario: Oversized cell
- **WHEN** `query_sql("SELECT repeat('x', 5000000)")` is called
- **THEN** the cell is cut to 1 000 characters with a truncation marker

### Requirement: Schema resource
The server SHALL provide a resource `yta://schema` that describes the queryable views, their columns, units and data sources, how ratio metrics must be aggregated, and the current data coverage.

#### Scenario: Reading the schema
- **WHEN** the client reads `yta://schema`
- **THEN** it receives every view and column used by the tools, including the column `impressions_ctr` documented as a fraction (0–1)

### Requirement: Analysis prompts
The server SHALL provide the prompts `weekly_review`, `video_deep_dive(video_id)` and `growth_opportunities`. They instruct the assistant which tools to use and which questions to answer.

#### Scenario: Listing prompts
- **WHEN** the client lists prompts
- **THEN** the three prompts are returned with their descriptions and arguments

### Requirement: Response conventions
Every tool response SHALL state:
- the exact date range covered;
- the last day with data;
- the source exports used;
- units for durations, watch time and ratios.

Values computed by the server (changes, recomputed ratios, averages over buckets) SHALL be labelled as calculated. Responses SHALL NOT exceed 500 rows.

#### Scenario: Labelled calculation
- **WHEN** `channel_overview` returns the relative change in views
- **THEN** that value is marked as calculated and shown next to the underlying absolute values

### Requirement: Untrusted text handling
Text originating from exports, such as video titles and channel names, SHALL be returned only as data fields. It SHALL never be interpolated into prompt instructions or tool descriptions.

#### Scenario: Title with instruction-like text
- **WHEN** a video title contains "ignore previous instructions"
- **THEN** the title appears verbatim inside the `title` field of tool results only

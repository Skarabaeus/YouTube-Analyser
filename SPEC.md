# YouTube Analyser — Specification

Status: **v0.4** (2026-10-27). Decisions are in §13. This document is the input for the OpenSpec changes; once a change is archived, `openspec/specs/` is the source of truth and this document is kept as context.

**Current path: local MCP.** v1 is a **local stdio MCP server over YouTube Studio CSV exports** (OpenSpec change `add-local-export-mcp`). An automated API pipeline on AWS is a *possible later phase*. It is described in this document only as a target architecture (marked **Later phase**), and is built only if the export-based workflow proves too limited (§14).

## 1. Goal

Help the owner grow their YouTube channel. Claude Desktop analyses the channel's performance through an MCP server and gives growth advice based on accurate numbers.

### Scope v1
- **One owner, one channel** (the owner's own channel).
- The owner exports CSVs from YouTube Studio (*Datum* and *Videos* tabs) into a local, git-ignored folder (`report-exports/` by default).
- A local stdio MCP server, started by Claude Desktop, reads, validates and merges these exports in memory (DuckDB) and offers analysis tools (§8).
- No Google OAuth, no AWS, no persistent storage, no network access, no running costs.

### Advantages over pasting CSVs into a chat
- Numbers are computed with SQL rather than estimated by the model, and ratios such as CTR are aggregated correctly.
- Every export is checked against its totals row before it is used.
- Overlapping and newer exports are merged automatically.
- Only the numbers a question needs enter the conversation, which keeps the context small.

### Later phases
- Daily automated ingestion via YouTube Analytics API, Data API and Reporting API on AWS (§3–§7, §9.2).
- Data that Studio exports cannot provide, e.g. per-video daily series, retention curves, and longer history without manual exports.

### Designed for, but not built in v1
- **Multiple owners (tenants)** and multiple channels per owner. The pipeline's data model, storage paths, secrets and IAM are tenant-scoped from day one (§6.3). The v1 server already keeps a channel column in every table.
- **Monetary metrics** (channel not monetised yet).
- **Remote MCP** for claude.ai web/mobile.

### Non-goals
- No web UI / dashboards.
- No write access to YouTube.
- No real-time data (YouTube data lags 24–72 h).
- No Content Owner (CMS / MCN) reports.

## 2. Actors & Concepts

| Term | Meaning |
|---|---|
| **Owner** | The person whose channel is analysed. v1 has exactly one. |
| **Channel** | A YouTube channel. v1 takes the channel name from the export folder name. |
| **Export** | One YouTube Studio CSV download: a folder named `<Dimension> <start>_<end> <Channel>` (§9.1). |
| **Exports root** | The folder the MCP server scans (`YTA_EXPORTS_DIR`, default `report-exports/`). |
| **Period total** | An export's totals row (`Gesamt`). It is the only valid source for distinct counts (e.g. unique viewers) over a multi-day period. |
| **Source** | Origin of a value: `export` (as exported), `period_total` (from a totals row), `calculated` (computed by the server). |

**Later phase only:**

| Term | Meaning |
|---|---|
| **Tenant** | An owner of data (a person/customer). The pipeline starts with exactly one: `default`. |
| **Account** | A Google account that granted OAuth consent. Belongs to one tenant. |
| **Grant** | An OAuth refresh token, bound to **one channel**. Google asks which channel/Brand Account to use at consent time, so each channel is onboarded separately. |
| **Ingestion run** | One execution of the daily pull for one channel. |

## 3. Data Sources

### v1: YouTube Studio exports
| Export | Content | Status |
|---|---|---|
| **Datum** (Date) table | Channel metrics per day, including impressions and CTR, plus a totals row for the period. | Required, imported |
| **Videos** (Content) table | Per-video totals for the export period, with title, publish date and duration. | Recommended, imported |
| Other dimensions (traffic source, geography, …) | Breakdowns | Not imported; listed as `unsupported` |
| Chart files (`Diagrammdaten.csv`, `Gesamtwerte.csv`) | Daily series of a single chart metric | Not imported |

Formats and rules: §9.1. How to export: README.

### Later phase: YouTube APIs
| API | Used for | Notes |
|---|---|---|
| **YouTube Analytics API v2** (`reports.query`) | Channel- and video-level metrics per day, traffic sources, demographics, geography, devices, retention. | Scope `yt-analytics.readonly`. Supports historical queries, used for a 24-month backfill. |
| **YouTube Data API v3** | Video inventory and metadata (title, publish time, duration, tags, thumbnail, Shorts vs. long-form), channel metadata, lifetime counters. | Scope `youtube.readonly`. Daily sync uses ≪ 100 of the 10 000 default quota units. |
| **YouTube Reporting API v1** | **Impressions and impression CTR** per video per day. | Report types `channel_reach_basic_a1` (date, video → `video_thumbnail_impressions`, `video_thumbnail_impressions_ctr`) and `channel_reach_combined_a1` (adds traffic source, device, OS). Scope `yt-analytics.readonly`. |
| **Manual import** | Gaps the APIs cannot fill, mainly impressions/CTR before the Reporting API's history window. | The same Studio exports as in v1. |

### 3.1 Data characteristics that drive the design
- **Latency & revisions:** data for day *D* arrives after ~2–3 days and can be revised.
  - **v1:** newer exports replace older ones where they overlap (§9.1).
  - **Later phase:** the daily job re-fetches a trailing window of 7 days and upserts idempotently.
- **History:**
  - **v1:** whatever the exports cover. A *Gesamte Laufzeit* (lifetime) export covers everything.
  - **Later phase:** a 24-month backfill via the Analytics API (configurable).
- **Distinct counts** (unique, new, returning viewers): Studio computes them per period. They cannot be added up across days, so multi-day values come only from a totals row for exactly that period.
- **Reporting API timing (later phase):**
  - A newly created job generates historical reports only for the **30 days before job creation**.
  - Reports can be downloaded for **60 days** after generation (historical ones for 30 days).
  - YouTube may issue backfill reports that replace earlier ones.
  - ⇒ Create the reporting jobs early if this phase is likely (`docs/setup-google-youtube.md`).
- **Volume:** small (one channel ≈ < 100k rows/year). Query flexibility, not storage, decides the engine.

## 4. Metrics

### v1 (from Studio exports)
The registry in `src/yta/metrics.py` is the source of truth for names, units and aggregation. The header mapping is in §9.1.

| Aggregation | Metrics |
|---|---|
| **Sum** over days | views, engaged views, watch time (minutes), subscribers gained / lost / net, likes, dislikes, shares, comments, videos published / added, impressions, co-viewing views, hypes, hype points |
| **Ratio**, recomputed from components | impressions CTR (clicks ÷ impressions, with clicks = impressions × CTR per day), average view duration (watch time ÷ views), likes ratio |
| **Weighted** by views | average view percentage |
| **Period total only** (distinct counts) | unique viewers, unique reach, new / returning / casual / regular viewers, views per viewer, watched to the end (%) |

- **Channel metrics per day:** from *Datum* exports.
- **Per-video totals for an export period:** from *Videos* exports.

### Later phase
- **Channel, per day:** `views, engagedViews (if available), estimatedMinutesWatched, averageViewDuration, averageViewPercentage, subscribersGained, subscribersLost, likes, dislikes, comments, shares, videosAddedToPlaylists, videosRemovedFromPlaylists`.
- **Video, per day:** same core metrics with dimensions `day,video`.
- **Reach, per video per day:** `impressions, impressions_ctr`, optionally by traffic source and device.
- **Breakdowns:** traffic sources, geography, devices / OS, demographics, subscribed status, content type.
- **Per-video snapshots:** audience retention curves; current metadata (history limited by policy, §7).
- **Future:** monetary metrics (`estimatedRevenue, cpm, …`), scope `yt-analytics-monetary.readonly`.

## 5. Architecture

### v1: local MCP
```
 YouTube Studio ──(manual CSV download)──► report-exports/   (local, git-ignored)
                                                 │ read-only scan on every tool call
                                                 │ (rebuilt only when files change)
                                                 ▼
 Claude Desktop ──stdio──► yta-mcp ── in-memory DuckDB (sandboxed, no file/network access)
```
- Claude Desktop starts `yta-mcp` as a subprocess (`uv run --directory <repo> yta-mcp`).
- On each tool call the server checks a fingerprint of the exports root. If it changed, the dataset is rebuilt. If the rebuild fails, the previous dataset stays active and the error is reported in `dataset_status`.
- Ingestion pipeline: discovery → parsing and validation → reconciliation against the totals row → merge → DuckDB tables and views.

### Later phase: AWS pipeline
```
                      ┌─────────────────────── AWS eu-central-1 ──────────────────────┐
                      │                                                               │
 EventBridge Scheduler│ ─(daily 06:00 UTC)─► Lambda "dispatcher"                      │
                      │                        │ one message per (tenant, channel)    │
                      │                        ▼                                      │
                      │                    SQS queue ──► Lambda "ingest" ─────────────┼──► YouTube APIs
                      │                     (+ DLQ)          │                        │
                      │                                      ├─► SSM SecureString     │
                      │                                      │   (refresh tokens)     │
                      │                                      ▼                        │
                      │                          S3 bucket (Parquet + raw)            │
                      │                                      ▲                        │
                      │  Lambda "retention" (daily) ─────────┘  (policy enforcement)  │
                      │  CloudWatch alarms ──► SNS ──► e-mail                         │
                      └──────────────────────────────────────▲────────────────────────┘
                                                             │ read-only (AWS SSO, short-lived creds)
 Laptop ── Claude Desktop ──stdio──► MCP server (local, DuckDB)
 Laptop ── `yta` CLI ──► onboarding (OAuth loopback), manual import, offboarding/purge
```

#### 5.1 Ingestion (later phase)
- EventBridge Scheduler + Lambda (arm64, Python).
- Fan-out via SQS: one message per channel, with the backfill chunked into month-sized messages. This keeps each invocation well under the 15-min limit and isolates failures.
- Rejected alternatives:
  - GitHub Actions cron: secrets would be split across systems.
  - Laptop cron: it only runs when the laptop is on.

#### 5.2 Secrets (later phase)
- Google OAuth client ID/secret: SSM SecureString `/yta/oauth/client`.
- Refresh tokens: SSM SecureString `/yta/tenants/<tenant>/channels/<channelId>/grant`, encrypted with the AWS-managed SSM key.
- IAM: only the ingest role may read grants, and only the onboarding identity may write them. The MCP identity has **no** access to grants.
- The Google OAuth consent screen must be **In production**. In *Testing* status, refresh tokens expire after 7 days. For personal use (< 100 users) no Google verification is required.
- `invalid_grant` → channel status `needs_reauth`, ingestion stops, e-mail alert, retention clock starts (§7).

## 6. Storage

### v1: no persistent storage
- The exports in `report-exports/` are the only stored data. They are the owner's files, git-ignored, and the server only reads them.
- The server loads them into an **in-memory** DuckDB database on start and on change. Nothing is written to disk.

| Table / view | Content |
|---|---|
| `exports` | Every discovered export folder: dimension, period, status, reason |
| `channel_daily_long` | One row per channel, date and metric (long format, so new Studio columns need no schema change) |
| `channel_daily` (view) | Wide daily table, plus the derived `impressions_clicks` |
| `video_metrics` / `video_totals` (view) | Per-video totals for the latest *Videos* export period |
| `video_meta` | Title, publish date, duration, content type (`short` ≤ 180 s) |
| `channel_period_totals` | Totals rows per export period; *Datum* wins over *Videos* |

`yta://schema` documents the views for the model.

### Later phase: S3 + Parquet, queried with DuckDB
DynamoDB was rejected. It costs about the same (≈ $0 at this volume), but it cannot do the aggregations, joins and time-range analyses the MCP tools need. S3 + Parquet + DuckDB gives full SQL at near-zero cost and no running database. Neither option exposes a database endpoint to the internet.

#### 6.2 Layout (later phase)
```
s3://yta-data-<aws-account-id>-euc1/
  raw/tenant=<t>/channel=<c>/<source>/<endpoint>/fetched=<ts>.json.gz   # verbatim API responses, 25-day lifecycle
  manual/tenant=<t>/channel=<c>/<import-id>/original.csv               # uploaded Studio exports (user data)
  curated/<table>/tenant=<t>/channel=<c>/month=<YYYY-MM>/data.parquet
```
- Curated partitions are **rewritten per (tenant, channel, month)**. This makes upserts and deletions idempotent without a database.
- Bucket: SSE-S3, Block Public Access, versioning (noncurrent versions expire after 7 days, so deletions are effective — see §7), TLS-only bucket policy.

#### 6.3 Multi-tenancy readiness (later phase)
- Every table has `tenant_id` as the leading key column, and every S3 and SSM path is prefixed by tenant.
- IAM policies are written with tenant prefixes, so per-tenant roles can be added later without moving data.
- The MCP server is configured with a tenant scope and every query is filtered by it. The scope starts fixed to `default`.
- Opening up to other users requires (out of scope): Google OAuth verification of sensitive scopes, a privacy policy, a self-service onboarding flow, per-tenant MCP authentication (remote MCP), and possibly a YouTube API compliance audit / quota extension.

#### 6.4 Logical data model (later phase)

| Table | Key | Content | Retention class (§7) |
|---|---|---|---|
| `tenants` | tenant_id | name, created_at | user data |
| `channels` | tenant_id, channel_id | title, handle, status (`active`/`needs_reauth`/`revoked`/`disabled`), onboarded_at, backfill_from, last_auth_verified_at, reauth_lost_at | mixed: title = metadata |
| `videos` | tenant_id, channel_id, video_id, valid_from | title, description, tags, duration_s, published_at, content_type, thumbnail_url, privacy_status | **metadata (30 d)** |
| `channel_daily` | tenant_id, channel_id, date, source | channel metrics | statistics |
| `video_daily` | tenant_id, channel_id, video_id, date, source | video metrics | statistics |
| `video_reach_daily` | tenant_id, channel_id, video_id, date, source, [traffic_source, device_type, os] | impressions, impressions_ctr | statistics |
| `traffic_source_daily` | tenant_id, channel_id, date, source_type, [video_id] | views, watch time | statistics |
| `geo_daily`, `device_daily` | … , date, dimension | views, watch time, subs | statistics |
| `demographics_period` | tenant_id, channel_id, period_start, period_end, age_group, gender | viewer_percentage | statistics |
| `video_retention` | tenant_id, channel_id, video_id, snapshot_date, elapsed_ratio | audience_watch_ratio, relative_retention | statistics |
| `annotations` | tenant_id, channel_id, [video_id], date, annotation_id | free-text notes by the owner | user data |
| `video_period_totals` | tenant_id, channel_id, video_id, period_start, period_end, source | per-video totals from manual Studio exports | statistics (user data) |
| `studio_metrics_daily` | tenant_id, channel_id, [video_id], date, metric | value (Studio-only metrics from manual exports) | user data |
| `manual_imports` | tenant_id, import_id | file name, type, date range, rows, imported_at | user data |
| `ingestion_runs` | tenant_id, channel_id, run_id | started/finished, status, rows, error | operational |
| `reporting_jobs` | tenant_id, channel_id, report_type | job_id, created_at, last_report_downloaded | operational |

All metric rows carry `source` and `fetched_at`. When API and manual data overlap for the same key, **API data wins**. Manual rows are kept with `source=manual_import`.

## 7. Data Retention & YouTube API Policy Compliance

### 7.0 v1
- Studio CSV exports are the owner's own data, downloaded manually from Studio. They are not retrieved through the YouTube APIs, so the API Services Developer Policies below do not apply.
- The exports stay on the owner's machine, in a git-ignored folder. They are never committed, uploaded or copied by the server.
- The server holds data in memory only. Deleting an export folder removes its data on the next tool call.
- Test fixtures are synthetic and anonymised.
- Calculated values are still labelled `calculated` in MCP output (§8.4), as good practice and so the later phase can reuse the same conventions.

### 7.1 Later phase: what the policies say
Checked against the [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) on 2026-10-27. Re-check before the API pipeline is built.

| Rule | Section | Applies to |
|---|---|---|
| Authorized Data from the **YouTube Analytics API** and **Reporting API**, and statistics (view/subscriber counts) from other YouTube APIs, may be stored **"for as long as is necessary"**. However, the client must confirm **every 30 days** that the user still authorizes access. | III.E.4.b | All metrics (fetched with the owner's OAuth consent) |
| **Other Authorized Data** (e.g. titles, descriptions, tags, thumbnails): store **≤ 30 calendar days**, then refresh or delete. | III.E.4.c | `videos`, channel title/handle, raw responses |
| Use reasonable efforts to keep stored data consistent with current API data, and show the most current data. | III.E.4.e/f | All |
| Do not replace API data with independently calculated data or create new/derived metrics. A separately calculated figure may only be shown next to the API figure, clearly labelled. | III.E.4.h | MCP outputs |
| A user must be able to request deletion of their stored data. Deletion happens within **7 days**. | III.E.4.g | Offboarding / purge |
| Revocation in the client → delete all Authorized Data within **7 days**. Revocation via Google account settings → delete within **30 days**; tokens must be re-validated periodically. | III.D.2.3 | Grants |
| [Derived-metrics & storage amendment](https://developers.google.com/youtube/terms/derived-metrics-policy) (III.L, since 2026-06-01): audited analytics developers who apply for it may create derived metrics and keep statistics for up to 36 months. | III.L | Not applicable unless the product is opened to others. |

**Result:** long-term storage of the analytics metrics **is allowed**, provided authorization is re-confirmed at least every 30 days. Metadata such as titles is limited to 30 days unless refreshed.

### 7.2 Later phase: how the pipeline complies
1. **Authorization re-confirmation:** every daily run refreshes the access token and calls `channels.list(mine=true)`. On success it sets `channels.last_auth_verified_at`. MCP responses flag the data as stale if that is older than 30 days.
2. **Metadata freshness:**
   - `videos` is refreshed daily.
   - Superseded metadata versions (`valid_to` older than 30 days) are deleted by the retention Lambda.
   - Metadata of videos no longer returned by the API is deleted after 30 days. Their metrics stay (keyed by `video_id`).
   - Title/thumbnail change history beyond 30 days lives in `annotations`, written by the owner.
3. **Raw API responses:** the S3 lifecycle rule deletes them after 25 days.
4. **Revocation handling:**
   - On `invalid_grant`, the channel moves to `needs_reauth`, and `reauth_lost_at` is set.
   - An e-mail alert goes out on day 0, day 14 and day 21.
   - If the channel is not re-authorized by **day 25**, the retention Lambda purges all API-derived data of that channel (5-day safety margin to the 30-day limit).
5. **Offboarding / deletion request:** `yta offboard <channel>` revokes the token at Google and deletes the SSM grant. It then purges all of the channel's data **immediately**, and noncurrent S3 versions expire within 7 days.
6. **Derived figures in MCP output:** API figures are returned as-is; computed values are labelled `calculated` and shown next to the underlying figures. Derived metrics are never stored as if they came from the API.

## 8. MCP Server

### 8.1 Hosting — local stdio
Claude Desktop starts the server as a subprocess over stdio. No port is opened and no network connection is made.
- **v1:** the server reads the local exports root (`YTA_EXPORTS_DIR`).
- **Later phase:** the same server queries S3 directly with DuckDB, using a **read-only** AWS profile from IAM Identity Center (short-lived credentials, no stored keys).
- **Remote MCP** (Lambda Function URL + OAuth 2.1, ~$0/month) stays an option for claude.ai web/mobile. Tool logic is kept separate from transport so it can be deployed either way.

### 8.2 Tools (v1)

| Tool | Purpose |
|---|---|
| `dataset_status` | Channels; every export with dimension, period, status (`accepted` / `rejected` / `unsupported` / `skipped`) and reason; coverage per metric; last day with data; unmapped headers. |
| `channel_overview(period, compare_to?)` | Key metrics for a period, optionally vs. `previous_period` or `previous_year`, with absolute and relative change. |
| `channel_timeseries(metrics, period, granularity)` | Metrics per `day`, `week` or `month`. Sums are summed, ratios recomputed from components, distinct counts only from exact period totals. |
| `video_performance(sort_by, limit, content_type?)` | Videos of the latest *Videos* export, ranked by a metric; `short` (≤ 180 s) or `long`. |
| `publishing_impact(window_days)` | Channel metrics in the `window_days` after each publish day vs. the same number of days before. |
| `query_sql(sql)` | One read-only `SELECT` over the documented views (§8.5). |

**Periods:** `all`, `last_N_days` (e.g. `last_28_days`), `YYYY`, `YYYY-MM`, `YYYY-MM-DD..YYYY-MM-DD`. Relative periods end at the **last day with data**, not today.

**Later phase (planned, need API data):** `top_videos` with per-day ranges, `video_details`, `video_timeseries`, `compare_videos(days_since_publish)`, `reach` with traffic-source/device breakdown, `retention_curve`, `traffic_sources`, `audience`, `publishing_patterns` (weekday/hour), `annotations`.

### 8.3 Resources & prompts
- **v1:**
  - Resource `yta://schema`: views, columns, units, aggregation rules, current coverage.
  - Prompts: `weekly_review`, `video_deep_dive(video_id)`, `growth_opportunities`.
- **Later phase:**
  - Resource `yta://channels/<id>/summary`: rolling 28-day summary.
  - Prompts: `content_ideas_from_top_performers`, `thumbnail_title_review(video)`.

### 8.4 Output conventions
- Every response states the explicit date range, the last day with data, the source exports and units.
- Each value is marked with its source: `export`, `period_total` or `calculated` (§2).
- Responses are capped at 500 rows.
- Text from exports (titles, channel names) is returned only as data fields, never placed into prompts or tool descriptions.

### 8.5 Security (v1)
- **Read-only:** export files are only read. Symbolic links inside the exports root are not followed.
- **Input limits:** 20 MB per file, 10 000 characters per cell. Malformed files are rejected with a reason, never partially imported.
- **`query_sql` sandbox:**
  - Exactly one `SELECT` / `WITH … SELECT` statement.
  - DuckDB runs with external access, extension autoload/autoinstall disabled and its configuration locked.
  - Limits: 5 s, 512 MB of memory, 2 threads.
  - Results capped at 500 rows, 100 columns, 1 000 characters per cell and 256 KB in total.
- **No network:** stdio only; a test asserts that no socket other than the local event-loop pair is opened.

## 9. Imports and CLI

### 9.1 Studio export formats (v1)
Pinned against real sample exports (German Studio UI, 2026-10-27; they live in the git-ignored `report-exports/` folder). The implemented source of truth for header mapping, units and aggregation is `src/yta/metrics.py`.

**Export folder structure:** `<Dimension> <YYYY-MM-DD>_<YYYY-MM-DD> <Channel name>[-n]/` (`-n` is added by the browser for repeated downloads), containing:

| File (DE) | File (EN) | Content |
|---|---|---|
| `Tabellendaten.csv` | `Table data.csv` | One row per dimension value, **all columns selected in the table**. The first data row `Gesamt`/`Total` holds the period totals. |
| `Diagrammdaten.csv` | `Chart data.csv` | Daily series per dimension value, but **only for the single chart metric** and **only for the rows selected in the chart** (default: top 5). Not imported. |
| `Gesamtwerte.csv` | `Totals.csv` | Daily totals of the chart metric. Not imported. |

**Parsing rules (observed):**
- UTF-8 without BOM, comma separator, `"`-quoted fields.
- Decimal point; no thousands separator.
- Dates are `YYYY-MM-DD`. The publish date is English `"Oct 2, 2025"` even in the German UI.
- `Dauer` is in seconds. `Durchschnittliche Wiedergabedauer` is `H:MM:SS`, and empty when there were no views.
- `Thumbnail-Klickrate (%)` is a percentage, stored as a fraction.
- Date-dimension tables omit some zero days. They are sorted by the **table's sort column** (e.g. *Engaged Views*), not by date, so the importer must not assume any order.
- An empty cell means *no data* and is stored as NULL. `0` is a real zero.
- `Abonnenten` is net subscribers (`Gewonnene` − `Verlorene`).
- **Reconciliation:** for *Datum* exports, the sum of all rows must equal the `Gesamt` row for additive metrics (views, watch time, impressions, likes, …). For *Videos* exports the sum must not exceed it, because their `Gesamt` row is the channel total and includes activity not attributable to listed videos (e.g. 247 vs. 315 subscribers gained in the real sample). Otherwise the export is rejected.
- In *Videos* exports, Studio leaves distinct viewer counts empty per video and `0` in the `Gesamt` row. These zeros are ignored.

**Header mapping (DE → canonical; EN variants are mapped too):**

| Header (DE) | Canonical column |
|---|---|
| `Datum` | `date` |
| `Videos` | `video_id` |
| `Videotitel` | `title` |
| `Veröffentlichungszeitpunkt des Videos` | `published_at` |
| `Dauer` | `duration_s` |
| `Aufrufe` | `views` |
| `Wiedergabezeit (Stunden)` | `watch_time_hours` → stored as minutes |
| `Durchschnittliche Wiedergabedauer` | `avg_view_duration_s` |
| `Abonnenten` | `subscribers_net` |
| `Thumbnail-Impressionen` | `impressions` |
| `Thumbnail-Klickrate (%)` | `impressions_ctr` |
| `Engaged Views` | `engaged_views` |
| `Durchschnittliche Wiedergabedauer in Prozent (%)` | `avg_view_percentage` |
| `Gewonnene Abonnenten` / `Verlorene Abonnenten` | `subscribers_gained` / `subscribers_lost` |
| `Likes` / `„Mag ich nicht“-Bewertungen` | `likes` / `dislikes` |
| `Geteilte Inhalte` | `shares` |
| `Kommentare hinzugefügt` | `comments` |
| `Veröffentlichte Videos` / `Hinzugefügte Videos` | `videos_published` / `videos_added` |
| `Likes und Dislikes im Vergleich (%)` | `likes_ratio` (recomputed from likes/dislikes when aggregated) |
| `Einzelne Zuschauer` / `Unique Reach` | `unique_viewers` / `unique_reach` |
| `Neue Zuschauer` / `Wiederkehrende Zuschauer` | `new_viewers` / `returning_viewers` |
| `Gelegentliche Zuschauer` / `Regelmäßige Zuschauer` | `casual_viewers` / `regular_viewers` |
| `Aufrufe pro Zuschauer (Durchschnitt)` | `views_per_viewer` |
| `Aufrufe (Co-Viewing)` | `coviewing_views` |
| `Bis zum Ende angesehen (%)` | `watched_to_end_pct` |
| `Hypes` / `Hype-Punkte` | `hypes` / `hype_points` |

**Distinct counts** (unique, new, returning, casual, regular viewers; views per viewer; unique reach; watched to the end) are not additive across days. For multi-day periods they come only from an export's `Gesamt` row for exactly that period. For monthly values, the owner adds one *Datum* export per calendar month.

Unknown headers are reported and kept as `x_<header>` metrics, queryable via SQL. A missing key column (`Datum`/`Videos`), no known metric, or a missing totals row fails the export.

**Import types and target tables (v1, in memory, §6):**
1. *Datum* table → `channel_daily_long` / `channel_daily`.
2. *Videos* table → `video_metrics` / `video_totals` and `video_meta` (`period_start`, `period_end` from the folder name). Never mixed into daily sums.
3. `Gesamt` rows of both → `channel_period_totals`. For the same period, *Datum* wins over *Videos*.
4. Overlapping exports of the same kind: the one with the later period end wins, then the newer file.

In the later pipeline, the same imports feed `video_period_totals`, `channel_daily` (`source=manual_import`) and `studio_metrics_daily` (§6.4).

**Optional extra exports** (`docs/setup-google-youtube.md` §5): per-video daily or monthly CTR (exports C/D) are not needed for v1.

### 9.2 CLI `yta` (later phase)
v1 has no CLI: the owner drops exports into the folder, and the MCP server picks them up. The pipeline would add:

| Command | Purpose |
|---|---|
| `yta onboard [--tenant default]` | OAuth loopback flow (`access_type=offline`, `prompt=consent`). Resolves the channel, stores the grant, creates Reporting API jobs if missing, enqueues the 24-month backfill. |
| `yta import studio <file.csv\|zip> --channel <id>` | Validates and imports a Studio export (§9.1), uploads the original to `manual/`, writes curated rows with `source=manual_import`. Dry-run by default, `--apply` to commit. |
| `yta annotate <channel> [--video <id>] --date <d> "<text>"` | Adds an annotation. |
| `yta backfill <channel> --from <date>` | Extends history. |
| `yta status` | Channels, runs, reporting jobs, auth freshness. |
| `yta offboard <channel>` / `yta purge <channel>` | Revoke + delete (§7.2). |

Write operations stay in the CLI, which uses a separate, write-capable AWS role. The MCP server stays strictly read-only.

## 10. Non-functional Requirements

### v1
- **Cost:** $0 (runs locally).
- **Security:** read-only, no network, sandboxed SQL, input size limits (§8.5); AI security review of every commit before it is pushed (`INSTRUCTIONS.md`).
- **Correctness:** exports are reconciled against their totals rows; ratios are recomputed, never averaged; distinct counts are never summed.
- **Freshness:** new exports are picked up on the next tool call, without a restart.
- **Robustness:** a broken export is rejected with a reason, and the rest of the dataset stays usable.
- **Privacy:** real exports are never committed (`.gitignore`); test fixtures are synthetic.

### Later phase
- **Cost:** target < $1/month for one channel (§11).
- **Reliability:** per-channel isolation; 3 retries with backoff; DLQ. Alarms go to SNS e-mail on DLQ depth > 0, no successful run in 48 h, a Reporting API report older than 7 days not downloaded, and `needs_reauth`.
- **Idempotency:** any date range can be re-ingested without duplicates.
- **Security:** least-privilege IAM per function; no long-lived AWS keys on the laptop (IAM Identity Center); secrets never logged; dependency and IaC scanning.
- **Compliance:** §7.
- **Observability:** structured JSON logs (30-day retention), `ingestion_runs`, freshness via MCP.
- **IaC:** **Terraform** for all AWS resources. State in an S3 backend with native S3 locking (`use_lockfile`), bootstrapped once. The Google Cloud setup is manual (`docs/setup-google-youtube.md`).

## 11. Cost Estimate

**v1:** $0. Everything runs on the owner's laptop.

**Later phase (eu-central-1, one channel).** Assumptions: one daily run of ~2 min at 512 MB plus ~30 one-off backfill invocations; ~5 MB of Parquet; ~300 MCP queries/month with ~50 S3 range GETs each.

| Item | Usage / month | Cost / month |
|---|---|---|
| Lambda (arm64) | ~100 invocations, ~2 000 GB-s | $0.00 (always-free tier; without it ≈ $0.03) |
| EventBridge Scheduler | ~60 invocations | $0.00 |
| SQS | < 1 000 requests | $0.00 |
| S3 storage | < 100 MB incl. raw + versions | < $0.01 |
| S3 requests | ~3 000 PUT, ~20 000 GET | ~$0.03 |
| S3 data transfer to laptop | < 1 GB | $0.00 |
| SSM Parameter Store (standard, AWS-managed key) | 2 parameters | $0.00 |
| KMS | AWS-managed keys only | $0.00 (a customer-managed key would add $1.00) |
| CloudWatch Logs | < 50 MB | ~$0.03 |
| CloudWatch alarms | 4 standard alarms | $0.40 |
| SNS e-mail | a few | $0.00 |
| Terraform state bucket | KB | $0.00 |
| **Total** | | **≈ $0.50/month** (< $6/year) |

DynamoDB on-demand would also cost ≈ $0, so cost does not decide between the two. Main cost risks: a runaway Lambda/S3 loop (mitigated by reserved concurrency of 2 and a $5 AWS Budget alert) and heavy `query_sql` scans (fractions of a cent each).

## 12. Tech Stack

- **Language:** Python 3.12; `uv` for dependencies and the lockfile.
- **v1 runtime dependencies:** the official MCP Python SDK (`mcp` 2.x, `MCPServer`, stdio) and DuckDB 2.x (in-memory). No other runtime dependencies.
- **Tests:** pytest with synthetic export fixtures and an in-process MCP client.
- **Tooling:** ruff (incl. `S` security rules), pip-audit, pre-commit hooks with gitleaks and ruff, and an AI security review as a pre-push hook.
- **Process:** OpenSpec + security review of every commit before it is pushed (`INSTRUCTIONS.md`). No CI (single developer).
- **Git hosting:** GitHub (`Skarabaeus/YouTube-Analyser`).
- **Later phase:** DuckDB also in Lambda (Parquet read/write, no pyarrow, so the Lambda zip stays under its size limit); Google API client; Terraform (`infra/`) with `tflint` and `checkov`; recorded and anonymised API fixtures.

## 13. Decisions Log

| # | Topic | Decision |
|---|---|---|
| 1 | Tenancy | Own channel only; tenant-scoped data model for the later pipeline. |
| 2 | Channels | 1 channel. |
| 3 | Revenue | Not in v1; prepared in the later schema. |
| 4 | Impressions / CTR | v1: from Studio *Datum*/*Videos* exports. Later: Reporting API, with exports for older data. |
| 5 | History | v1: whatever the exports cover (lifetime export recommended). Later: 24-month API backfill. |
| 6 | Storage | v1: none (exports + in-memory DuckDB). Later: S3 + Parquet + DuckDB (≈ $0.50/month). |
| 7 | MCP | Local stdio for Claude Desktop; remote option kept open. |
| 8 | IaC | Terraform, when AWS is used. |
| 9 | AWS | Existing account with IAM Identity Center, `eu-central-1` (later phase). |
| 10 | Git | GitHub remote `Skarabaeus/YouTube-Analyser`. OpenSpec initialised for Claude Code. |
| 11 | ToS | v1 is not subject to the API policies (owner's own exports). Later: long-term storage of analytics metrics allowed with 30-day auth re-confirmation; metadata limited to 30 days (§7). |
| 12 | v1 path (2026-10-27) | **Local MCP over Studio exports** instead of the API pipeline. Gives most of the value (correct numbers, validation, small context) at no cost or operational effort. The pipeline is built only if the manual exports become the bottleneck. |
| 13 | Distinct counts | Never summed across days; only from an export's totals row for exactly the requested period. |

## 14. Phasing (each phase = one or more OpenSpec changes)

0. **v1 — local export MCP** (`add-local-export-mcp`): repo tooling (uv, ruff, pytest, pre-commit/pre-push hooks with gitleaks and security review), Studio export ingestion, local stdio MCP server. **Current.**

Possible next steps on the local path:
- More export dimensions (traffic sources, geography, devices, subscribed status).
- Chart data (`Diagrammdaten.csv`) as per-video daily series.
- Annotations (owner notes), e.g. in a local file next to the exports.

The following phases are deferred and start only if the export-based workflow proves too limited:

1. **Foundation:** Terraform bootstrap (state bucket) and base (data bucket, SSM paths, IAM roles, budget alert).
2. **Google setup + onboarding CLI:** OAuth flow, grant storage, channel registry, Reporting API job creation.
3. **Core ingestion:** `channel_daily`, `video_daily`, `videos`, trailing-window upsert, 24-month backfill, `ingestion_runs`, alarms.
4. **Reach ingestion:** Reporting API download incl. backfill-report replacement.
5. **Retention & compliance:** retention Lambda, revocation flow, offboard/purge.
6. **MCP server on the pipeline data:** reuse the v1 tools on top of the API-fed store.
7. **Breakdowns:** traffic sources, audience, retention snapshots.
8. **Later:** remote MCP, multi-tenant onboarding, monetary metrics.

## 15. Open Questions

None blocking. Resolved:
- Studio export formats are pinned in §9.1 (German UI).
- The alert e-mail address for the later phase is set as the Terraform variable `alert_email` in the git-ignored `infra/terraform.tfvars` and never committed.

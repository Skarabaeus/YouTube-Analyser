# YouTube Analyser — Specification

Status: **Draft v0.3** (2026-10-27). Decisions from the review rounds are in §13. This document is the input for the OpenSpec changes; after decomposition into `openspec/specs/` it is kept as historical context only.

> **Scope change (2026-10-27):** v1 is a **local MCP server over YouTube Studio CSV exports** (OpenSpec change `add-local-export-mcp`). The AWS/API pipeline described in §3–§8 and §10–§11 is deferred to a later phase (§14). It is kept here as the target architecture, not as v1 scope. The README describes the current setup.

## 1. Goal

Collect success metrics for a YouTube channel once per day, store them durably, and expose them through an MCP server so that Claude Desktop can analyse performance and give growth advice.

### Scope v1 (revised)
- **One owner, one channel** (the owner's own channel).
- The owner exports CSVs from YouTube Studio (*Datum* and *Videos* tabs) into a local, git-ignored folder.
- A local stdio MCP server for Claude Desktop reads, validates and merges these exports in memory (DuckDB) and offers analysis tools (§8, README).
- No Google OAuth, no AWS, no persistent storage, no network access.

### Later phases (previously v1)
- Daily ingestion via YouTube Analytics API, Data API and Reporting API (impressions/CTR) on AWS (§3–§7).

### Designed for, but not built in v1
- **Multiple owners (tenants)** and multiple channels per owner — data model, storage paths, secrets and IAM are tenant-scoped from day one (§6.3).
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
| **Tenant** | An owner of data (a person/customer). v1 has exactly one: `default`. |
| **Account** | A Google account that granted OAuth consent. Belongs to one tenant. |
| **Channel** | A YouTube channel. One Google account may manage several channels (Brand Accounts). |
| **Grant** | An OAuth refresh token. Bound to **one channel** — Google asks which channel/Brand Account to use at consent time, so each channel is onboarded separately. |
| **Ingestion run** | One execution of the daily pull for one channel. |
| **Source** | Origin of a data row: `analytics_api`, `reporting_api`, `data_api`, `manual_import`. |

## 3. Data Sources

| API | Used for | Notes |
|---|---|---|
| **YouTube Analytics API v2** (`reports.query`) | Channel- and video-level metrics per day, traffic sources, demographics, geography, devices, retention. | Scope `yt-analytics.readonly`. Supports historical queries → used for the 24-month backfill. |
| **YouTube Data API v3** | Video inventory and metadata (title, publish time, duration, tags, thumbnail, Shorts vs. long-form), channel metadata, lifetime counters. | Scope `youtube.readonly`. Daily sync uses ≪ 100 of the 10 000 default quota units. |
| **YouTube Reporting API v1** | **Impressions and impression CTR** per video per day. | Report types `channel_reach_basic_a1` (date, video → `video_thumbnail_impressions`, `video_thumbnail_impressions_ctr`) and `channel_reach_combined_a1` (adds traffic source, device, OS). Scope `yt-analytics.readonly`. |
| **Manual import** | Gaps the APIs cannot fill, mainly impressions/CTR before the Reporting API's history window. | YouTube Studio CSV export, imported via CLI (§9). |

### 3.1 Data characteristics that drive the design
- **Latency & revisions:** data for day *D* arrives after ~2–3 days and can be revised. The daily job re-fetches a **trailing window of 7 days** and upserts idempotently.
- **Backfill:** on onboarding, **24 months** via the Analytics API. That covers year-over-year comparisons (seasonality), and more history is unlikely to help a young channel. The depth is configurable and can be extended later, because the API keeps full history.
- **Reporting API timing:**
  - A newly created job generates historical reports for only the **30 days before job creation**.
  - Reports can be downloaded for **60 days** after generation (historical ones for 30 days); after that they are gone.
  - YouTube may issue **backfill reports** that replace earlier ones. The importer must replace the data for that period.
  - ⇒ Create the reporting jobs as early as possible (see `docs/setup-google-youtube.md`). Anything older is filled by manual import.
- **Volume:** small (one channel ≈ < 100k rows/year). Query flexibility, not storage, is the deciding factor for the database choice.

## 4. Metrics (v1)

### Channel, per day
`views, engagedViews (if available), estimatedMinutesWatched, averageViewDuration, averageViewPercentage, subscribersGained, subscribersLost, likes, dislikes, comments, shares, videosAddedToPlaylists, videosRemovedFromPlaylists`

### Video, per day
Same core metrics with dimensions `day,video`, chunked by video list.

### Reach, per video per day
`impressions, impressions_ctr` (Reporting API or manual import), optionally broken down by traffic source and device.

### Breakdowns
- Traffic sources (`insightTrafficSourceType`, `insightTrafficSourceDetail` for top videos)
- Geography (`country`), devices / OS
- Demographics (`ageGroup, gender` → `viewerPercentage`), period-based
- Subscribed vs. not subscribed
- Content type (Shorts / long-form / live)

### Per-video snapshots
- Audience retention curve (`audienceWatchRatio`, `relativeRetentionPerformance` by `elapsedVideoTimeRatio`): weekly for videos < 90 days old, monthly otherwise.
- Current metadata (title, description, tags, thumbnail URL, duration). History is limited by policy (§7).

### Future (not v1)
Monetary metrics (`estimatedRevenue, cpm, …`) need scope `yt-analytics-monetary.readonly`. The schema keeps a nullable revenue column group so they can be added without migration.

## 5. Architecture

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

### 5.1 Ingestion
EventBridge Scheduler + Lambda (arm64, Python). Fan-out via SQS (one message per channel; backfill is chunked into month-sized messages) keeps each invocation well under the 15-min limit and isolates failures. GitHub Actions cron and a laptop cron job were considered and rejected: secrets would be split across systems, and the laptop job only runs when the laptop is on.

### 5.2 Secrets
- Google OAuth client ID/secret: SSM SecureString `/yta/oauth/client`.
- Refresh tokens: SSM SecureString `/yta/tenants/<tenant>/channels/<channelId>/grant`, encrypted with the AWS-managed SSM key (free tier).
- IAM: only the ingest role may read grants, and only the onboarding identity may write them. The MCP identity has **no** access to grants.
- The Google OAuth consent screen must be **In production**. In *Testing* status, refresh tokens expire after 7 days. For personal use (< 100 users) no Google verification is required; the "unverified app" warning is acceptable.
- `invalid_grant` → channel status `needs_reauth`, ingestion stops, e-mail alert, retention clock starts (§7).

## 6. Storage

### 6.1 Decision: S3 + Parquet, queried with DuckDB
DynamoDB was rejected. It costs about the same (≈ $0 at this volume), but it cannot do the aggregations, joins and time-range analyses the MCP tools need. S3 + Parquet + DuckDB gives full SQL at near-zero cost and no running database. Neither option exposes a database endpoint to the internet.

### 6.2 Layout
```
s3://yta-data-<aws-account-id>-euc1/
  raw/tenant=<t>/channel=<c>/<source>/<endpoint>/fetched=<ts>.json.gz   # verbatim API responses, 25-day lifecycle
  manual/tenant=<t>/channel=<c>/<import-id>/original.csv               # uploaded Studio exports (user data)
  curated/<table>/tenant=<t>/channel=<c>/month=<YYYY-MM>/data.parquet
```
- Curated partitions are **rewritten per (tenant, channel, month)**. This makes upserts and deletions idempotent without a database.
- Bucket: SSE-S3, Block Public Access, versioning (noncurrent versions expire after 7 days, so deletions are effective — see §7), TLS-only bucket policy.

### 6.3 Multi-tenancy readiness (v1 = single tenant `default`)
- Every table has `tenant_id` as the leading key column, and every S3 and SSM path is prefixed by tenant.
- IAM policies are written with tenant prefixes, so per-tenant roles can be added later without moving data.
- The MCP server is configured with a tenant scope and every query is filtered by it. In v1 the scope is fixed to `default`.
- Opening up to other users later requires (out of scope now): Google OAuth verification of sensitive scopes, a privacy policy, a self-service onboarding flow, per-tenant MCP authentication (remote MCP), and possibly a YouTube API compliance audit / quota extension.

### 6.4 Logical data model

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
| `annotations` | tenant_id, channel_id, [video_id], date, annotation_id | free-text notes by the owner, e.g. "changed thumbnail", "posted on Reddit" | user data |
| `video_period_totals` | tenant_id, channel_id, video_id, period_start, period_end, source | views, watch time, subscribers_net, impressions, impressions_ctr (manual Studio exports) | statistics (user data) |
| `studio_metrics_daily` | tenant_id, channel_id, [video_id], date, metric | value (Studio-only metrics from manual exports) | user data |
| `manual_imports` | tenant_id, import_id | file name, type, date range, rows, imported_at | user data |
| `ingestion_runs` | tenant_id, channel_id, run_id | started/finished, status, rows, error | operational |
| `reporting_jobs` | tenant_id, channel_id, report_type | job_id, created_at, last_report_downloaded | operational |

All metric rows carry `source` and `fetched_at`. When API and manual data overlap for the same key, **API data wins** in the curated views. Manual rows are kept and stay visible with `source=manual_import`.

## 7. Data Retention & YouTube API Policy Compliance

Checked against the [YouTube API Services Developer Policies](https://developers.google.com/youtube/terms/developer-policies) on 2026-10-27. Re-check on every major change.

### 7.1 What the policies say (relevant parts)
| Rule | Section | Applies to |
|---|---|---|
| Authorized Data from the **YouTube Analytics API** and **Reporting API**, and statistics (view/subscriber counts) from other YouTube APIs, may be stored **"for as long as is necessary"**. However, the client must confirm **every 30 days** that the user still authorizes access. | III.E.4.b | All metrics in this system (fetched with the owner's OAuth consent) |
| **Other Authorized Data** (e.g. titles, descriptions, tags, thumbnails): store **≤ 30 calendar days**, then refresh or delete. | III.E.4.c | `videos`, channel title/handle, raw responses |
| Use reasonable efforts to keep stored data consistent with current API data, and show the most current data. | III.E.4.e/f | All |
| Do not replace API data with independently calculated data or create new/derived metrics. A separately calculated figure may only be shown next to the API figure, clearly labelled. | III.E.4.h | MCP outputs |
| A user must be able to request deletion of their stored data. Deletion happens within **7 days**. | III.E.4.g | Offboarding / purge |
| Revocation in the client → delete all Authorized Data within **7 days**. Revocation via Google account settings → delete within **30 days**; tokens must be re-validated periodically. | III.D.2.3 | Grants |
| [Derived-metrics & storage amendment](https://developers.google.com/youtube/terms/derived-metrics-policy) (III.L, since 2026-06-01): audited analytics developers who apply for it may create derived metrics and keep statistics for up to 36 months. | III.L | **Not applicable to v1**. Relevant if the product is opened to others. |

**Result:** long-term storage of the analytics metrics **is allowed**, provided authorization is re-confirmed at least every 30 days. Metadata such as titles is limited to 30 days unless refreshed. Studio CSV exports are the owner's own data, supplied manually and not retrieved through the API, so the API policies do not cover them. They are still deleted on purge.

### 7.2 How the system complies
1. **Authorization re-confirmation:** every daily run refreshes the access token and calls `channels.list(mine=true)`. On success it sets `channels.last_auth_verified_at`. MCP responses flag the data as stale if `last_auth_verified_at` is older than 30 days.
2. **Metadata freshness:**
   - `videos` is refreshed daily.
   - Superseded metadata versions (`valid_to` older than 30 days) are deleted by the retention Lambda.
   - Metadata of videos no longer returned by the API (deleted/private) is deleted after 30 days. Their metrics stay (keyed by `video_id`).
   - Title/thumbnail change history beyond 30 days lives in `annotations`, written by the owner.
3. **Raw API responses:** the S3 lifecycle rule deletes them after 25 days.
4. **Revocation handling:**
   - On `invalid_grant`, the channel moves to `needs_reauth`, and `reauth_lost_at` is set.
   - An e-mail alert goes out on day 0, day 14 and day 21.
   - If the channel is not re-authorized by **day 25**, the retention Lambda purges all API-derived data of that channel (5-day safety margin to the 30-day limit).
5. **Offboarding / deletion request:** `yta offboard <channel>` revokes the token at Google and deletes the SSM grant. It then purges all of the channel's data (API-derived, manual imports, annotations) **immediately**, and noncurrent S3 versions expire within 7 days.
6. **Derived figures in MCP output:**
   - Tools return API figures as-is.
   - Computed values (period deltas, growth %, per-video averages, CTR recomputed from breakdowns) are labelled `calculated` and shown next to the underlying API figures.
   - The system never stores derived metrics as if they came from the API.

## 8. MCP Server

### 8.1 Hosting — decision: local stdio (v1)
Claude Desktop starts the server as a subprocess. The server queries S3 directly with DuckDB (`httpfs`/`aws` extension), using a **read-only** AWS profile from IAM Identity Center (short-lived credentials, no stored keys). No endpoint is exposed.

Remote MCP (Lambda Function URL + OAuth 2.1, ~$0/month) stays a later option for claude.ai web/mobile and multi-tenant use. The server code keeps transport and auth separate from the tool logic so it can be deployed either way.

### 8.2 Tools (v1)

| Tool | Purpose |
|---|---|
| `list_channels` | Channels, status, last successful sync, last complete data day, auth freshness. |
| `channel_overview(channel, period, compare_to?)` | KPIs for a period vs. the previous period. |
| `top_videos(channel, metric, period, limit, content_type?)` | Ranked videos. |
| `video_details(video)` | Metadata, lifetime figures, recent daily metrics, reach, annotations. |
| `video_timeseries(video, metrics, period)` | Daily series. |
| `compare_videos(videos[], metrics, days_since_publish)` | Videos aligned by age (day 0 = publish day). |
| `reach(channel|video, period, breakdown?)` | Impressions & CTR, optionally by traffic source/device; shows data source per row. |
| `retention_curve(video)` | Latest retention snapshot. |
| `traffic_sources(channel|video, period)` | Breakdown. |
| `audience(channel, period)` | Geography, devices, demographics, subscribed status. |
| `publishing_patterns(channel, period)` | Performance by publish weekday/hour, duration bucket, Shorts vs. long-form. |
| `annotations(channel, video?, period?)` | Owner's notes; read-only via MCP in v1. |
| `query_sql(sql)` | Read-only SQL over curated views. Only `SELECT`/`WITH` allowed, row limit, timeout, tenant filter enforced through views. |

### 8.3 Resources & prompts
- Resource `yta://schema` — table/column descriptions, data sources, retention notes.
- Resource `yta://channels/<id>/summary` — rolling 28-day summary.
- Prompts: `weekly_review`, `video_post_mortem(video)`, `content_ideas_from_top_performers`, `thumbnail_title_review(video)`.

### 8.4 Output conventions
Every response states the units, the explicit date range, the data freshness (last complete day) and the source (API / manual). Calculated values are labelled (§7.2). Response size is capped.

## 9. CLI (`yta`, runs locally)

| Command | Purpose |
|---|---|
| `yta onboard [--tenant default]` | OAuth loopback flow (`access_type=offline`, `prompt=consent`). Resolves the channel, stores the grant, creates Reporting API jobs if missing, enqueues the 24-month backfill. |
| `yta import studio <file.csv\|zip> --channel <id>` | Validates and imports a YouTube Studio export (§9.1), uploads the original to `manual/`, writes curated rows with `source=manual_import`. Dry-run by default, `--apply` to commit. |
| `yta annotate <channel> [--video <id>] --date <d> "<text>"` | Adds an annotation. |
| `yta backfill <channel> --from <date>` | Extends history. |
| `yta status` | Channels, runs, reporting jobs, auth freshness. |
| `yta offboard <channel>` / `yta purge <channel>` | Revoke + delete (§7.2). |

Write operations stay in the CLI, which uses a separate, write-capable AWS role. The MCP server is strictly read-only.

### 9.1 Manual import formats
Pinned against real sample exports (German Studio UI, 2026-10-27; they live in the git-ignored `report-exports/` and `data/` folders). The implemented source of truth for header mapping, units and aggregation is `src/yta/metrics.py`.

**Export folder structure:** `<Dimension> <YYYY-MM-DD>_<YYYY-MM-DD> <Channel name>/`, containing:

| File (DE) | File (EN) | Content |
|---|---|---|
| `Tabellendaten.csv` | `Table data.csv` | One row per dimension value, **all columns selected in the table**. The first data row `Gesamt`/`Total` holds totals and is skipped (it can be used to cross-check the import). |
| `Diagrammdaten.csv` | `Chart data.csv` | Daily series per dimension value, but **only for the single chart metric** and **only for the rows selected in the chart** (default: top 5). Columns: `Datum, <dimension…>, <chart metric>`. |
| `Gesamtwerte.csv` | `Totals.csv` | Daily totals of the chart metric. |

**Parsing rules (observed):**
- UTF-8 without BOM, comma separator, `"`-quoted fields.
- Decimal point; no thousands separator.
- Dates are `YYYY-MM-DD`. The publish date is English `"Oct 2, 2025"` even in the German UI.
- `Dauer` is in seconds. `Durchschnittliche Wiedergabedauer` is `H:MM:SS`, and empty when there were no views.
- `Thumbnail-Klickrate (%)` is a percentage, stored as a fraction.
- Date-dimension tables omit some zero days. They are sorted by the **table's sort column** (e.g. *Engaged Views*), not by date, so the importer must not assume any order.
- An empty cell means *no data* and is stored as NULL. `0` is a real zero.
- `Abonnenten` is net subscribers (`Gewonnene` − `Verlorene`).
- **Reconciliation:** for *Datum* exports, the sum of all rows must equal the `Gesamt` row for additive metrics (views, watch time, impressions, likes, …). For *Videos* exports the sum must not exceed it, because their `Gesamt` row is the channel total and includes activity not attributable to listed videos (e.g. 247 vs. 315 subscribers gained in the real sample). Otherwise the import is rejected.
- In *Videos* exports, Studio leaves distinct viewer counts empty per video and `0` in the `Gesamt` row. These zeros are ignored.

**Header mapping (DE → canonical; EN variants are mapped too):**

| Header (DE) | Canonical column |
|---|---|
| `Datum` | `date` |
| `Videos` | `video_id` |
| `Videotitel` | `title` (not stored beyond 30 days — §7) |
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

**Studio-only metrics** may not be available through the Analytics API. Distinct counts among them are not additive across days; for multi-day periods they come only from an export's `Gesamt` row for exactly that period. In v1, all metrics are kept in long format (`channel_daily_long`), so new Studio columns need no schema change:
- `Einzelne Zuschauer` (unique viewers)
- `Neue Zuschauer` / `Wiederkehrende Zuschauer` (new / returning viewers)
- `Aufrufe pro Zuschauer (Durchschnitt)` (views per viewer)
- `Unique Reach`
- `Aufrufe (Co-Viewing)` (co-viewing views)
- `Bis zum Ende angesehen (%)` (watched to the end)
- `Hypes` / `Hype-Punkte`

The API ingestion does not refresh them. They only stay current through periodic manual exports. API availability is checked in phase 3 and documented in the change.

Unknown headers are reported and kept as `x_<header>` metrics, queryable via SQL. A missing required column (`Datum`/`Videos` plus at least one metric) fails the import.

**Import types and target views (v1, in memory):**
1. *Videos* table → `video_totals` (`period_start`, `period_end` from the folder name). Never mixed into daily sums.
2. *Datum* table → `channel_daily` / `channel_daily_long`.
3. `Gesamt` rows of both → `channel_period_totals`. For the same period, *Datum* wins over *Videos*.
4. Chart data (`Diagrammdaten.csv`) is not imported in v1. A later change could map it to per-video daily data, one export per chart metric.

In the later pipeline, the same imports feed `video_period_totals`, `channel_daily` (`source=manual_import`) and `studio_metrics_daily` (§6.4).

**Historical CTR scope (status 2026-10-27):**
- Channel-level daily impressions/CTR (export B) and per-video lifetime totals (export A) are both available. That is sufficient for v1.
- Per-video daily or monthly CTR (exports C/D, A-monthly) is optional.

See `docs/setup-google-youtube.md` §5.

## 10. Non-functional Requirements

- **Cost:** target < $1/month for one channel (§11).
- **Reliability:** per-channel isolation; 3 retries with backoff; DLQ. Alarms go to SNS e-mail on DLQ depth > 0, no successful run in 48 h, a Reporting API report older than 7 days not downloaded, and `needs_reauth`.
- **Idempotency:** any date range can be re-ingested without duplicates.
- **Security:** least-privilege IAM per function; no long-lived AWS keys on the laptop (IAM Identity Center); secrets never logged; dependency and IaC scanning; per-commit security review (`INSTRUCTIONS.md`).
- **Compliance:** §7.
- **Observability:** structured JSON logs (30-day retention), `ingestion_runs`, freshness via MCP.
- **IaC:** **Terraform** for all AWS resources. State in an S3 backend with native S3 locking (`use_lockfile`, no DynamoDB lock table), bootstrapped once. The Google Cloud setup is manual and documented in `docs/setup-google-youtube.md`.

## 11. Cost Estimate (eu-central-1, one channel)

Assumptions:
- One daily run of ~2 min at 512 MB, plus about 30 one-off backfill invocations.
- About 5 MB of Parquet.
- About 300 MCP queries/month, each with ~50 S3 range GETs.

| Item | Usage / month | Cost / month |
|---|---|---|
| Lambda (arm64) | ~100 invocations, ~2 000 GB-s | $0.00 (always-free tier: 1M requests, 400k GB-s; without free tier ≈ $0.03) |
| EventBridge Scheduler | ~60 invocations | $0.00 (14M free) |
| SQS | < 1 000 requests | $0.00 (1M free) |
| S3 storage | < 100 MB incl. raw + versions | < $0.01 |
| S3 requests | ~3 000 PUT, ~20 000 GET | ~$0.03 |
| S3 data transfer to laptop | < 1 GB | $0.00 (100 GB/month free egress) |
| SSM Parameter Store (standard, SecureString with AWS-managed key) | 2 parameters | $0.00 |
| KMS | AWS-managed keys only | $0.00 (a customer-managed key would add $1.00) |
| CloudWatch Logs | < 50 MB | ~$0.03 |
| CloudWatch alarms | 4 standard alarms | $0.40 |
| SNS e-mail | a few | $0.00 |
| Terraform state bucket | KB | $0.00 |
| **Total** | | **≈ $0.50/month** (< $6/year) |

For comparison, DynamoDB on-demand would also cost ≈ $0 here, so cost does not decide between the two. Main cost risks:
- A runaway loop of Lambda/S3 requests. Mitigations: reserved concurrency of 2 on the ingest Lambda, and an AWS Budget alert at $5.
- Accidental heavy `query_sql` scans. Each one still costs fractions of a cent.

## 12. Tech Stack

- Language: **Python 3.12** (Google API client, DuckDB, official MCP Python SDK); `uv` for dependencies and lockfile.
- DuckDB is used both in Lambda (Parquet read/write) and in the MCP server. pyarrow is not needed, which keeps the Lambda zip under its size limit (no container image/ECR).
- IaC: **Terraform** (`infra/`), with `tflint` and `checkov`.
- Tests: pytest, recorded and anonymised API fixtures, in-memory DuckDB for MCP tool tests, Terraform `validate` + `plan` in CI.
- Process: OpenSpec + per-commit security review (`INSTRUCTIONS.md`).
- Git hosting: GitHub (remote to be created later).

## 13. Decisions Log

| # | Topic | Decision |
|---|---|---|
| 1 | Tenancy | Own channel only in v1; tenant-scoped data model from day one. |
| 2 | Channels | 1 channel. |
| 3 | Revenue | Not in v1; schema prepared. |
| 4 | Impressions / CTR | Yes, via the Reporting API plus manual Studio import for older data. |
| 5 | Backfill | 24 months (configurable, extendable). |
| 6 | Storage | S3 + Parquet + DuckDB (≈ $0.50/month total). |
| 7 | MCP | Local stdio for Claude Desktop; remote option kept open. |
| 8 | IaC | Terraform. |
| 9 | AWS | Existing account with IAM Identity Center, `eu-central-1`. |
| 10 | Git | Local repo now, GitHub remote later. OpenSpec initialised for Claude Code. |
| 11 | ToS | Long-term storage of analytics metrics allowed with 30-day auth re-confirmation. Metadata limited to 30 days. Compliance per §7. |

## 14. Phasing (each phase = one or more OpenSpec changes)

0. **v1 — local export MCP** (`add-local-export-mcp`): repo tooling (uv, ruff, pytest, pre-commit with gitleaks and security review), Studio export ingestion, local stdio MCP server.

The following phases are deferred and will only start if the export-based workflow proves too limited:

1. **Foundation:** Terraform bootstrap (state bucket) and base (data bucket, SSM paths, IAM roles, budget alert).
2. **Google setup + onboarding CLI:** OAuth flow, grant storage, channel registry, Reporting API job creation.
3. **Core ingestion:** `channel_daily`, `video_daily`, `videos`, trailing-window upsert, 24-month backfill, `ingestion_runs`, alarms.
4. **Reach ingestion:** Reporting API download incl. backfill-report replacement.
5. **Retention & compliance:** retention Lambda, revocation flow, offboard/purge.
6. **MCP server on the pipeline data:** reuse the v1 tools on top of the API-fed store.
7. **Annotations** (owner notes; needs a persistent local or remote store).
8. **Breakdowns:** traffic sources, audience, retention snapshots.
9. **Later:** remote MCP, multi-tenant onboarding, monetary metrics.

## 15. Open Questions

None blocking. Resolved:
- Studio export formats are pinned in §9.1 (German UI). Additional daily impressions/CTR exports are pending from the owner.
- The alert e-mail address is provided. It is set as the Terraform variable `alert_email` in the git-ignored `infra/terraform.tfvars` and never committed.

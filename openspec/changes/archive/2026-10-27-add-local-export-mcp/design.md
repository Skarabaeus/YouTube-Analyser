# Design

## Context

- The repository has no code yet. The planned AWS/API pipeline (`SPEC.md` §5–§8) is deferred; see proposal.md, "Why".
- Real exports exist locally (git-ignored):
  - `report-exports/Datum … /Tabellendaten.csv`: 462 days, 28 metric columns, plus a `Gesamt` totals row.
  - Earlier `Videos …` exports in `data/`: 25 videos with lifetime totals incl. impressions/CTR.
- The formats observed are documented in `SPEC.md` §9.1. One new header appeared since then: `Gelegentliche Zuschauer` / `Regelmäßige Zuschauer` (casual / regular viewers).
- Data volume is tiny (< 1 MB), so loading everything into memory on every refresh is cheap.

## Goals / Non-Goals

**Goals:**
- A correct, testable ingestion core that the later API pipeline can reuse: the same canonical metric names and the same views.
- Claude Desktop setup with one config entry.

**Non-Goals:**
- Persistent database, caching to disk, or any write path.
- Chart exports (`Diagrammdaten.csv`) and dimensions other than `Datum` and `Videos`. They are reported but ignored, and are candidates for follow-up changes.
- Per-video daily data.
- Multi-tenant handling. The channel name from the folder name is kept as a column, so several channels already work naturally.

## Decisions

### D1: Python 3.12 + `uv`, official `mcp` SDK (FastMCP), DuckDB in-memory
DuckDB gives full SQL for `query_sql` and the aggregations, is in-process, and is the engine planned for the later pipeline (`SPEC.md` §12).
- *Alternative:* pandas only. Rejected: no safe SQL surface, and the aggregation logic would be duplicated.
- *Alternative:* SQLite. Rejected: weaker analytics SQL and no built-in way to block file access.

### D2: Parse CSVs in Python, load into DuckDB afterwards
CSV parsing (header mapping, German/English variants, percent and duration handling, totals reconciliation) is done in plain Python with the `csv` module. Normalized rows are then inserted into DuckDB. This keeps the parsing rules unit-testable and lets DuckDB run with external file access disabled (D5).
- *Alternative:* DuckDB `read_csv`. Rejected: it needs file access enabled in the same connection that serves `query_sql`.

### D3: Data model (in-memory views)
| View | Grain | Notes |
|---|---|---|
| `channel_daily` | channel, date | Wide view: one column per known canonical metric. Nulls are kept. |
| `channel_daily_long` | channel, date, metric | All metrics, including unmapped ones. Unmapped metrics use the name `x_<normalized header>`. |
| `video_totals` | channel, video_id, period_start, period_end | Title, published_at, duration_s, content_type, plus metrics. |
| `channel_period_totals` | channel, period_start, period_end, metric | From each export's `Gesamt` row. The only valid source for non-additive metrics over multi-day periods. |
| `exports` | export_id | Folder, dimension, period, status, reason, mtime. |

- Internally, data is stored long (`metric, value`) so unknown headers need no schema change.
- The wide view is generated from the known-metric registry, so tools and SQL stay simple.
- The CTR click count is materialized as `impressions_clicks = impressions × ctr` (labelled calculated), which makes ratio aggregation correct.

### D4: Metric registry as the single source of truth
One module defines each canonical metric: German and English headers, unit, parser, whether it is additive (used for reconciliation and summing), and how it aggregates (`sum` / `ratio(num, den)` / `weighted_avg(base)` / `none`). The header mapping, the reconciliation check, the time-series aggregation and the `yta://schema` resource are all derived from it. Unknown headers default to `aggregation = none`, are excluded from reconciliation, and can only be queried through `query_sql`.

Newly mapped in this change: `Gelegentliche Zuschauer` → `casual_viewers`, `Regelmäßige Zuschauer` → `regular_viewers`. `Einzelne Zuschauer`, `Neue Zuschauer`, `Wiederkehrende Zuschauer` and similar viewer counts are **not additive** across days (they count unique people per day), so they get `aggregation = period_total`. For a multi-day period or bucket, the tools look up `channel_period_totals` by exact `(period_start, period_end)`:
- If a match exists, they use it.
- If not, they return null and name the Studio export that would fill the gap, e.g. "Datum export 2026-09-01_2026-09-30".

Exact matching is deliberate. Containment or interpolation would produce wrong distinct counts.

Recommended routine: one `Datum` export per calendar month, plus the rolling range. This gives correct monthly unique, new and returning viewers.

### D5: `query_sql` sandbox
1. Parse with `duckdb.extract_statements`. Exactly one statement of type `SELECT` is allowed.
2. After the data is loaded, the connection is locked down: `SET enable_external_access = false`, `SET autoload_known_extensions = false`, `SET lock_configuration = true`. File functions (`read_csv`, `COPY`, `ATTACH`, `INSTALL`, …) then fail at engine level, independent of the parser check.
3. The query runs as `SELECT * FROM (<sql>) LIMIT 501` to detect truncation. Output is further capped at 100 columns, 1 000 characters per cell and 256 KB in total. The connection runs with `memory_limit = 512MB` and `threads = 2` (locked).
4. A timer thread calls `connection.interrupt()` after 5 s.
5. On a refresh (D6), a **new** connection is built, loaded and then locked, so loading never needs the lock lifted.

- *Alternative:* regex allow-listing. Rejected as brittle; it is only used as defence-in-depth after the parser check.

### D6: Refresh by fingerprint
On every tool call, the server computes a fingerprint of the exports root (sorted list of `(relative path, size, mtime_ns)` for the relevant CSVs). If the fingerprint changed, it rebuilds the dataset into a new connection and swaps it in atomically. If the rebuild fails, the previous dataset stays active and the error appears in `dataset_status`.
- *Alternative:* a file watcher. Rejected: an extra dependency, and pointless for a process that only runs during chats.

### D7: Merge precedence
"Latest period end wins, ties broken by latest folder mtime", applied per (key, metric) when building the long table (spec: Merging overlapping exports). Studio revises recent days, and a newer export reflects those revisions.

### D8: Period parameters
Tools accept `last_7_days`, `last_28_days`, `last_90_days`, `last_365_days`, `all`, `YYYY`, `YYYY-MM` or `YYYY-MM-DD..YYYY-MM-DD`. Relative periods are anchored at the **last day with data**, not at today, because exports lag behind. The response always echoes the resolved dates.

### D9: Package layout
```
pyproject.toml          # uv, ruff, pytest config; console script yta-mcp
src/yta/metrics.py      # metric registry (D4)
src/yta/ingest/         # discovery, parsing, reconciliation, merge
src/yta/dataset.py      # DuckDB build, lock-down, refresh (D5, D6)
src/yta/periods.py      # period resolution (D8)
src/yta/mcp_server.py   # FastMCP tools, resources, prompts
tests/fixtures/         # synthetic exports (German + English headers)
```

### D10: Claude Desktop integration
```json
"yta": {
  "command": "uv",
  "args": ["run", "--directory", "<repo>", "yta-mcp"],
  "env": { "YTA_EXPORTS_DIR": "<repo>/report-exports" }
}
```
This is documented in `README.md`; no installer is needed.

### D11: Repository tooling (part of this change because it is the first code)
- `ruff` (incl. `S` security rules), `pytest`, `pip-audit`.
- `pre-commit` with `gitleaks`, `ruff`, and a local hook that runs `claude -p "/security-review"` on staged changes, as in `INSTRUCTIONS.md` §2.
- CI is deferred until the GitHub remote exists.

## Security considerations

- **Attack surface:** no network listener and no outbound calls. The stdio peer is the local MCP client. Inputs are tool arguments (from the LLM) and CSV files (from the user).
- **SQL tool:** parser allow-list, plus engine-level `enable_external_access=false` and locked configuration (D5). Row and time caps prevent context flooding and runaway queries. The dataset is rebuilt from source files, so even a hypothetical in-memory mutation would not persist.
- **No path arguments:** no tool accepts file paths. Symbolic links inside the exports root are not followed. The exports root comes only from the environment, which prevents path traversal through the LLM.
- **Untrusted text:**
  - Video titles and channel names could contain prompt-injection text. They are only returned as JSON data fields, never placed into prompts or tool descriptions.
  - CSV files are parsed with the stdlib `csv` module, with size limits (skip files > 20 MB, cells > 10 000 chars) to avoid memory exhaustion.
- **Secrets:** none are involved in this change.
- **Supply chain:** two runtime dependencies (`mcp`, `duckdb`), pinned in `uv.lock` and audited with `pip-audit`.
- **Personal data:** real exports stay in git-ignored folders (`report-exports/`, `data/`). Tests use synthetic fixtures only, and gitleaks plus the review check that no real export is committed.

## Data retention

- This change stores nothing. Data lives only in process memory and is rebuilt from the user's own files.
- Studio exports are user-supplied data, not API data, so the YouTube API storage rules (`SPEC.md` §7) do not apply.
- Deleting an export folder removes its data from the server on the next tool call (D6).

## Risks / Trade-offs

- **Studio changes the export format or header names:** unmapped headers are kept and reported (D4); a reconciliation failure rejects the export visibly instead of corrupting data.
- **Non-additive viewer metrics are summed by mistake:** the registry marks them `period_total`. Tests assert that multi-day buckets without an exactly matching period total return null.
- **Large SQL results flood Claude's context:** the 500-row cap and the truncation flag.
- **The user forgets to refresh exports, so data goes stale:** every response states the last day with data; `dataset_status` shows coverage.
- **Totals row tolerance:** a floating-point rounding difference could reject a valid export. Tolerance is the larger of 0.5 % and 1 unit, and is applied only to additive metrics.

## Migration Plan

This is greenfield; there is nothing to migrate. Rollback means removing the `yta` entry from the Claude Desktop config.

## Open Questions

- Whether Studio's `Datum` export is in the channel's local time zone or Pacific Time. This doesn't affect this change and only matters when merging with API data later.

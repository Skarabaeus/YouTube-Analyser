# Tasks

## 1. Project setup

- [x] 1.1 Create `pyproject.toml` (Python 3.12, `uv`, console script `yta-mcp`, runtime deps `mcp` + `duckdb`, dev deps `pytest`, `ruff`, `pre-commit`, `pip-audit`; ruff rule set incl. `S`) and the `src/yta/` + `tests/` skeleton; verify `uv sync` succeeds and `uv run pytest` reports 0 tests collected without errors
- [x] 1.2 Add `.pre-commit-config.yaml` with gitleaks, ruff (lint + format) and a local hook running `claude -p "/security-review"` on staged changes; verify `pre-commit run --all-files` passes on the skeleton

## 2. Metric registry and value parsing

- [x] 2.1 Implement the metric registry (D4): canonical name, German/English headers (all headers from `SPEC.md` §9.1 plus `Gelegentliche`/`Regelmäßige Zuschauer`), unit, additive flag, aggregation rule; verify with a unit test that every header in the real `report-exports` header line maps to a known metric (test reads only the header list, hard-coded into the test, not the file)
- [x] 2.2 Implement value parsers (empty → null, decimal, percent → fraction, `H:MM:SS` → seconds, hours → minutes, `Oct 2, 2025` → date) with errors naming the row and column; verify with parametrized unit tests covering each parser and malformed input

## 3. Export ingestion

- [x] 3.1 Create synthetic fixtures in `tests/fixtures/`:
  - `Datum` export with German headers, unsorted rows, empty cells and a matching `Gesamt` row;
  - the same with English headers;
  - a `Videos` export;
  - a truncated export (totals mismatch);
  - an unsupported dimension;
  - a non-matching folder.

  Verify that the fixtures contain no real channel data.
- [x] 3.2 Implement folder discovery and naming-pattern parsing (dimension, period, channel, `-n` suffix, skipped/unsupported reasons, missing root); verify with tests for each scenario in the "Export folder discovery" and "Supported export dimensions" requirements
- [x] 3.3 Implement table parsing for `Datum` and `Videos` (header mapping, unmapped headers kept as `x_…`, totals row detection, row-order independence, file/cell size limits); verify with tests for the "Localized header mapping", "Value parsing" and "Row order independence" scenarios
- [x] 3.4 Implement totals reconciliation for additive metrics with the tolerance from the spec; verify with the consistent and truncated fixture tests
- [x] 3.5 Store each accepted export's `Gesamt` row as channel period totals, including non-additive metrics, and merge `Datum` and `Videos` totals for identical periods; verify with tests for the three "Channel period totals" scenarios, using a fixture with a full-range and a monthly `Datum` export
- [x] 3.6 Implement merge precedence across exports (latest period end, then mtime; per key and metric; complementary columns); verify with tests for both "Merging overlapping exports" scenarios

## 4. Dataset and query engine

- [x] 4.1 Build the DuckDB dataset: long table, generated wide `channel_daily` view, `video_totals`, `exports`, calculated `impressions_clicks`; verify with tests that query each view against fixtures
- [x] 4.2 Implement connection lock-down (`enable_external_access=false`, no extension autoload, `lock_configuration=true`) and the `query_sql` executor (single SELECT via `extract_statements`, 501-row truncation detection, 5 s interrupt); verify with tests for all four "Read-only SQL tool" scenarios, including `read_csv`, `COPY`, `ATTACH`, `INSTALL`, `SET` and multi-statement attempts
- [x] 4.3 Implement fingerprint-based refresh with an atomic swap that keeps the previous dataset on failure; verify with tests that add, modify and delete fixture folders between calls (in a temporary copy) and that source files' contents and mtimes are unchanged afterwards
- [x] 4.4 Implement period resolution (D8), anchored at the last day with data; verify with unit tests for each period form and for out-of-coverage errors

## 5. MCP server

- [x] 5.1 Implement the FastMCP server entry point (stdio only, `YTA_EXPORTS_DIR` with fallback) and the `dataset_status` tool; verify with an in-process MCP client test that lists tools and calls `dataset_status` against fixtures
- [x] 5.2 Implement `channel_overview` with `previous_period` / `previous_year` comparison and labelled calculated changes; verify with tests for the 28-day comparison and no-data scenarios
- [x] 5.3 Implement `channel_timeseries` with sum / ratio / weighted / period_total aggregation; verify the monthly-CTR scenario numerically
- [x] 5.4 Implement the exact-period lookup for non-additive metrics, shared by `channel_overview` and `channel_timeseries` (value from `channel_period_totals` marked `period_total`, otherwise null plus an export hint); verify with tests for the three "Non-additive metrics over multi-day periods" scenarios
- [x] 5.5 Implement `video_performance` (sorting, limit, short/long classification); verify with tests for the top-CTR and Shorts-only scenarios
- [x] 5.6 Implement `publishing_impact`; verify with a fixture containing two publish days and hand-calculated expected deltas
- [x] 5.7 Expose `query_sql` as a tool and add the `yta://schema` resource generated from the registry; verify with tests that the schema lists every view and column and documents `impressions_ctr` as a fraction
- [x] 5.8 Add the prompts `weekly_review`, `video_deep_dive(video_id)` and `growth_opportunities`; verify that the prompt listing returns all three and that no export text is interpolated into prompt templates
- [x] 5.9 Apply the response conventions in all tools (date range, last data day, source exports, units, `calculated` labels, 500-row cap); verify with one shared contract test run against every tool
- [x] 5.10 Verify no network use: a test runs the tool suite with socket creation patched to raise

## 6. Documentation

- [x] 6.1 Write `README.md`: purpose, Studio export how-to (German UI paths, recommended columns), Claude Desktop config (D10), tool overview; verify by following it on a clean checkout and seeing Claude Desktop list the `yta` tools
- [x] 6.2 Update `SPEC.md` (§1 scope, §14 phasing): v1 = local MCP over Studio exports, the AWS/API pipeline becomes a later phase, the new header mappings go into §9.1; verify that `SPEC.md` and the README don't contradict each other

## 7. Integration and release checks

- [ ] 7.1 End-to-end manual check with the real `report-exports` folder in Claude Desktop: `dataset_status` shows the export as accepted, `channel_overview(last_28_days)` matches the Studio UI for the same period, and a `query_sql` file-access attempt is rejected
- [x] 7.2 Run `/security-review` on the staged changes, then `ruff check`, `pytest`, `pip-audit` and `pre-commit run --all-files`; resolve findings of medium severity or higher before committing with the `Security-Review:` trailer

## Workflow follow-up

- Owner approves this proposal before `/openspec-apply-change` is started.
- Archive the change with `/openspec-archive-change` after it is committed and verified in Claude Desktop.

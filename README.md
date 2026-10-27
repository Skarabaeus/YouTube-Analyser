# YouTube Analyser

A local MCP server that lets Claude Desktop (or any MCP client) analyse your YouTube channel from **YouTube Studio CSV exports**. It needs no Google login, no cloud and no network access. The server reads the export folders, checks each export against its totals row, merges overlapping exports, and answers with SQL-backed numbers.

## Requirements

- macOS or Linux with [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Claude Desktop

```sh
uv sync
uv run pytest        # optional: run the test suite
```

## 1. Export data from YouTube Studio

YouTube Studio → **Analyse** → **Erweiterter Modus** (Advanced mode), then:

1. Pick the date range at the top right (e.g. *Gesamte Laufzeit* / *Lifetime*).
2. Pick a tab (dimension):
   - **Datum** (Date): channel metrics per day. This is the main dataset.
   - **Videos** (Content): totals per video for the selected range.
3. Add every metric you want with **+** in the table header. Recommended: *Aufrufe, Wiedergabezeit, Durchschnittliche Wiedergabedauer (in Prozent), Thumbnail-Impressionen, Thumbnail-Klickrate, Abonnenten (gewonnen / verloren), Einzelne / Neue / Wiederkehrende Zuschauer, Likes, Kommentare, Geteilte Inhalte, Veröffentlichte Videos*.
4. **Download icon** → *Kommagetrennte Werte (.csv)*. Unzip the download into the exports folder **without renaming the folder**. The folder name (e.g. `Datum 2025-05-23_2026-10-27 My Channel`) tells the server the dimension, period and channel.

Notes:
- The German and English Studio UIs are both supported.
- New exports are picked up automatically on the next question; no restart is needed. Newer exports win where they overlap with older ones.
- Distinct viewer counts (unique, new, returning, casual and regular viewers) cannot be added up across days. They come only from an export's totals row for exactly the requested period. For monthly values, add one *Datum* export per calendar month.
- Other dimensions (traffic sources, geography, …) and the chart files are not imported yet; `dataset_status` lists them as unsupported.

The default folder is `report-exports/` in this repository. It is git-ignored, and so are all CSV files outside `tests/fixtures/`.

## 2. Connect Claude Desktop

Claude Desktop → *Settings* → *Developer* → *Edit Config*. Add this to `claude_desktop_config.json`, replacing `<repo>` with the absolute path of this repository:

```json
{
  "mcpServers": {
    "yta": {
      "command": "uv",
      "args": ["run", "--directory", "<repo>", "yta-mcp"],
      "env": { "YTA_EXPORTS_DIR": "<repo>/report-exports" }
    }
  }
}
```

Restart Claude Desktop. The `yta` tools then appear under the tools icon. If `uv` is not found, use its absolute path (`which uv`).

## 3. What the server offers

| Tool | Purpose |
|---|---|
| `dataset_status` | Loaded / rejected / unsupported exports with reasons, coverage per metric, last day with data |
| `channel_overview(period, compare_to?)` | Key metrics for a period, optionally vs. `previous_period` or `previous_year` |
| `channel_timeseries(metrics, period, granularity)` | Metrics per `day`, `week` or `month`; ratios are recomputed correctly |
| `video_performance(sort_by, limit, content_type?)` | Videos ranked by a metric; `short` (≤ 180 s) or `long` |
| `publishing_impact(window_days)` | Channel metrics after each publish day vs. before |
| `query_sql(sql)` | One read-only `SELECT` over the views (max 500 rows, 5 s) |

- **Periods:** `last_7_days`, `last_28_days`, `last_N_days`, `all`, `2026`, `2026-09` or `2026-09-01..2026-09-30`. Relative periods end at the **last day with data**, not today.
- **Resource:** `yta://schema` documents the views, columns, units and aggregation rules.
- **Prompts:** `weekly_review`, `video_deep_dive(video_id)`, `growth_opportunities`.
- **Sources:** every result states its date range, last data day, source exports and units. Values are marked `export` (as exported), `period_total` (from an export's totals row) or `calculated` (computed by the server).

## Security

- **No network:** the server runs only over stdio and opens no network connections.
- **Read-only SQL:** `query_sql` accepts one `SELECT` only. DuckDB runs with file, network and extension access disabled and its configuration locked.
- **Your files stay unchanged:** export files are only read, never modified. Data lives in memory only.
- **Video titles are data:** they are returned as data fields and never placed into prompts.

## Development

The project uses spec-driven changes (OpenSpec) and requires a security review before every commit; see `INSTRUCTIONS.md`.

```sh
uv run pre-commit install
uv run ruff check && uv run ruff format --check && uv run pytest && uv run pip-audit
```

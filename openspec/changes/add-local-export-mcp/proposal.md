# Proposal

## Why

The full pipeline in `SPEC.md` needs Google OAuth, AWS infrastructure and YouTube API policy handling. For a single channel with about 25 videos that is more effort than value right now. YouTube Studio exports already contain richer data than the APIs provide for this channel: daily impressions/CTR, unique, new and returning viewers, and engaged views. A local MCP server that reads these exports gives Claude Desktop accurate, SQL-backed answers within hours of work instead of weeks, and it can grow into the full pipeline later.

## What Changes

- New local **stdio MCP server** for Claude Desktop. It reads YouTube Studio CSV exports from a configurable folder (default `report-exports/`) and answers analysis questions through dedicated tools and a read-only SQL tool.
- New **Studio export ingestion**:
  - discovers export folders (`<Dimension> <start>_<end> <Channel>[-n]`);
  - parses the German (and English) UI formats;
  - validates each export against its totals row;
  - merges overlapping exports into one de-duplicated dataset in memory.
- Supported export types in this change: **Datum** (channel metrics per day) and **Videos** (per-video totals for the export period). Other dimensions are detected and reported as unsupported.
- No AWS, no Google OAuth, no network access, no persistent storage beyond the user's own export files.
- Repository tooling for the first code in the repo: Python project (`uv`), lint/test setup, and pre-commit hooks that enforce the security review defined in `INSTRUCTIONS.md`.
- `SPEC.md` is updated: v1 becomes "local MCP over Studio exports", and the AWS/API pipeline moves to a later phase.

## Capabilities

### New Capabilities
- `studio-export-ingestion`: discovering, parsing, validating and merging YouTube Studio CSV exports into a normalized, queryable dataset.
- `analytics-mcp-server`: the read-only MCP interface (tools, resources, prompts) that exposes the channel analytics to MCP clients such as Claude Desktop.

### Modified Capabilities
_None — no existing specs._

## Impact

- **New code:** Python package (`src/yta/`) with an ingestion module and an MCP server entry point (`yta-mcp`).
- **New dependencies:** `mcp` (official Python SDK) and `duckdb`; dev dependencies `pytest`, `ruff`, `pre-commit`.
- **Local setup:** an entry in Claude Desktop's `claude_desktop_config.json` that points the server at the exports folder.
- **Data:** the user's exports stay local in git-ignored folders. Test fixtures are synthetic.
- **Docs:** a README section on setup and the export how-to; `SPEC.md` phasing updated.

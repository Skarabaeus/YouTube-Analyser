"""Parse and validate the table file of one export folder."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date

from yta.ingest.discovery import ExportFolder, table_file
from yta.metrics import BY_NAME, TOTAL_ROW_LABELS, map_header
from yta.parsing import ValueParseError, parse_iso_date, parse_publish_date

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CELL_CHARS = 10_000
RECONCILE_RELATIVE = 0.005
RECONCILE_ABSOLUTE = 1.0


class ExportRejected(Exception):
    """The export is invalid as a whole and must not contribute any data."""


@dataclass
class VideoRow:
    video_id: str
    title: str | None
    published_at: date | None
    duration_s: float | None
    metrics: dict[str, float]


@dataclass
class ParsedExport:
    folder: ExportFolder
    daily: dict[date, dict[str, float]] = field(default_factory=dict)
    videos: dict[str, VideoRow] = field(default_factory=dict)
    totals: dict[str, float] = field(default_factory=dict)


def parse_export(folder: ExportFolder) -> ParsedExport:
    """Parse an accepted export folder; raises ExportRejected with a readable reason."""
    if folder.path is None or folder.kind is None:
        raise ExportRejected("not an importable export folder")
    path = table_file(folder.path)
    if path is None:
        raise ExportRejected("table file disappeared")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ExportRejected(f"{path.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")

    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
    except (csv.Error, UnicodeDecodeError) as exc:
        raise ExportRejected(f"{path.name} is not a readable UTF-8 CSV file: {exc}") from None
    if not rows:
        raise ExportRejected(f"{path.name} is empty")

    header, body = rows[0], rows[1:]
    mappings = [map_header(h) for h in header]
    key_name = "date" if folder.kind == "date" else "video_id"
    if not mappings or mappings[0].name != key_name:
        raise ExportRejected(f"first column must be the {key_name} column, got '{header[0]}'")
    if not any(m.kind == "metric" for m in mappings):
        raise ExportRejected("no known metric column")
    folder.unmapped_headers = [m.header for m in mappings if m.kind == "unmapped"]

    parsed = ParsedExport(folder)
    for row_number, row in enumerate(body, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(header):
            raise ExportRejected(
                f"{path.name}, row {row_number}: expected {len(header)} cells, got {len(row)}"
            )
        if any(len(cell) > MAX_CELL_CHARS for cell in row):
            raise ExportRejected(f"{path.name}, row {row_number}: cell exceeds size limit")
        values, dims = _parse_row(path.name, row_number, row, mappings)
        key = row[0].strip()
        if key.casefold() in TOTAL_ROW_LABELS:
            parsed.totals = values
        elif folder.kind == "date":
            day = _parse_key(path.name, row_number, header[0], key, parse_iso_date)
            parsed.daily[day] = values
        else:
            if not key:
                raise ExportRejected(f"{path.name}, row {row_number}: empty video id")
            parsed.videos[key] = VideoRow(
                video_id=key,
                title=dims.get("title"),
                published_at=_parse_key(
                    path.name,
                    row_number,
                    "published_at",
                    dims.get("published_at", ""),
                    parse_publish_date,
                ),
                duration_s=_dimension_number(dims.get("duration_s")),
                metrics=values,
            )
    if not parsed.totals:
        raise ExportRejected(f"{path.name} has no 'Gesamt'/'Total' row")
    reconcile(parsed)
    return parsed


def _parse_row(file, row_number, row, mappings) -> tuple[dict[str, float], dict[str, str]]:
    values: dict[str, float] = {}
    dims: dict[str, str] = {}
    for cell, mapping in zip(row[1:], mappings[1:], strict=True):
        if mapping.kind == "dimension":
            dims[mapping.name] = cell.strip()
            continue
        try:
            value = mapping.parser(cell)
        except ValueParseError as exc:
            if mapping.kind == "unmapped":
                continue
            raise ExportRejected(
                str(ValueParseError(str(exc), file=file, row=row_number, column=mapping.header))
            ) from None
        if value is not None:
            values[mapping.name] = value
    return values, dims


def _parse_key(file, row_number, column, raw, parser):
    try:
        return parser(raw)
    except ValueParseError as exc:
        raise ExportRejected(
            str(ValueParseError(str(exc), file=file, row=row_number, column=column))
        ) from None


def _dimension_number(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def reconcile(parsed: ParsedExport) -> None:
    """Check additive metrics against the totals row: equal for Datum, upper bound for Videos."""
    rows = (
        list(parsed.daily.values())
        if parsed.folder.kind == "date"
        else [v.metrics for v in parsed.videos.values()]
    )
    for name, total in parsed.totals.items():
        metric = BY_NAME.get(name)
        if metric is None or not metric.additive:
            continue
        row_sum = sum(r.get(name, 0.0) for r in rows)
        tolerance = max(RECONCILE_ABSOLUTE, abs(total) * RECONCILE_RELATIVE)
        if parsed.folder.kind == "date":
            failed = abs(row_sum - total) > tolerance
            relation = "does not match"
        else:
            failed = row_sum - total > tolerance
            relation = "exceeds"
        if failed:
            raise ExportRejected(
                f"totals check failed for '{name}': sum of rows {_fmt(row_sum)} "
                f"{relation} total {_fmt(total)}"
            )


def _fmt(value: float) -> str:
    return f"{value:.0f}" if value == int(value) else f"{value:.4f}"

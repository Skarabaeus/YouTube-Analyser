"""Merge parsed exports into de-duplicated long-format records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from yta.ingest.tables import ParsedExport
from yta.metrics import BY_NAME


def precedence(export: ParsedExport) -> tuple:
    """Latest period end wins; ties go to the most recently modified export."""
    folder = export.folder
    return (folder.end, folder.mtime_ns, folder.name)


def _period_total_precedence(export: ParsedExport) -> tuple:
    """For totals rows of the same period, Datum beats Videos regardless of age."""
    return (export.folder.kind == "date", *precedence(export))


def _usable_period_total(export: ParsedExport, metric: str) -> bool:
    """Studio leaves distinct viewer counts at 0 in Videos totals rows; never use them."""
    definition = BY_NAME.get(metric)
    if export.folder.kind == "video" and definition is not None:
        return definition.aggregation != "period_total"
    return True


@dataclass
class VideoMeta:
    title: str | None
    published_at: date | None
    duration_s: float | None
    export_id: str


@dataclass
class MergedData:
    # (channel, date, metric) -> (value, export_id)
    daily: dict[tuple[str, date, str], tuple[float, str]] = field(default_factory=dict)
    # (channel, video_id, start, end, metric) -> (value, export_id)
    video_totals: dict[tuple[str, str, date, date, str], tuple[float, str]] = field(
        default_factory=dict
    )
    # (channel, video_id, start, end) -> VideoMeta
    video_meta: dict[tuple[str, str, date, date], VideoMeta] = field(default_factory=dict)
    # (channel, start, end, metric) -> (value, export_id)
    period_totals: dict[tuple[str, date, date, str], tuple[float, str]] = field(
        default_factory=dict
    )


def merge(exports: list[ParsedExport]) -> MergedData:
    """Apply exports in ascending precedence so later ones overwrite per key and metric."""
    merged = MergedData()
    for export in sorted(exports, key=precedence):
        folder = export.folder
        channel, start, end, export_id = folder.channel, folder.start, folder.end, folder.name
        for day, values in export.daily.items():
            for metric, value in values.items():
                merged.daily[(channel, day, metric)] = (value, export_id)
        for video in export.videos.values():
            key = (channel, video.video_id, start, end)
            merged.video_meta[key] = VideoMeta(
                video.title, video.published_at, video.duration_s, export_id
            )
            for metric, value in video.metrics.items():
                merged.video_totals[(*key, metric)] = (value, export_id)
    for export in sorted(exports, key=_period_total_precedence):
        folder = export.folder
        for metric, value in export.totals.items():
            if _usable_period_total(export, metric):
                key = (folder.channel, folder.start, folder.end, metric)
                merged.period_totals[key] = (value, folder.name)
    return merged

"""Metric registry: the single source of truth for headers, units, parsing and aggregation.

Aggregation rules for multi-day buckets:

* ``sum``          additive across days and videos.
* ``ratio``        recomputed as sum(numerator) / sum(denominator) over daily values.
* ``weighted``     average weighted by a base metric (e.g. views).
* ``period_total`` not additive (distinct counts); only valid from an export's totals row
                   for exactly the requested period.
* ``none``         unknown columns; available via SQL only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from yta.parsing import (
    Parser,
    parse_duration,
    parse_hours_to_minutes,
    parse_lenient,
    parse_number,
    parse_percent,
)

Aggregation = Literal["sum", "ratio", "weighted", "period_total", "none"]


@dataclass(frozen=True)
class Metric:
    name: str
    headers: tuple[str, ...]
    unit: str
    description: str
    parser: Parser = parse_number
    additive: bool = False
    aggregation: Aggregation = "sum"
    # SQL expressions over channel_daily columns, used when aggregation == "ratio"
    ratio: tuple[str, str] | None = None
    # base metric, used when aggregation == "weighted"
    weight: str | None = None


def _sum(name: str, headers: tuple[str, ...], unit: str, description: str, **kw) -> Metric:
    return Metric(name, headers, unit, description, additive=True, aggregation="sum", **kw)


def _distinct(name: str, headers: tuple[str, ...], description: str, **kw) -> Metric:
    return Metric(name, headers, "count", description, aggregation="period_total", **kw)


METRICS: tuple[Metric, ...] = (
    _sum("views", ("Aufrufe", "Views"), "count", "Views."),
    _sum("engaged_views", ("Engaged Views", "Engaged views"), "count", "Engaged views."),
    _sum(
        "watch_time_minutes",
        ("Wiedergabezeit (Stunden)", "Watch time (hours)"),
        "minutes",
        "Watch time (exported in hours, stored in minutes).",
        parser=parse_hours_to_minutes,
    ),
    Metric(
        "avg_view_duration_s",
        ("Durchschnittliche Wiedergabedauer", "Average view duration"),
        "seconds",
        "Average view duration.",
        parser=parse_duration,
        aggregation="ratio",
        ratio=("watch_time_minutes * 60", "views"),
    ),
    Metric(
        "avg_view_percentage",
        ("Durchschnittliche Wiedergabedauer in Prozent (%)", "Average percentage viewed (%)"),
        "fraction",
        "Average share of the video watched per view (0-1).",
        parser=parse_percent,
        aggregation="weighted",
        weight="views",
    ),
    _sum("subscribers_net", ("Abonnenten", "Subscribers"), "count", "Net subscribers."),
    _sum(
        "subscribers_gained",
        ("Gewonnene Abonnenten", "Subscribers gained"),
        "count",
        "Subscribers gained.",
    ),
    _sum(
        "subscribers_lost",
        ("Verlorene Abonnenten", "Subscribers lost"),
        "count",
        "Subscribers lost.",
    ),
    _sum("likes", ("Likes",), "count", "Likes."),
    _sum("dislikes", ("„Mag ich nicht“-Bewertungen", "Dislikes"), "count", "Dislikes."),
    Metric(
        "likes_ratio",
        ("Likes und Dislikes im Vergleich (%)", "Likes (vs. dislikes) (%)"),
        "fraction",
        "Likes as share of likes + dislikes (0-1).",
        parser=parse_percent,
        aggregation="ratio",
        ratio=("likes", "likes + dislikes"),
    ),
    _sum("shares", ("Geteilte Inhalte", "Shares"), "count", "Shares."),
    _sum("comments", ("Kommentare hinzugefügt", "Comments added"), "count", "Comments added."),
    _sum(
        "videos_published",
        ("Veröffentlichte Videos", "Videos published"),
        "count",
        "Videos published.",
    ),
    _sum("videos_added", ("Hinzugefügte Videos", "Videos added"), "count", "Videos added."),
    _sum(
        "impressions", ("Thumbnail-Impressionen", "Impressions"), "count", "Thumbnail impressions."
    ),
    Metric(
        "impressions_ctr",
        ("Thumbnail-Klickrate (%)", "Impressions click-through rate (%)"),
        "fraction",
        "Thumbnail click-through rate (0-1).",
        parser=parse_percent,
        aggregation="ratio",
        ratio=("impressions * impressions_ctr", "impressions"),
    ),
    _sum(
        "coviewing_views",
        ("Aufrufe (Co-Viewing)", "Views (co-viewing)"),
        "count",
        "Co-viewing views.",
    ),
    _sum("hypes", ("Hypes",), "count", "Hypes."),
    _sum("hype_points", ("Hype-Punkte", "Hype points"), "count", "Hype points."),
    Metric(
        "watched_to_end_pct",
        ("Bis zum Ende angesehen (%)",),
        "fraction",
        "Share of views watched to the end (Shorts) (0-1).",
        parser=parse_percent,
        aggregation="period_total",
    ),
    _distinct("unique_viewers", ("Einzelne Zuschauer", "Unique viewers"), "Unique viewers."),
    _distinct("unique_reach", ("Unique Reach",), "Unique reach."),
    _distinct("new_viewers", ("Neue Zuschauer", "New viewers"), "New viewers."),
    _distinct(
        "returning_viewers", ("Wiederkehrende Zuschauer", "Returning viewers"), "Returning viewers."
    ),
    _distinct("casual_viewers", ("Gelegentliche Zuschauer", "Casual viewers"), "Casual viewers."),
    _distinct("regular_viewers", ("Regelmäßige Zuschauer", "Regular viewers"), "Regular viewers."),
    Metric(
        "views_per_viewer",
        ("Aufrufe pro Zuschauer (Durchschnitt)", "Average views per viewer"),
        "ratio",
        "Average views per viewer.",
        aggregation="period_total",
    ),
)

BY_NAME: dict[str, Metric] = {m.name: m for m in METRICS}
BY_HEADER: dict[str, Metric] = {h.casefold(): m for m in METRICS for h in m.headers}

# Non-metric columns, per canonical name.
DIMENSION_HEADERS: dict[str, tuple[str, ...]] = {
    "date": ("Datum", "Date"),
    "video_id": ("Videos", "Content", "Video"),
    "title": ("Videotitel", "Video title"),
    "published_at": ("Veröffentlichungszeitpunkt des Videos", "Video publish time"),
    "duration_s": ("Dauer", "Duration"),
}
DIMENSION_BY_HEADER: dict[str, str] = {
    h.casefold(): name for name, headers in DIMENSION_HEADERS.items() for h in headers
}

TOTAL_ROW_LABELS = frozenset({"gesamt", "total"})

# Derived columns in channel_daily that are not exported by Studio.
CALCULATED_DAILY = {"impressions_clicks": "impressions * impressions_ctr"}


@dataclass(frozen=True)
class ColumnMapping:
    header: str
    kind: Literal["dimension", "metric", "unmapped"]
    name: str
    parser: Parser


def unmapped_name(header: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", header).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return f"x_{slug or 'column'}"


def map_header(header: str) -> ColumnMapping:
    key = header.strip().casefold()
    if key in DIMENSION_BY_HEADER:
        return ColumnMapping(header, "dimension", DIMENSION_BY_HEADER[key], parse_lenient)
    metric = BY_HEADER.get(key)
    if metric is not None:
        return ColumnMapping(header, "metric", metric.name, metric.parser)
    parser = parse_percent if "(%)" in header else parse_lenient
    return ColumnMapping(header, "unmapped", unmapped_name(header), parser)


def unit_of(name: str) -> str:
    metric = BY_NAME.get(name)
    if metric is not None:
        return metric.unit
    if name in CALCULATED_DAILY:
        return "count"
    return "unknown"

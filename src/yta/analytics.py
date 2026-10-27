"""Analysis functions behind the MCP tools. Pure functions over a Dataset snapshot.

Every result is wrapped by ``envelope`` so it states the covered date range, the last day
with data, the source exports and the units. Values carry a ``source``:

* ``export``        taken as exported (a single day's value)
* ``period_total``  taken from an export's totals row for exactly that period
* ``calculated``    computed by this server (sums over days, recomputed ratios, changes)
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from yta.dataset import MAX_ROWS, Dataset, to_json_value
from yta.metrics import BY_NAME, CALCULATED_DAILY, Metric, unit_of
from yta.periods import PeriodError, buckets, previous_period, previous_year, resolve

OVERVIEW_METRICS = (
    "views",
    "engaged_views",
    "watch_time_minutes",
    "avg_view_duration_s",
    "avg_view_percentage",
    "impressions",
    "impressions_ctr",
    "subscribers_gained",
    "subscribers_lost",
    "subscribers_net",
    "unique_viewers",
    "new_viewers",
    "returning_viewers",
    "casual_viewers",
    "regular_viewers",
    "likes",
    "comments",
    "shares",
)
VIDEO_METRICS = (
    "views",
    "engaged_views",
    "watch_time_minutes",
    "avg_view_duration_s",
    "avg_view_percentage",
    "impressions",
    "impressions_ctr",
    "subscribers_net",
    "likes",
    "comments",
    "shares",
    "unique_reach",
    "watched_to_end_pct",
)
IMPACT_METRICS = ("views", "impressions", "impressions_ctr", "subscribers_net")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


class ToolInputError(ValueError):
    """Invalid tool input; the message is shown to the client."""


# -- helpers ---------------------------------------------------------------------


def resolve_channel(ds: Dataset, channel: str | None) -> str:
    channels = ds.channels()
    if not channels:
        raise ToolInputError(
            "no accepted exports found; call dataset_status to see what was skipped or rejected"
        )
    if channel is None:
        if len(channels) > 1:
            raise ToolInputError(f"several channels available, pass one of: {channels}")
        return channels[0]
    if channel not in channels:
        raise ToolInputError(f"unknown channel '{channel}', available: {channels}")
    return channel


def _coverage(ds: Dataset, channel: str) -> tuple[date, date]:
    coverage = ds.coverage(channel)
    if coverage is None:
        raise ToolInputError(f"no daily data for channel '{channel}'; add a Studio 'Datum' export")
    return coverage


def _resolve_period(expression: str, coverage: tuple[date, date]) -> tuple[date, date]:
    try:
        return resolve(expression, *coverage)
    except PeriodError as exc:
        raise ToolInputError(str(exc)) from None


def _clip(start: date, end: date, coverage: tuple[date, date]) -> tuple[date, date] | None:
    clipped = (max(start, coverage[0]), min(end, coverage[1]))
    return None if clipped[0] > clipped[1] else clipped


def envelope(
    ds: Dataset,
    channel: str | None,
    period: tuple[date, date] | None,
    sources: set[str],
    unit_names: list[str] | tuple[str, ...],
    body: dict[str, Any],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    last_day = None
    if channel is not None:
        coverage = ds.coverage(channel)
        last_day = coverage[1].isoformat() if coverage else None
    return {
        "channel": channel,
        "period": None
        if period is None
        else {"start": period[0].isoformat(), "end": period[1].isoformat()},
        "last_data_day": last_day,
        "sources": sorted(sources),
        "units": {name: unit_of(name) for name in dict.fromkeys(unit_names)},
        **body,
        "notes": notes or [],
    }


def _daily_sources(ds: Dataset, channel: str, start: date, end: date) -> set[str]:
    rows = ds.fetch(
        "SELECT DISTINCT export_id FROM channel_daily_long "
        "WHERE channel = ? AND date BETWEEN ? AND ?",
        [channel, start, end],
    )
    return {r[0] for r in rows}


def _aggregate_expr(metric: Metric) -> str:
    """SQL aggregate over channel_daily for one metric (registry constants only)."""
    if metric.aggregation == "sum":
        return f"sum({metric.name})"
    if metric.aggregation == "ratio" and metric.ratio:
        numerator, denominator = metric.ratio
        return (
            f"sum({numerator}) / nullif(sum({denominator}) "
            f"FILTER (WHERE ({numerator}) IS NOT NULL), 0)"
        )
    if metric.aggregation == "weighted" and metric.weight:
        return (
            f"sum({metric.name} * {metric.weight}) / "
            f"nullif(sum({metric.weight}) FILTER (WHERE {metric.name} IS NOT NULL), 0)"
        )
    return "NULL"


def _period_total(
    ds: Dataset, channel: str, start: date, end: date, coverage: tuple[date, date], metric: str
) -> tuple[float | None, str | None]:
    """Exact-period lookup; export ranges beyond the data coverage are equivalent."""
    rows = ds.fetch(
        """
        SELECT value, export_id FROM channel_period_totals
        WHERE channel = ? AND metric = ?
          AND greatest(period_start, ?) = ? AND least(period_end, ?) = ?
        ORDER BY period_end DESC LIMIT 1
        """,
        [channel, metric, coverage[0], start, coverage[1], end],
    )
    return (rows[0][0], rows[0][1]) if rows else (None, None)


def _export_hint(channel: str, start: date, end: date) -> str:
    return (
        f"a Studio 'Datum' export for {start}..{end} "
        f"(folder 'Datum {start}_{end} {channel}') would provide it"
    )


def aggregate(
    ds: Dataset,
    channel: str,
    start: date,
    end: date,
    names: tuple[str, ...] | list[str],
    coverage: tuple[date, date],
) -> tuple[dict[str, dict[str, Any]], set[str], list[str]]:
    """Values for [start, end] (already clipped to coverage) with their sources."""
    values: dict[str, dict[str, Any]] = {}
    sources: set[str] = set()
    notes: list[str] = []
    single_day = start == end
    computable = [BY_NAME[n] for n in names if BY_NAME[n].aggregation != "period_total"]
    if single_day:
        row = ds.fetch_dicts(
            f"SELECT {', '.join(names)} FROM channel_daily WHERE channel = ? AND date = ?",  # noqa: S608
            [channel, start],
        )
        for name in names:
            value = row[0][name] if row else None
            values[name] = {"value": to_json_value(value), "source": "export"}
        sources |= _daily_sources(ds, channel, start, end)
        return values, sources, notes

    row: dict[str, Any] = {}
    if computable:
        selects = ", ".join(f"{_aggregate_expr(m)} AS {m.name}" for m in computable)
        row = ds.fetch_dicts(
            f"SELECT {selects} FROM channel_daily WHERE channel = ? AND date BETWEEN ? AND ?",  # noqa: S608
            [channel, start, end],
        )[0]
        sources |= _daily_sources(ds, channel, start, end)
    missing_distinct: list[str] = []
    for name in names:
        exact, export_id = _period_total(ds, channel, start, end, coverage, name)
        if exact is not None:
            values[name] = {"value": to_json_value(exact), "source": "period_total"}
            sources.add(export_id)
        elif BY_NAME[name].aggregation == "period_total":
            values[name] = {"value": None, "source": "period_total"}
            missing_distinct.append(name)
        else:
            values[name] = {"value": to_json_value(row[name]), "source": "calculated"}
    if missing_distinct:
        notes.append(
            f"{', '.join(missing_distinct)}: distinct counts cannot be summed over days; "
            + _export_hint(channel, start, end)
        )
    return values, sources, notes


def _change(current: Any, previous: Any) -> dict[str, Any]:
    if current is None or previous is None:
        return {"absolute": None, "relative": None, "source": "calculated"}
    absolute = current - previous
    relative = None if previous == 0 else absolute / previous
    return {
        "absolute": to_json_value(round(absolute, 6)),
        "relative": None if relative is None else round(relative, 4),
        "source": "calculated",
    }


def _check_metrics(metrics: list[str]) -> None:
    unknown = [m for m in metrics if m not in BY_NAME]
    if unknown:
        raise ToolInputError(
            f"unknown metric(s) {unknown}; available: {sorted(BY_NAME)}. "
            "Unmapped Studio columns (x_...) are available via query_sql on channel_daily_long."
        )


# -- tools -----------------------------------------------------------------------


def dataset_status(ds: Dataset, root_problem: str | None, rebuild_error: str | None) -> dict:
    exports = ds.fetch_dicts(
        "SELECT export_id, status, reason, dimension, channel, period_start, period_end, "
        "modified_at, unmapped_headers FROM exports ORDER BY export_id"
    )
    channels = []
    accepted: set[str] = set()
    for channel in ds.channels():
        coverage = ds.coverage(channel)
        per_metric = ds.fetch_dicts(
            "SELECT metric, min(date) AS first_day, max(date) AS last_day, count(*) AS days "
            "FROM channel_daily_long WHERE channel = ? GROUP BY metric ORDER BY metric",
            [channel],
        )
        channels.append(
            {
                "channel": channel,
                "first_day": coverage[0].isoformat() if coverage else None,
                "last_data_day": coverage[1].isoformat() if coverage else None,
                "metric_coverage": [
                    {k: to_json_value(v) for k, v in row.items()} for row in per_metric
                ],
            }
        )
    for export in exports:
        if export["status"] == "accepted":
            accepted.add(export["export_id"])
    notes = [p for p in (root_problem, rebuild_error) if p]
    single = channels[0]["channel"] if len(channels) == 1 else None
    first_days = [c["first_day"] for c in channels if c["first_day"]]
    last_days = [c["last_data_day"] for c in channels if c["last_data_day"]]
    period = (
        (date.fromisoformat(min(first_days)), date.fromisoformat(max(last_days)))
        if first_days
        else None
    )
    return envelope(
        ds,
        single,
        period,
        accepted,
        [m["metric"] for c in channels for m in c["metric_coverage"] if m["metric"] in BY_NAME],
        {
            "channels": channels,
            "exports": [{k: to_json_value(v) for k, v in e.items()} for e in exports],
        },
        notes,
    )


def channel_overview(
    ds: Dataset, period: str, compare_to: str | None = None, channel: str | None = None
) -> dict:
    channel = resolve_channel(ds, channel)
    coverage = _coverage(ds, channel)
    requested = _resolve_period(period, coverage)
    clipped = _clip(*requested, coverage)
    if clipped is None:
        raise ToolInputError(
            f"no data in {requested[0]}..{requested[1]}; "
            f"available coverage is {coverage[0]}..{coverage[1]}"
        )
    notes = []
    if clipped != requested:
        notes.append(f"requested {requested[0]}..{requested[1]} was limited to the data coverage")
    values, sources, agg_notes = aggregate(ds, channel, *clipped, OVERVIEW_METRICS, coverage)
    notes += agg_notes
    body: dict[str, Any] = {"metrics": values}

    if compare_to is not None:
        if compare_to == "previous_period":
            other = previous_period(*clipped)
        elif compare_to == "previous_year":
            other = previous_year(*clipped)
        else:
            raise ToolInputError("compare_to must be 'previous_period' or 'previous_year'")
        other_clipped = _clip(*other, coverage)
        if other_clipped is None:
            body["comparison"] = None
            notes.append(f"comparison period {other[0]}..{other[1]} has no data")
        else:
            if other_clipped != other:
                notes.append(
                    f"comparison period {other[0]}..{other[1]} is only partly covered by data"
                )
            other_values, other_sources, other_notes = aggregate(
                ds, channel, *other_clipped, OVERVIEW_METRICS, coverage
            )
            sources |= other_sources
            notes += other_notes
            body["comparison"] = {
                "period": {
                    "start": other_clipped[0].isoformat(),
                    "end": other_clipped[1].isoformat(),
                },
                "metrics": other_values,
            }
            body["change"] = {
                name: _change(values[name]["value"], other_values[name]["value"])
                for name in OVERVIEW_METRICS
            }
    return envelope(ds, channel, clipped, sources, OVERVIEW_METRICS, body, notes)


def channel_timeseries(
    ds: Dataset,
    metrics: list[str],
    period: str = "all",
    granularity: str = "day",
    channel: str | None = None,
) -> dict:
    if not metrics:
        raise ToolInputError("pass at least one metric")
    _check_metrics(metrics)
    channel = resolve_channel(ds, channel)
    coverage = _coverage(ds, channel)
    requested = _resolve_period(period, coverage)
    clipped = _clip(*requested, coverage)
    if clipped is None:
        raise ToolInputError(
            f"no data in {requested[0]}..{requested[1]}; "
            f"available coverage is {coverage[0]}..{coverage[1]}"
        )
    try:
        ranges = buckets(*clipped, granularity)
    except PeriodError as exc:
        raise ToolInputError(str(exc)) from None
    notes: list[str] = []
    truncated = len(ranges) > MAX_ROWS
    if truncated:
        ranges = ranges[-MAX_ROWS:]
        notes.append(f"only the last {MAX_ROWS} buckets are returned")

    rows = []
    sources: set[str] = set()
    column_sources: dict[str, set[str]] = {m: set() for m in metrics}
    missing_distinct: set[str] = set()
    for bucket_start, bucket_end in ranges:
        values, bucket_sources, _ = aggregate(
            ds, channel, bucket_start, bucket_end, metrics, coverage
        )
        sources |= bucket_sources
        row: dict[str, Any] = {"start": bucket_start.isoformat(), "end": bucket_end.isoformat()}
        for name in metrics:
            row[name] = values[name]["value"]
            column_sources[name].add(values[name]["source"])
            if (
                values[name]["value"] is None
                and BY_NAME[name].aggregation == "period_total"
                and bucket_start != bucket_end
            ):
                missing_distinct.add(name)
        rows.append(row)
    if missing_distinct:
        notes.append(
            f"{', '.join(sorted(missing_distinct))}: null where no export covers exactly that "
            "bucket; distinct counts cannot be summed over days. One Studio 'Datum' export per "
            "bucket (e.g. per calendar month) fills them."
        )
    return envelope(
        ds,
        channel,
        clipped,
        sources,
        metrics,
        {
            "granularity": granularity,
            "column_sources": {k: sorted(v) for k, v in column_sources.items()},
            "rows": rows,
            "truncated": truncated,
        },
        notes,
    )


def video_performance(
    ds: Dataset,
    sort_by: str = "views",
    limit: int = 10,
    content_type: str | None = None,
    channel: str | None = None,
) -> dict:
    _check_metrics([sort_by])
    if sort_by not in VIDEO_METRICS:
        raise ToolInputError(f"sort_by must be one of {list(VIDEO_METRICS)}")
    if content_type not in (None, "short", "long"):
        raise ToolInputError("content_type must be 'short', 'long' or omitted")
    if not 1 <= limit <= MAX_ROWS:
        raise ToolInputError(f"limit must be between 1 and {MAX_ROWS}")
    channel = resolve_channel(ds, channel)
    latest = ds.fetch(
        "SELECT period_start, period_end, export_id FROM video_meta WHERE channel = ? "
        "ORDER BY period_end DESC, period_end - period_start DESC LIMIT 1",
        [channel],
    )
    if not latest:
        raise ToolInputError("no 'Videos' export loaded; add a Studio 'Videos' export")
    start, end, export_id = latest[0]
    columns = ", ".join(VIDEO_METRICS)
    filters = "channel = ? AND period_start = ? AND period_end = ?"
    params: list[Any] = [channel, start, end]
    if content_type:
        filters += " AND content_type = ?"
        params.append(content_type)
    rows = ds.fetch_dicts(
        "SELECT video_id, title, published_at, duration_s, content_type, "  # noqa: S608
        f"{columns} FROM video_totals WHERE {filters} "
        f"ORDER BY {sort_by} DESC NULLS LAST, views DESC NULLS LAST LIMIT ?",
        [*params, limit],
    )
    videos = [{k: to_json_value(v) for k, v in row.items()} for row in rows]
    notes = [
        "values are totals for the export period; ratios as exported by Studio",
        "content_type is derived from duration (short = up to 180 s)",
    ]
    return envelope(
        ds,
        channel,
        (start, end),
        {export_id},
        ("duration_s", *VIDEO_METRICS),
        {"sort_by": sort_by, "videos": videos},
        notes,
    )


def publishing_impact(ds: Dataset, window_days: int = 7, channel: str | None = None) -> dict:
    if not 1 <= window_days <= 90:
        raise ToolInputError("window_days must be between 1 and 90")
    channel = resolve_channel(ds, channel)
    coverage = _coverage(ds, channel)
    publish_days = [
        r[0]
        for r in ds.fetch(
            "SELECT date FROM channel_daily WHERE channel = ? AND videos_published > 0 "
            "ORDER BY date",
            [channel],
        )
    ]
    events = []
    sources: set[str] = set()
    notes: list[str] = []
    for day in publish_days[-MAX_ROWS:]:
        before = (day - timedelta(days=window_days), day - timedelta(days=1))
        after = (day, day + timedelta(days=window_days - 1))
        before_clip, after_clip = _clip(*before, coverage), _clip(*after, coverage)
        before_values = after_values = {}
        if before_clip:
            before_values, s1, _ = aggregate(ds, channel, *before_clip, IMPACT_METRICS, coverage)
            sources |= s1
        if after_clip:
            after_values, s2, _ = aggregate(ds, channel, *after_clip, IMPACT_METRICS, coverage)
            sources |= s2
        published = ds.fetch_dicts(
            "SELECT DISTINCT video_id, title FROM video_meta "
            "WHERE channel = ? AND published_at = ? ORDER BY video_id",
            [channel, day],
        )

        def value(values, name):
            return values.get(name, {}).get("value")

        events.append(
            {
                "publish_date": day.isoformat(),
                "videos": published,
                "complete": before_clip == before and after_clip == after,
                "before": {"start": before[0].isoformat(), "end": before[1].isoformat()}
                | {m: value(before_values, m) for m in IMPACT_METRICS},
                "after": {"start": after[0].isoformat(), "end": after[1].isoformat()}
                | {m: value(after_values, m) for m in IMPACT_METRICS},
                "change": {
                    m: _change(value(after_values, m), value(before_values, m))
                    for m in IMPACT_METRICS
                },
            }
        )
    if len(publish_days) > MAX_ROWS:
        notes.append(f"only the last {MAX_ROWS} publish days are returned")
    notes.append(
        "after window starts on the publish day; values are calculated from daily data; "
        "complete=false means a window is not fully covered by data"
    )
    period = (
        (publish_days[0] - timedelta(days=window_days), publish_days[-1])
        if publish_days
        else coverage
    )
    return envelope(
        ds,
        channel,
        period,
        sources,
        IMPACT_METRICS,
        {"window_days": window_days, "events": events},
        notes,
    )


def query_sql(ds: Dataset, sql: str) -> dict:
    result = ds.query_sql(sql)
    accepted = {r[0] for r in ds.fetch("SELECT export_id FROM exports WHERE status = 'accepted'")}
    channels = ds.channels()
    channel = channels[0] if len(channels) == 1 else None
    coverage = ds.coverage(channel) if channel else None
    unit_names = [c for c in result.columns if c in BY_NAME or c in CALCULATED_DAILY]
    return envelope(
        ds,
        channel,
        coverage,
        accepted,
        unit_names,
        {"columns": result.columns, "rows": result.rows, "truncated": result.truncated},
        ["fractions (0-1) for ratio columns; see resource yta://schema for definitions"],
    )


def validate_video_id(video_id: str) -> str:
    if not VIDEO_ID_RE.match(video_id):
        raise ToolInputError("video_id must be a YouTube video id (letters, digits, '-' and '_')")
    return video_id

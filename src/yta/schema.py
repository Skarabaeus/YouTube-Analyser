"""Human- and model-readable description of the queryable views (resource yta://schema)."""

from __future__ import annotations

from yta.dataset import Dataset
from yta.metrics import CALCULATED_DAILY, METRICS

AGGREGATION_HELP = {
    "sum": "additive: sum over days",
    "ratio": "ratio: recompute as sum(numerator) / sum(denominator); never average",
    "weighted": "average weighted by the base metric",
    "period_total": "distinct count: NOT additive; use channel_period_totals for exact periods",
}

VIEWS = {
    "channel_daily": (
        "One row per channel and day. Key: channel, date. One column per metric below, "
        f"plus calculated: {', '.join(f'{k} = {v}' for k, v in CALCULATED_DAILY.items())}."
    ),
    "channel_daily_long": (
        "Same data in long format: channel, date, metric, value, export_id. Also contains "
        "Studio columns without a known mapping, named x_<header>."
    ),
    "channel_period_totals": (
        "Totals rows of the exports: channel, period_start, period_end, metric, value, "
        "export_id. The only correct source for distinct counts (unique/new/returning/casual/"
        "regular viewers) over more than one day."
    ),
    "video_totals": (
        "One row per video and export period: video_id, title, published_at, duration_s, "
        "content_type ('short' = up to 180 s, else 'long'), period_start, period_end, plus the "
        "metric columns. Totals for the export period, not daily values."
    ),
    "exports": (
        "Export folders and their status: export_id, status (accepted/rejected/unsupported/"
        "skipped), reason, dimension, kind, channel, period_start, period_end, modified_at, "
        "unmapped_headers."
    ),
}


def render(ds: Dataset) -> str:
    lines = ["# YouTube Studio export dataset", "", "## Views (DuckDB SQL, read-only)", ""]
    lines += [f"- `{name}`: {text}" for name, text in VIEWS.items()]
    lines += [
        "",
        "## Metric columns",
        "",
        "| column | unit | aggregation | description |",
        "|---|---|---|---|",
    ]
    for metric in METRICS:
        how = AGGREGATION_HELP[metric.aggregation]
        if metric.ratio:
            how += f" ({metric.ratio[0]} / {metric.ratio[1]})"
        if metric.weight:
            how += f" (weight: {metric.weight})"
        lines.append(f"| `{metric.name}` | {metric.unit} | {how} | {metric.description} |")
    lines += [
        "",
        "Fractions are 0-1 (e.g. impressions_ctr 0.054 = 5.4 %). Durations are seconds, "
        "watch time is minutes.",
        "",
        "## Coverage",
        "",
    ]
    for channel in ds.channels():
        coverage = ds.coverage(channel)
        span = f"{coverage[0]}..{coverage[1]}" if coverage else "no daily data"
        lines.append(f"- `{channel}`: {span}")
    return "\n".join(lines) + "\n"

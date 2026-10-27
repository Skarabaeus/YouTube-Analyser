import json
import socket
from datetime import date, timedelta
from pathlib import Path

import anyio
import pytest
from conftest import FIXTURES, write_export
from mcp import Client

from yta.dataset import MAX_ROWS
from yta.mcp_server import DEFAULT_EXPORTS_DIR, create_server, exports_root

CH = "Example Channel"
ALL_TOOLS = {
    "dataset_status",
    "channel_overview",
    "channel_timeseries",
    "video_performance",
    "publishing_impact",
    "query_sql",
}


def call(root: Path, name: str, args: dict | None = None):
    async def run():
        async with Client(create_server(root)) as client:
            return await client.call_tool(name, args or {})

    return anyio.run(run)


def ok(root: Path, name: str, args: dict | None = None) -> dict:
    result = call(root, name, args)
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def error(root: Path, name: str, args: dict | None = None) -> str:
    result = call(root, name, args)
    assert result.is_error
    return result.content[0].text


def with_client(root: Path, fn):
    async def run():
        async with Client(create_server(root)) as client:
            return await fn(client)

    return anyio.run(run)


def daily_export(root: Path, start: date, end: date, *, views=10, extra_folder="", unique=None):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    header = [
        "Datum",
        "Aufrufe",
        "Thumbnail-Impressionen",
        "Thumbnail-Klickrate (%)",
        "Einzelne Zuschauer",
        "Veröffentlichte Videos",
    ]
    rows = [[d.isoformat(), views, 100, 5, 3, ""] for d in days]
    total = [
        "Gesamt",
        views * len(days),
        100 * len(days),
        5,
        unique if unique is not None else 3 * len(days) // 2,
        0,
    ]
    write_export(root, f"Datum {start}_{end} {CH}{extra_folder}", header, [total, *rows])


BASIC = FIXTURES / "basic"


# --- 5.1 ---------------------------------------------------------------------------


def test_lists_all_tools():
    tools = with_client(BASIC, lambda c: c.list_tools())
    assert {t.name for t in tools.tools} == ALL_TOOLS


def test_dataset_status_lists_accepted_and_rejected_exports(tmp_path):
    daily_export(tmp_path, date(2026, 1, 1), date(2026, 1, 5))
    write_export(
        tmp_path,
        f"Datum 2026-02-01_2026-02-02 {CH}",
        ["Datum", "Aufrufe"],
        [["Gesamt", 99], ["2026-02-01", 1], ["2026-02-02", 1]],
    )
    status = ok(tmp_path, "dataset_status")
    by_id = {e["export_id"]: e for e in status["exports"]}
    assert by_id[f"Datum 2026-01-01_2026-01-05 {CH}"]["status"] == "accepted"
    rejected = by_id[f"Datum 2026-02-01_2026-02-02 {CH}"]
    assert rejected["status"] == "rejected" and "totals check failed" in rejected["reason"]
    assert status["channels"][0]["last_data_day"] == "2026-01-05"


def test_missing_root_is_reported(tmp_path):
    status = ok(tmp_path / "missing", "dataset_status")
    assert status["exports"] == [] and "does not exist" in status["notes"][0]


def test_exports_root_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("YTA_EXPORTS_DIR", str(tmp_path))
    assert exports_root() == tmp_path
    monkeypatch.delenv("YTA_EXPORTS_DIR")
    assert exports_root() == Path(DEFAULT_EXPORTS_DIR)


# --- 5.2 ---------------------------------------------------------------------------


def test_overview_last_28_days_vs_previous_period(tmp_path):
    daily_export(tmp_path, date(2026, 1, 1), date(2026, 2, 28))
    result = ok(
        tmp_path, "channel_overview", {"period": "last_28_days", "compare_to": "previous_period"}
    )
    assert result["period"] == {"start": "2026-02-01", "end": "2026-02-28"}
    assert result["comparison"]["period"] == {"start": "2026-01-04", "end": "2026-01-31"}
    assert result["metrics"]["views"] == {"value": 280, "source": "calculated"}
    assert result["comparison"]["metrics"]["views"]["value"] == 280
    assert result["change"]["views"] == {"absolute": 0, "relative": 0.0, "source": "calculated"}
    assert set(result["metrics"]) >= {
        "views",
        "impressions",
        "impressions_ctr",
        "subscribers_net",
        "unique_viewers",
        "new_viewers",
    }


def test_overview_period_without_data_states_coverage(tmp_path):
    daily_export(tmp_path, date(2026, 1, 1), date(2026, 1, 31))
    message = error(tmp_path, "channel_overview", {"period": "2020"})
    assert "2026-01-01..2026-01-31" in message


def test_overview_matching_full_export_uses_totals_row():
    result = ok(BASIC, "channel_overview", {"period": "all"})
    assert result["metrics"]["views"] == {"value": 1370, "source": "period_total"}
    assert result["metrics"]["unique_viewers"] == {"value": 500, "source": "period_total"}


# --- 5.3 ---------------------------------------------------------------------------


def test_monthly_ctr_is_impression_weighted(tmp_path):
    header = ["Datum", "Thumbnail-Impressionen", "Thumbnail-Klickrate (%)"]
    rows = [["2026-02-28", 50, 4], ["2026-03-01", 1000, 10], ["2026-03-02", 100, 2]]
    write_export(
        tmp_path, f"Datum 2026-02-28_2026-03-02 {CH}", header, [["Gesamt", 1150, 9.04], *rows]
    )
    result = ok(
        tmp_path,
        "channel_timeseries",
        {"metrics": ["impressions", "impressions_ctr"], "period": "all", "granularity": "month"},
    )
    march = result["rows"][1]
    assert march["start"] == "2026-03-01"
    assert march["impressions_ctr"] == pytest.approx((1000 * 0.10 + 100 * 0.02) / 1100)
    assert march["impressions_ctr"] != pytest.approx((0.10 + 0.02) / 2)
    assert "calculated" in result["column_sources"]["impressions_ctr"]  # Feb bucket is 1 day


def test_unknown_metric_lists_available_metrics():
    message = error(BASIC, "channel_timeseries", {"metrics": ["followers"]})
    assert "followers" in message and "impressions_ctr" in message


def test_daily_series_uses_exported_values():
    result = ok(BASIC, "channel_timeseries", {"metrics": ["views"], "period": "all"})
    assert {r["start"]: r["views"] for r in result["rows"]}["2026-01-05"] == 300
    assert result["column_sources"]["views"] == ["export"]


# --- 5.4 non-additive metrics --------------------------------------------------------


@pytest.fixture
def monthly_root(tmp_path):
    daily_export(tmp_path, date(2026, 1, 1), date(2026, 3, 31), unique=999)
    daily_export(tmp_path, date(2026, 1, 1), date(2026, 1, 31), extra_folder="-2", unique=41)
    daily_export(tmp_path, date(2026, 2, 1), date(2026, 2, 28), extra_folder="-3", unique=37)
    return tmp_path


def test_non_additive_metric_from_matching_export(monthly_root):
    result = ok(monthly_root, "channel_overview", {"period": "2026-02"})
    assert result["metrics"]["unique_viewers"] == {"value": 37, "source": "period_total"}


def test_non_additive_metric_without_matching_export_is_null_with_hint(monthly_root):
    result = ok(monthly_root, "channel_overview", {"period": "last_28_days"})
    assert result["metrics"]["unique_viewers"] == {"value": None, "source": "period_total"}
    assert any("Datum 2026-03-04_2026-03-31" in note for note in result["notes"])


def test_monthly_series_of_non_additive_metric(monthly_root):
    result = ok(
        monthly_root,
        "channel_timeseries",
        {"metrics": ["unique_viewers"], "period": "2026", "granularity": "month"},
    )
    values = {r["start"]: r["unique_viewers"] for r in result["rows"]}
    assert values == {"2026-01-01": 41, "2026-02-01": 37, "2026-03-01": None}


def test_non_additive_metric_is_never_summed(tmp_path):
    daily_export(tmp_path, date(2026, 1, 1), date(2026, 1, 31))
    result = ok(
        tmp_path,
        "channel_timeseries",
        {"metrics": ["unique_viewers"], "period": "all", "granularity": "week"},
    )
    assert all(r["unique_viewers"] is None for r in result["rows"] if r["start"] != r["end"])


# --- 5.5 video_performance ---------------------------------------------------------


def test_top_videos_by_ctr():
    result = ok(BASIC, "video_performance", {"sort_by": "impressions_ctr", "limit": 2})
    assert [v["video_id"] for v in result["videos"]] == ["vidLong0001", "vidOld00003"]
    assert result["period"] == {"start": "2026-01-01", "end": "2026-01-10"}


def test_shorts_only():
    result = ok(BASIC, "video_performance", {"content_type": "short"})
    assert [v["video_id"] for v in result["videos"]] == ["vidShort002"]
    assert all(v["duration_s"] <= 180 for v in result["videos"])


# --- 5.6 publishing_impact -----------------------------------------------------------


def test_publishing_impact_two_day_window():
    result = ok(BASIC, "publishing_impact", {"window_days": 2})
    first, second = result["events"]
    assert first["publish_date"] == "2026-01-05"
    assert (first["before"]["views"], first["after"]["views"]) == (140, 460)
    assert first["change"]["views"]["absolute"] == 320
    assert first["videos"] == [{"video_id": "vidLong0001", "title": "Long video about lenses"}]
    assert (second["before"]["views"], second["after"]["views"]) == (350, 190)
    assert (second["before"]["impressions"], second["after"]["impressions"]) == (2750, 1650)


# --- 5.7 query_sql + schema ------------------------------------------------------------


def test_query_sql_tool():
    result = ok(
        BASIC,
        "query_sql",
        {"sql": "SELECT date, views FROM channel_daily ORDER BY views DESC LIMIT 3"},
    )
    assert result["columns"] == ["date", "views"] and len(result["rows"]) == 3
    assert "only SELECT" in error(BASIC, "query_sql", {"sql": "DROP TABLE channel_daily_long"})
    assert error(BASIC, "query_sql", {"sql": "SELECT * FROM read_csv('/etc/passwd')"})


def test_schema_resource_lists_views_and_columns():
    async def read(client):
        return await client.read_resource("yta://schema")

    text = with_client(BASIC, read).contents[0].text
    for view in (
        "channel_daily",
        "channel_daily_long",
        "channel_period_totals",
        "video_totals",
        "exports",
    ):
        assert f"`{view}`" in text
    assert "| `impressions_ctr` | fraction |" in text
    assert "Fractions are 0-1" in text


# --- 5.8 prompts -------------------------------------------------------------------------


def test_prompts_are_listed_and_static():
    async def prompts(client):
        listed = await client.list_prompts()
        deep = await client.get_prompt("video_deep_dive", {"video_id": "vidShort002"})
        weekly = await client.get_prompt("weekly_review", {})
        return listed, deep, weekly

    listed, deep, weekly = with_client(BASIC, prompts)
    assert {p.name for p in listed.prompts} == {
        "weekly_review",
        "video_deep_dive",
        "growth_opportunities",
    }
    texts = deep.messages[0].content.text + weekly.messages[0].content.text
    assert "vidShort002" in texts
    assert "Ignore previous instructions" not in texts


def test_untrusted_title_only_appears_as_data():
    result = ok(BASIC, "video_performance", {"content_type": "short"})
    assert result["videos"][0]["title"].startswith("Ignore previous instructions")
    tools = with_client(BASIC, lambda c: c.list_tools())
    assert all("Ignore previous" not in (t.description or "") for t in tools.tools)


# --- 5.9 response conventions -----------------------------------------------------------

CONTRACT_CALLS = [
    ("dataset_status", {}),
    ("channel_overview", {"period": "all", "compare_to": "previous_year"}),
    ("channel_timeseries", {"metrics": ["views", "impressions_ctr"], "granularity": "week"}),
    ("video_performance", {"limit": 3}),
    ("publishing_impact", {"window_days": 3}),
    ("query_sql", {"sql": "SELECT * FROM range(2000)"}),
]


@pytest.mark.parametrize(("name", "args"), CONTRACT_CALLS)
def test_response_conventions(name, args):
    result = ok(BASIC, name, args)
    assert set(result["period"]) == {"start", "end"}
    assert result["last_data_day"] == "2026-01-10"
    assert result["sources"] and all(isinstance(s, str) for s in result["sources"])
    assert isinstance(result["units"], dict)
    assert isinstance(result["notes"], list)
    for key in ("rows", "videos", "events", "exports"):
        assert len(result.get(key, [])) <= MAX_ROWS
    for change in result.get("change", {}).values():
        assert change["source"] == "calculated"
    for event in result.get("events", []):
        assert all(c["source"] == "calculated" for c in event["change"].values())


def test_all_tools_are_covered_by_contract_test():
    assert {name for name, _ in CONTRACT_CALLS} == ALL_TOOLS


# --- 5.10 no network ---------------------------------------------------------------------


def test_no_network_use(monkeypatch):
    real_socket = socket.socket

    def guarded(family=socket.AF_INET, *args, **kwargs):
        # The asyncio event loop needs a local AF_UNIX socketpair; anything else is network.
        if family != socket.AF_UNIX:
            raise AssertionError("network access attempted")
        return real_socket(family, *args, **kwargs)

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", guarded)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    for name, args in CONTRACT_CALLS:
        ok(BASIC, name, args)

import os
import shutil
from datetime import date

import pytest
from conftest import FIXTURES

from yta import dataset as dataset_module
from yta.dataset import MAX_ROWS, DatasetManager, QueryError

CH = "Example Channel"


@pytest.fixture
def ds():
    return DatasetManager(FIXTURES / "basic").get()


# --- 4.1 views -----------------------------------------------------------------


def test_channel_daily_view_is_wide_with_calculated_clicks(ds):
    row = ds.query_sql(
        "SELECT views, impressions, impressions_ctr, impressions_clicks, avg_view_duration_s "
        "FROM channel_daily WHERE date = DATE '2026-01-05'"
    ).rows[0]
    assert row == [300, 1250, 0.06, 75, 180]


def test_channel_daily_long_keeps_unmapped_metrics(ds):
    rows = ds.query_sql(
        "SELECT value FROM channel_daily_long "
        "WHERE metric = 'x_zukunftsmetrik' AND date = '2026-01-05'"
    ).rows
    assert rows == [[10]]


def test_video_totals_view_joins_metadata(ds):
    rows = ds.query_sql(
        "SELECT video_id, content_type, published_at, views FROM video_totals ORDER BY video_id"
    ).rows
    assert rows == [
        ["vidLong0001", "long", "2026-01-05", 700],
        ["vidOld00003", "long", "2025-12-01", 150],
        ["vidShort002", "short", "2026-01-09", 400],
    ]


def test_period_totals_and_exports_views(ds):
    assert ds.query_sql(
        "SELECT value FROM channel_period_totals WHERE metric = 'unique_viewers'"
    ).rows == [[500]]
    statuses = dict(ds.query_sql("SELECT export_id, status FROM exports").rows)
    assert statuses[f"Datum 2026-01-01_2026-01-10 {CH}"] == "accepted"
    assert statuses["notes"] == "skipped"


def test_coverage(ds):
    assert ds.coverage(CH) == (date(2026, 1, 1), date(2026, 1, 10))


# --- 4.2 query_sql sandbox -------------------------------------------------------


def test_valid_query_returns_rows_and_columns(ds):
    result = ds.query_sql("SELECT date, views FROM channel_daily ORDER BY views DESC LIMIT 3")
    assert result.columns == ["date", "views"]
    assert len(result.rows) == 3 and not result.truncated


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE channel_daily_long",
        "SELECT 1; DELETE FROM channel_daily_long",
        "DELETE FROM channel_daily_long",
        "INSERT INTO channel_daily_long VALUES ('x', DATE '2026-01-01', 'views', 1, 'x')",
        "UPDATE channel_daily_long SET value = 0",
        "CREATE TABLE x AS SELECT 1",
        "COPY channel_daily_long TO '/tmp/x.csv'",
        "ATTACH '/tmp/x.db'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET enable_external_access = true",
        "PRAGMA enable_profiling",
        "EXPORT DATABASE '/tmp/x'",
        "SELECT 1) AS q; DROP TABLE channel_daily_long; SELECT * FROM (SELECT 1",
    ],
)
def test_non_select_statements_are_rejected_and_data_unchanged(ds, sql):
    before = ds.query_sql("SELECT count(*), sum(value) FROM channel_daily_long").rows
    with pytest.raises(QueryError):
        ds.query_sql(sql)
    assert ds.query_sql("SELECT count(*), sum(value) FROM channel_daily_long").rows == before


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM glob('/*')",
        "SELECT * FROM query('DROP TABLE channel_daily_long')",
        "SELECT * FROM read_parquet('https://example.com/x.parquet')",
    ],
)
def test_file_and_network_access_is_blocked(ds, sql):
    with pytest.raises(QueryError):
        ds.query_sql(sql)


def test_oversized_result_is_truncated(ds):
    result = ds.query_sql("SELECT * FROM range(10000)")
    assert len(result.rows) == MAX_ROWS and result.truncated


def test_long_running_query_is_interrupted(ds, monkeypatch):
    monkeypatch.setattr(dataset_module, "QUERY_TIMEOUT_S", 0.2)
    with pytest.raises(QueryError, match="stopped"):
        ds.query_sql(
            "SELECT count(*) FROM range(100000) a, range(100000) b "
            "WHERE (a.range * b.range) % 7 = 3"
        )


def test_trailing_semicolon_is_fine(ds):
    assert ds.query_sql("SELECT 1 AS one;").rows == [[1]]


# --- 4.3 refresh -----------------------------------------------------------------


def _snapshot(root):
    return {
        p.relative_to(root): (p.stat().st_mtime_ns, p.read_bytes())
        for p in root.rglob("*")
        if p.is_file()
    }


def test_refresh_picks_up_added_changed_and_removed_exports(basic_root):
    manager = DatasetManager(basic_root)
    first = manager.get()
    assert manager.get() is first  # unchanged root: no rebuild

    added = basic_root / f"Datum 2026-02-01_2026-02-02 {CH}"
    shutil.copytree(basic_root / f"Datum 2026-01-01_2026-01-10 {CH}", added)
    table = added / "Tabellendaten.csv"
    table.write_text("Datum,Aufrufe\nGesamt,5\n2026-02-01,2\n2026-02-02,3\n", encoding="utf-8")
    second = manager.get()
    assert second is not first
    assert second.coverage(CH)[1] == date(2026, 2, 2)

    table.write_text("Datum,Aufrufe\nGesamt,9\n2026-02-01,4\n2026-02-02,5\n", encoding="utf-8")
    os.utime(table, ns=(10**19, 10**19))
    third = manager.get()
    assert third.query_sql("SELECT views FROM channel_daily WHERE date = '2026-02-02'").rows == [
        [5]
    ]

    shutil.rmtree(added)
    assert manager.get().coverage(CH)[1] == date(2026, 1, 10)


def test_failed_rebuild_keeps_previous_dataset(basic_root, monkeypatch):
    manager = DatasetManager(basic_root)
    first = manager.get()
    (basic_root / f"Datum 2026-03-01_2026-03-01 {CH}").mkdir()

    def boom(_root):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(dataset_module, "load", boom)
    assert manager.get() is first
    assert "disk on fire" in manager.last_error


def test_source_files_are_not_modified(basic_root):
    before = _snapshot(basic_root)
    manager = DatasetManager(basic_root)
    manager.get().query_sql("SELECT * FROM channel_daily")
    manager.get()
    assert _snapshot(basic_root) == before


def test_huge_cells_are_truncated(ds):
    result = ds.query_sql("SELECT repeat('x', 5000000) AS big")
    (cell,) = result.rows[0]
    assert len(cell) <= dataset_module.MAX_CELL_CHARS + 20 and cell.endswith("[truncated]")


def test_result_size_budget_truncates_rows(ds):
    result = ds.query_sql("SELECT repeat('x', 900) AS s FROM range(400)")
    assert result.truncated
    assert len(result.rows) < 400


def test_too_many_columns_are_rejected(ds):
    columns = ", ".join(f"{i} AS c{i}" for i in range(150))
    with pytest.raises(QueryError, match="columns"):
        ds.query_sql(f"SELECT {columns}")


def test_memory_and_thread_limits_are_locked(ds):
    limits = dict(
        ds.query_sql(
            "SELECT name, value FROM duckdb_settings() WHERE name IN ('threads', 'memory_limit')"
        ).rows
    )
    assert limits["threads"] == "2"
    with pytest.raises(QueryError):
        ds.query_sql("SET memory_limit = '64GB'")

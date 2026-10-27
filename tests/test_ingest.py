import os
from datetime import date
from pathlib import Path

from conftest import DATUM_HEADER, FIXTURES, write_export

from yta.ingest.discovery import discover
from yta.ingest.loader import load

CH = "Example Channel"


def by_name(result):
    return {f.name: f for f in result.folders}


# --- discovery ---------------------------------------------------------------


def test_valid_folder_is_registered_with_dimension_period_and_channel(tmp_path):
    write_export(
        tmp_path,
        f"Datum 2025-05-23_2026-10-27 {CH}-3",
        DATUM_HEADER,
        [["Gesamt", 1, 1, 1, 1], ["2025-05-23", 1, 1, 1, 1]],
    )
    folders, problem = discover(tmp_path)
    assert problem is None
    (folder,) = folders
    assert folder.kind == "date"
    assert (folder.start, folder.end) == (date(2025, 5, 23), date(2026, 10, 27))
    assert folder.channel == CH


def test_unrelated_entries_are_skipped_with_reason():
    result = load(FIXTURES / "basic")
    folders = by_name(result)
    assert folders["notes"].status == "skipped" and folders["notes"].reason
    assert folders["stray.txt"].status == "skipped"


def test_missing_root_gives_empty_dataset_and_problem(tmp_path):
    result = load(tmp_path / "does-not-exist")
    assert result.folders == [] and result.parsed == []
    assert "does not exist" in result.root_problem


def test_unsupported_dimension_contributes_no_rows():
    result = load(FIXTURES / "basic")
    traffic = by_name(result)[f"Trafficquelle 2026-01-01_2026-01-10 {CH}"]
    assert traffic.status == "unsupported"
    assert all(p.folder is not traffic for p in result.parsed)


# --- table parsing -----------------------------------------------------------


def _daily(result, day, metric):
    return result.merged.daily.get((CH, date.fromisoformat(day), metric), (None,))[0]


def test_german_headers_map_to_canonical_metrics():
    result = load(FIXTURES / "basic")
    assert _daily(result, "2026-01-05", "views") == 300
    assert _daily(result, "2026-01-05", "impressions_ctr") == 0.06


def test_english_export_equals_german_export():
    def known(result):
        return {k: v[0] for k, v in result.merged.daily.items() if not k[2].startswith("x_")}

    assert known(load(FIXTURES / "basic")) == known(load(FIXTURES / "english"))


def test_unknown_header_is_kept_and_reported():
    result = load(FIXTURES / "basic")
    datum = by_name(result)[f"Datum 2026-01-01_2026-01-10 {CH}"]
    assert datum.unmapped_headers == ["Zukunftsmetrik"]
    assert _daily(result, "2026-01-05", "x_zukunftsmetrik") == 10


def test_empty_cell_is_missing_and_zero_is_zero():
    result = load(FIXTURES / "basic")
    assert _daily(result, "2026-01-10", "views") == 0
    assert _daily(result, "2026-01-10", "avg_view_duration_s") is None


def test_malformed_number_rejects_export_with_location(tmp_path):
    write_export(
        tmp_path,
        f"Datum 2026-01-01_2026-01-02 {CH}",
        DATUM_HEADER,
        [["Gesamt", 2, 2, 1, 1], ["2026-01-01", "abc", 1, 1, 1], ["2026-01-02", 1, 1, 1, 1]],
    )
    (folder,) = load(tmp_path).folders
    assert folder.status == "rejected"
    assert "row 3" in folder.reason and "Aufrufe" in folder.reason


def test_row_order_does_not_matter(tmp_path):
    rows = [["2026-01-01", 5, 50, 2, 4], ["2026-01-02", 7, 70, 3, 5]]
    write_export(
        tmp_path / "a",
        f"Datum 2026-01-01_2026-01-02 {CH}",
        DATUM_HEADER,
        [["Gesamt", 12, 120, 2.58, 8], *rows],
    )
    write_export(
        tmp_path / "b",
        f"Datum 2026-01-01_2026-01-02 {CH}",
        DATUM_HEADER,
        [*reversed(rows), ["Gesamt", 12, 120, 2.58, 8]],
    )
    values = lambda r: {k: v[0] for k, v in r.merged.daily.items()}  # noqa: E731
    assert values(load(tmp_path / "a")) == values(load(tmp_path / "b"))


def test_video_rows_are_parsed():
    result = load(FIXTURES / "basic")
    meta = {k[1]: v for k, v in result.merged.video_meta.items()}
    assert meta["vidOld00003"].published_at == date(2025, 12, 1)
    assert meta["vidShort002"].duration_s == 45


# --- reconciliation ----------------------------------------------------------


def test_consistent_export_is_accepted():
    datum = by_name(load(FIXTURES / "basic"))[f"Datum 2026-01-01_2026-01-10 {CH}"]
    assert datum.status == "accepted"


def test_truncated_export_is_rejected_naming_metric_and_values():
    (folder,) = load(FIXTURES / "truncated").folders
    assert folder.status == "rejected"
    assert "views" in folder.reason and "1370" in folder.reason and "6370" in folder.reason


def test_videos_export_with_channel_level_remainder_is_accepted():
    videos = by_name(load(FIXTURES / "basic"))[f"Videos 2026-01-01_2026-01-10 {CH}"]
    assert videos.status == "accepted"  # rows: 9 net subs, total: 11


def test_videos_export_exceeding_channel_total_is_rejected():
    (folder,) = load(FIXTURES / "videos_exceeding").folders
    assert folder.status == "rejected" and "exceeds" in folder.reason


def test_totals_row_is_not_stored_as_data_row():
    result = load(FIXTURES / "basic")
    assert all(isinstance(k[1], date) for k in result.merged.daily)
    assert all(k[1] != "Gesamt" for k in result.merged.video_meta)


# --- period totals -----------------------------------------------------------


def _pt(result, start, end, metric):
    key = (CH, date.fromisoformat(start), date.fromisoformat(end), metric)
    return result.merged.period_totals.get(key, (None, None))


def test_totals_row_becomes_period_total_including_distinct_counts():
    result = load(FIXTURES / "basic")
    assert _pt(result, "2026-01-01", "2026-01-10", "unique_viewers")[0] == 500
    assert _pt(result, "2026-01-01", "2026-01-10", "views")[0] == 1370


def test_monthly_export_creates_its_own_period_total(tmp_path):
    write_export(
        tmp_path,
        f"Datum 2026-09-01_2026-09-30 {CH}",
        DATUM_HEADER,
        [["Gesamt", 3, 30, 1, 2], ["2026-09-01", 3, 30, 1, 3]],
    )
    assert _pt(load(tmp_path), "2026-09-01", "2026-09-30", "unique_viewers")[0] == 2


def test_datum_totals_win_over_newer_videos_totals_and_distinct_zeros_are_ignored(basic_root):
    videos = basic_root / f"Videos 2026-01-01_2026-01-10 {CH}" / "Tabellendaten.csv"
    future = os.stat(videos).st_mtime_ns + 10**10
    os.utime(videos, ns=(future, future))
    result = load(basic_root)
    value, source = _pt(result, "2026-01-01", "2026-01-10", "unique_viewers")
    assert value == 500 and source.startswith("Datum")
    assert _pt(result, "2026-01-01", "2026-01-10", "views")[1].startswith("Datum")


def test_distinct_counts_from_videos_totals_never_create_period_totals(tmp_path):
    header = ["Videos", "Videotitel", "Aufrufe", "Einzelne Zuschauer"]
    write_export(
        tmp_path,
        f"Videos 2026-01-01_2026-01-10 {CH}",
        header,
        [["Gesamt", "", 10, 0], ["vid00000001", "A", 10, ""]],
    )
    result = load(tmp_path)
    assert _pt(result, "2026-01-01", "2026-01-10", "unique_viewers") == (None, None)
    assert _pt(result, "2026-01-01", "2026-01-10", "views")[0] == 10


# --- merging -----------------------------------------------------------------


def test_newer_export_revises_old_days(tmp_path):
    write_export(
        tmp_path,
        f"Datum 2026-10-01_2026-10-27 {CH}",
        DATUM_HEADER,
        [["Gesamt", 10, 100, 1, 1], ["2026-10-20", 10, 100, 1, 1]],
    )
    write_export(
        tmp_path,
        f"Datum 2026-10-15_2026-11-10 {CH}",
        DATUM_HEADER,
        [["Gesamt", 12, 100, 1, 1], ["2026-10-20", 12, 100, 1, 1]],
    )
    assert _daily(load(tmp_path), "2026-10-20", "views") == 12


def test_complementary_columns_are_merged(tmp_path):
    write_export(
        tmp_path,
        f"Datum 2026-10-01_2026-10-27 {CH}",
        ["Datum", "Aufrufe"],
        [["Gesamt", 10], ["2026-10-20", 10]],
    )
    write_export(
        tmp_path,
        f"Datum 2026-10-01_2026-10-28 {CH}",
        ["Datum", "Thumbnail-Impressionen"],
        [["Gesamt", 99], ["2026-10-20", 99]],
    )
    result = load(tmp_path)
    assert _daily(result, "2026-10-20", "views") == 10
    assert _daily(result, "2026-10-20", "impressions") == 99


def test_equal_period_end_uses_most_recently_modified(tmp_path):
    a = write_export(
        tmp_path,
        f"Datum 2026-10-01_2026-10-27 {CH}",
        ["Datum", "Aufrufe"],
        [["Gesamt", 10], ["2026-10-20", 10]],
    )
    b = write_export(
        tmp_path,
        f"Datum 2026-10-01_2026-10-27 {CH}-2",
        ["Datum", "Aufrufe"],
        [["Gesamt", 11], ["2026-10-20", 11]],
    )
    os.utime(a, ns=(2 * 10**18, 2 * 10**18))
    os.utime(b, ns=(1 * 10**18, 1 * 10**18))
    assert _daily(load(tmp_path), "2026-10-20", "views") == 10


def test_size_limit_rejects_oversized_cell(tmp_path):
    write_export(
        tmp_path,
        f"Videos 2026-01-01_2026-01-10 {CH}",
        ["Videos", "Videotitel", "Aufrufe"],
        [["Gesamt", "", 1], ["vid00000001", "x" * 20_000, 1]],
    )
    (folder,) = load(tmp_path).folders
    assert folder.status == "rejected" and "size limit" in folder.reason


def test_fixture_root_is_left_untouched():
    before = {
        p: (p.stat().st_mtime_ns, p.read_bytes()) for p in Path(FIXTURES).rglob("*") if p.is_file()
    }
    load(FIXTURES / "basic")
    after = {
        p: (p.stat().st_mtime_ns, p.read_bytes()) for p in Path(FIXTURES).rglob("*") if p.is_file()
    }
    assert before == after


def test_symlinked_folders_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    write_export(
        outside,
        f"Datum 2026-01-01_2026-01-01 {CH}",
        ["Datum", "Aufrufe"],
        [["Gesamt", 1], ["2026-01-01", 1]],
    )
    root = tmp_path / "root"
    root.mkdir()
    (root / f"Datum 2026-01-01_2026-01-01 {CH}").symlink_to(
        outside / f"Datum 2026-01-01_2026-01-01 {CH}"
    )
    (folder,) = load(root).folders
    assert folder.status == "skipped" and "symbolic" in folder.reason

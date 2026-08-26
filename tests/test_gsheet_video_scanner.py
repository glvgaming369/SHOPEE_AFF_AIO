from pathlib import Path

import openpyxl

import pytest

from gsheet_video_scanner import ProductRow, build_matched_pool, find_results_file, scan_video_ids


def _make_results_file(path: Path, rows: list[list[str]]) -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    header = ["SP ID", "Tên sản phẩm", "URL Shopee"] + [f"col{i}" for i in range(4, 16)] + ["Merge Links"]
    worksheet.append(header)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def test_scan_video_ids_only_exact_numeric_filenames(tmp_path: Path):
    (tmp_path / "244397997.mp4").write_bytes(b"x")
    (tmp_path / "244397997_2.mp4").write_bytes(b"x")  # suffix -> excluded
    (tmp_path / "abc.mp4").write_bytes(b"x")  # not numeric -> excluded
    (tmp_path / "111.MP4").write_bytes(b"x")  # case-insensitive ext -> included
    (tmp_path / "readme.txt").write_bytes(b"x")

    ids = scan_video_ids(tmp_path)

    assert ids == {"244397997", "111"}


def test_build_matched_pool_preserves_results_order_and_filters_unmatched(tmp_path: Path):
    (tmp_path / "111.mp4").write_bytes(b"x")
    (tmp_path / "333.mp4").write_bytes(b"x")

    filler = ["" for _ in range(12)]
    rows = [
        ["111", "SP A", "http://a", *filler, "merge-a"],
        ["222", "SP B", "http://b", *filler, "merge-b"],  # no matching video -> filtered out
        ["333", "SP C", "http://c", *filler, "merge-c"],
    ]
    _make_results_file(tmp_path / "shop_results.xlsx", rows)

    pool = build_matched_pool(tmp_path)

    assert [row.sp_id for row in pool] == ["111", "333"]
    assert pool[0] == ProductRow(sp_id="111", product_name="SP A", shopee_url="http://a", merge_links="merge-a")


def test_build_matched_pool_raises_when_no_results_file(tmp_path: Path):
    (tmp_path / "111.mp4").write_bytes(b"x")

    try:
        build_matched_pool(tmp_path)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_find_results_file_raises_when_multiple_matches_are_ambiguous(tmp_path: Path):
    _make_results_file(tmp_path / "a_results.xlsx", [])
    _make_results_file(tmp_path / "b_results.xlsx", [])

    with pytest.raises(ValueError):
        find_results_file(tmp_path)


def test_read_product_rows_raises_when_source_is_missing_merge_links_column(tmp_path: Path):
    workbook_path = tmp_path / "narrow_results.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["SP ID", "Tên sản phẩm", "URL Shopee"])
    worksheet.append(["111", "SP A", "http://a"])
    workbook.save(workbook_path)

    with pytest.raises(ValueError):
        build_matched_pool(tmp_path)

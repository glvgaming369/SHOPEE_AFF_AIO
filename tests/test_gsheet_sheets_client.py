from gsheet_paths import normalize_folder
from gsheet_push_engine import to_sheet_row
from gsheet_sheets_client import (
    ACCOUNT_SHEET_HEADERS,
    _compute_job_stats,
    _extract_used_ids_for_folder,
    extract_spreadsheet_id,
    sanitize_sheet_title,
)
from gsheet_video_scanner import ProductRow

_HEADER = ["A"] * 12  # placeholder header row, only its presence/position matters


def _account_row(sp_id: str, folder: str) -> list[str]:
    # A..L, only B (index 1, SP ID) and L (index 11, Thư Mục Video / folder) matter here.
    row = [""] * 12
    row[1] = sp_id
    row[11] = folder
    return row


def _status_row(sp_id: str, status: str) -> list[str]:
    # A..L, only B (index 1, SP ID) and J (index 9, Report 1 / status) matter here.
    row = [""] * 12
    row[1] = sp_id
    row[9] = status
    return row


def test_extract_used_ids_matches_what_to_sheet_row_actually_writes(tmp_path):
    """Regression test: to_sheet_row() and _extract_used_ids_for_folder() must agree on
    which column holds the SP ID. A prior bug had them looking at different columns (B vs
    C) after the update.txt remapping, silently breaking sheet-based dedup."""
    folder = str(tmp_path / "vids")
    product = ProductRow(sp_id="12345", product_name="Some Product", shopee_url="url", merge_links="links")

    written_row = to_sheet_row(product, video_folder=folder)
    rows = [ACCOUNT_SHEET_HEADERS, written_row]

    used = _extract_used_ids_for_folder(rows, normalize_folder(folder))

    assert used == {"12345"}


def test_extract_used_ids_only_matches_the_same_folder(tmp_path):
    folder_a = str(tmp_path / "a")
    folder_b = str(tmp_path / "b")
    rows = [_HEADER, _account_row("111", folder_a), _account_row("222", folder_b)]

    used = _extract_used_ids_for_folder(rows, normalize_folder(folder_a))

    assert used == {"111"}


def test_extract_used_ids_is_empty_when_no_row_matches_target_folder(tmp_path):
    folder_a = str(tmp_path / "a")
    folder_c = str(tmp_path / "c")
    rows = [_HEADER, _account_row("111", folder_a)]

    used = _extract_used_ids_for_folder(rows, normalize_folder(folder_c))

    assert used == set()


def test_extract_used_ids_reuses_same_sp_id_across_folders_without_collision(tmp_path):
    """The exact scenario the user flagged: two different folders that happen to share a
    video filename (same SP ID) must not cross-block each other."""
    folder_a = str(tmp_path / "a")
    folder_b = str(tmp_path / "b")
    rows = [_HEADER, _account_row("999", folder_a)]

    used_for_a = _extract_used_ids_for_folder(rows, normalize_folder(folder_a))
    used_for_b = _extract_used_ids_for_folder(rows, normalize_folder(folder_b))

    assert used_for_a == {"999"}
    assert used_for_b == set()


def test_extract_used_ids_handles_short_rows_without_crashing():
    rows = [_HEADER, ["only", "three", "cols"]]

    used = _extract_used_ids_for_folder(rows, "d:/anything")

    assert used == set()


def test_compute_job_stats_counts_completed_pending_and_total():
    rows = [
        _HEADER,
        _status_row("1", "Ngày 18/08/2026 lúc 20:49:53 - Đăng thành công M2 S1"),
        _status_row("2", ""),  # pending: cột J trống
        _status_row("3", "Lỗi: tài khoản bị khoá"),  # co noi dung nhung khong phai "thanh cong"
        ["", "", "", "", "", "", "", "", "", "khong co SP ID", "", ""],  # bo qua, khong tinh vao total
    ]

    total, completed, pending = _compute_job_stats(rows)

    assert total == 3
    assert completed == 1
    assert pending == 1


def test_compute_job_stats_matches_real_world_concatenated_status_text():
    """Cell content can hold multiple log lines glued together, as pasted by the user."""
    status = (
        "Ngày 18/08/2026 lúc 20:49:53 - Đăng thành công M2 S1"
        "Ngày 18/08/2026 lúc 20:51:48 - Đăng thành công M2 S2"
    )
    rows = [_HEADER, _status_row("1", status)]

    total, completed, pending = _compute_job_stats(rows)

    assert (total, completed, pending) == (1, 1, 0)


def test_compute_job_stats_empty_sheet_is_all_zero():
    assert _compute_job_stats([_HEADER]) == (0, 0, 0)


def test_sanitize_sheet_title_replaces_invalid_characters():
    assert sanitize_sheet_title("shop/name:1*2") == "shop_name_1_2"


def test_sanitize_sheet_title_truncates_to_100_chars():
    assert len(sanitize_sheet_title("x" * 200)) == 100


def test_extract_spreadsheet_id_from_full_url():
    url = "https://docs.google.com/spreadsheets/d/ABC123xyz/edit?gid=0#gid=0"
    assert extract_spreadsheet_id(url) == "ABC123xyz"


def test_extract_spreadsheet_id_from_bare_id():
    assert extract_spreadsheet_id("ABC123xyz") == "ABC123xyz"

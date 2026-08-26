"""Thin wrapper around gspread for the two sheet shapes this tool cares about."""
from __future__ import annotations

import re
import time

import gspread
from google.oauth2.service_account import Credentials

from gsheet_paths import normalize_folder

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# 0-based positions of the SP ID column and the video-folder tracking column within an
# account sheet's A..L range. Per update.txt's mapping, SP ID lives in column B ("Tên Sản
# Phẩm") and the product name lives in column C ("Tên Video") - NOT the reverse. The
# folder column (L) exists purely so get_used_video_ids() can stay folder-scoped - it's
# not part of the A..K product-data mapping in to_sheet_row().
_ACCOUNT_COL_SP_ID = 1
_ACCOUNT_COL_STATUS = 9  # column J ("Report 1") - written by a downstream posting tool
_ACCOUNT_COL_FOLDER = 11

# Substring a downstream posting tool writes into column J once a job has actually been
# posted. A job row with an empty column J is still pending; anything else (present but
# not this marker) is neither counted as completed nor pending - see get_job_stats().
_JOB_COMPLETED_MARKER = "Đăng thành công"

DEFAULT_SHEET_NAME = "Data Tạo Video Từ Link"
DEFAULT_SHEET_HEADERS = ["Profile", "adb_serial", "Tên Sheet", "Link Trang Cá Nhân"]
ACCOUNT_SHEET_HEADERS = [
    "Link Sản Phẩm làm Video",
    "Tên Sản Phẩm",
    "Tên Video",
    "Nội Dung Video",
    "Prompt Tạo Video Ai",
    "Link Sản Phẩm Muốn Gắn Giỏ",
    "Caption Cho Sản Phẩm",
    "Thứ Tự Ảnh",
    "Report",
    "Report 1",
    "Report 2",
    "Thư Mục Video",
]

_INVALID_SHEET_CHARS = re.compile(r"[\[\]\*\?/\\:]")
_SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def sanitize_sheet_title(name: str) -> str:
    cleaned = _INVALID_SHEET_CHARS.sub("_", name).strip()
    return (cleaned or "Untitled")[:100]


def extract_spreadsheet_id(url_or_id: str) -> str:
    match = _SPREADSHEET_ID_RE.search(url_or_id)
    return match.group(1) if match else url_or_id.strip()


def _extract_used_ids_for_folder(rows: list[list[str]], target_folder: str) -> set[str]:
    """Given an account sheet's raw rows (header at index 0), return the SP IDs (column B)
    whose stored folder (column L, already-normalized `target_folder`) matches.

    `target_folder` must already be normalize_folder()'d by the caller. Rows pushed from a
    *different* folder are ignored, so the same SP ID reused across folders never collides.
    """
    used: set[str] = set()
    for row in rows[1:]:
        sp_id = row[_ACCOUNT_COL_SP_ID].strip() if len(row) > _ACCOUNT_COL_SP_ID else ""
        folder_value = row[_ACCOUNT_COL_FOLDER].strip() if len(row) > _ACCOUNT_COL_FOLDER else ""
        if sp_id and folder_value and normalize_folder(folder_value) == target_folder:
            used.add(sp_id)
    return used


def _compute_job_stats(rows: list[list[str]]) -> tuple[int, int, int]:
    """Pure counting logic behind SheetsClient.get_job_stats() - see its docstring."""
    total = completed = pending = 0
    for row in rows[1:]:
        sp_id = row[_ACCOUNT_COL_SP_ID].strip() if len(row) > _ACCOUNT_COL_SP_ID else ""
        if not sp_id:
            continue
        total += 1
        status = row[_ACCOUNT_COL_STATUS].strip() if len(row) > _ACCOUNT_COL_STATUS else ""
        if _JOB_COMPLETED_MARKER in status:
            completed += 1
        elif not status:
            pending += 1
    return total, completed, pending


# A long-lived HTTP connection reused across many calls over a long-running server process
# (and across concurrent requests) can go stale server-side; without a timeout, a request
# over a stale connection hangs indefinitely instead of failing with a catchable error.
_REQUEST_TIMEOUT_SECONDS = 30

# Retry-on-429 tuning: Google Sheets' default quota is per-minute, so a short exponential
# backoff (a few seconds up to ~16s) is usually enough to clear a transient burst without
# making the user wait excessively or without giving up too early.
_RATE_LIMIT_MAX_ATTEMPTS = 5
_RATE_LIMIT_BASE_DELAY_SECONDS = 2.0


class SheetsClient:
    def __init__(self, credentials_path: str, spreadsheet_url: str):
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        self._gc = gspread.authorize(creds)
        self._gc.set_timeout(_REQUEST_TIMEOUT_SECONDS)
        self._sh = self._gc.open_by_key(extract_spreadsheet_id(spreadsheet_url))

    @staticmethod
    def _call(func, *args, **kwargs):
        """Call a gspread method, retrying with exponential backoff on HTTP 429 (rate
        limit). Any other error, or a 429 that persists past the last attempt, propagates
        immediately so it still surfaces to the caller via the existing error handling.
        """
        for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
            try:
                return func(*args, **kwargs)
            except gspread.exceptions.APIError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status != 429 or attempt == _RATE_LIMIT_MAX_ATTEMPTS - 1:
                    raise
                time.sleep(_RATE_LIMIT_BASE_DELAY_SECONDS * (2**attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    def _worksheet_map(self) -> dict[str, gspread.Worksheet]:
        """Fetch the full worksheet list in a single API call, indexed by title. Prefer
        this over repeated `Spreadsheet.worksheet(title)` calls in a loop - each of those
        re-fetches the *entire* sheet metadata, which is what previously turned "reload N
        accounts" into 1+2N API calls and tripped Google's per-minute rate limit.
        """
        return {ws.title: ws for ws in self._call(self._sh.worksheets)}

    def get_default_sheet(self):
        try:
            return self._call(self._sh.worksheet, DEFAULT_SHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = self._call(self._sh.add_worksheet, title=DEFAULT_SHEET_NAME, rows=200, cols=4)
            self._call(ws.update, "A1:D1", [DEFAULT_SHEET_HEADERS])
            return ws

    def read_profiles(self) -> list[str]:
        values = self._call(self.get_default_sheet().col_values, 1)
        return [v.strip() for v in values[1:] if v and v.strip()]

    def append_new_profiles(self, profiles: list[str]) -> list[str]:
        """Append profiles from `profiles` that aren't already in column A. Returns what was added."""
        ws = self.get_default_sheet()
        existing = set(self.read_profiles())
        new_ones = [p for p in dict.fromkeys(profiles) if p not in existing]
        if new_ones:
            rows = [[p, "", sanitize_sheet_title(p), ""] for p in new_ones]
            self._call(ws.append_rows, rows, value_input_option="RAW")
        return new_ones

    def ensure_account_sheet(self, profile: str):
        title = sanitize_sheet_title(profile)
        try:
            return self._call(self._sh.worksheet, title)
        except gspread.WorksheetNotFound:
            ws = self._call(self._sh.add_worksheet, title=title, rows=500, cols=12)
            self._call(ws.update, "A1:L1", [ACCOUNT_SHEET_HEADERS])
            return ws

    def get_job_stats(self, profile: str) -> tuple[int, int, int]:
        """Returns (total, completed, pending) job counts for `profile`'s sheet.

        total = data rows with a SP ID (column B). completed = of those, ones whose
        column J contains "Đăng thành công" (written by a downstream posting tool).
        pending = of those, ones whose column J is still empty. A row with a SP ID and
        a non-empty, non-matching column J counts toward total but neither bucket.

        For more than one profile, prefer get_job_stats_bulk() - it costs a small,
        constant number of API calls no matter how many accounts, instead of ~2 per
        account here (one to resolve the sheet, one to read its values).
        """
        return self.get_job_stats_bulk([profile])[0][1:]

    def get_job_stats_bulk(self, profiles: list[str]) -> list[tuple[str, int, int, int]]:
        """Like get_job_stats(), for many accounts at once, in O(1) API calls instead of
        O(n): one call to list worksheets, one batched values call covering all of them
        (via Sheets API values:batchGet) instead of a separate request per account. This
        is what keeps "reload accounts" from tripping Google's rate limit as the account
        list grows.
        """
        ws_by_title = self._worksheet_map()
        title_by_profile = {p: sanitize_sheet_title(p) for p in profiles}
        existing_titles = [
            title for title in dict.fromkeys(title_by_profile.values()) if title in ws_by_title
        ]

        values_by_title: dict[str, list[list[str]]] = {}
        if existing_titles:
            ranges = [f"'{title}'!A:L" for title in existing_titles]
            response = self._call(self._sh.values_batch_get, ranges)
            for title, value_range in zip(existing_titles, response.get("valueRanges", [])):
                values_by_title[title] = value_range.get("values", [])

        results: list[tuple[str, int, int, int]] = []
        for profile in profiles:
            rows = values_by_title.get(title_by_profile[profile])
            total, completed, pending = _compute_job_stats(rows) if rows else (0, 0, 0)
            results.append((profile, total, completed, pending))
        return results

    def clear_account_data(self, profile: str) -> bool:
        """Clear all data rows (row 2 onward) in `profile`'s sheet, keeping the header row.

        Returns True if a sheet existed and was cleared, False if there was nothing to clear
        (the account never had a sheet created for it). For more than one profile, prefer
        clear_accounts_bulk() to avoid a redundant metadata fetch per account.
        """
        return self.clear_accounts_bulk([profile]) == [profile]

    def clear_accounts_bulk(self, profiles: list[str]) -> list[str]:
        """Like clear_account_data(), for many accounts: resolves all their sheets from a
        single worksheet listing instead of one metadata fetch per account. Returns the
        profiles that actually had a sheet to clear.
        """
        ws_by_title = self._worksheet_map()
        cleared: list[str] = []
        for profile in profiles:
            ws = ws_by_title.get(sanitize_sheet_title(profile))
            if ws is None:
                continue
            last_row = max(ws.row_count, 2)
            self._call(ws.batch_clear, [f"A2:L{last_row}"])
            cleared.append(profile)
        return cleared

    def get_used_video_ids(self, profiles: list[str], folder: str) -> set[str]:
        """SP IDs already pushed for `folder` specifically, scoped to this tool's
        per-account sheets for `profiles`.

        Matches column B (SP ID) together with column L (Thư Mục Video = source folder), so
        the same SP ID/filename reused across two different video folders never cross-blocks —
        dedup only applies within the folder currently being processed. Only scans sheets
        whose title matches a known profile, so unrelated tabs a human added to the
        spreadsheet can't pollute dedup either.
        """
        wanted_titles = {sanitize_sheet_title(p) for p in profiles}
        target_folder = normalize_folder(folder)
        used: set[str] = set()
        for ws in self._call(self._sh.worksheets):
            if ws.title not in wanted_titles:
                continue
            used |= _extract_used_ids_for_folder(self._call(ws.get_all_values), target_folder)
        return used

    def push_rows(self, profile: str, rows: list[list[str]]) -> None:
        if not rows:
            return
        ws = self.ensure_account_sheet(profile)
        self._call(ws.append_rows, rows, value_input_option="RAW")

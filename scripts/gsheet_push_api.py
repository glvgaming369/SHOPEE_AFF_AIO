"""API routes cho tab "Push Sheet" - dashboard Shopee Affiliate Offer Scraper.

Port tu app desktop PySide6 doc lap (D:\\push_data_to_ggsheet): quet thu muc video +
doi chieu *_results.xlsx, quan ly danh sach tai khoan, roi push du lieu len cac sheet
con tren Google Sheets. Business logic (gsheet_config/account/accounts_state/paths/
push_cache/push_engine/sheets_client/video_scanner) la Python thuan duoc port gan nhu
nguyen ven; module nay chi la lop route HTTP thay cho PySide6 UI cu.

Khong dung Blueprint - giu dung quy uoc cua affiliate_scrape_server.py (1 global Flask
app, @app.route truc tiep). register(app) duoc goi 1 lan luc khoi dong server.
"""
from __future__ import annotations

import threading
from dataclasses import asdict
from pathlib import Path

from flask import jsonify, request

from gsheet_account import AccountRow
import gsheet_accounts_state as accounts_state
from gsheet_config import AppConfig
import gsheet_push_cache as push_cache
from gsheet_paths import normalize_folder
from gsheet_push_engine import allocate_sequential, filter_unused, to_sheet_row
from gsheet_sheets_client import SheetsClient
from gsheet_video_scanner import ProductRow, build_matched_pool, find_results_file

# Cache 1 SheetsClient da xac thuc, tao lai khi credentials/spreadsheet_url doi - tuong duong
# _get_client() cua MainWindow ben ban desktop.
_client: SheetsClient | None = None
_client_key: tuple[str, str] | None = None

# Pool da quet, cho Push dung lai ma khong can quet lai thu muc - tuong duong
# MainWindow._scanned_pool/_scanned_folder.
_scanned_pool: list[ProductRow] | None = None
_scanned_folder: str | None = None

# Chan 2 request mutate (import/reload/scan/push/cleanup/xoa cache/xoa file) chay chong cheo -
# ben desktop UI tu disable nut luc bung (_set_busy), web co the co nhieu tab/request nen can
# khoa rieng o server.
_busy_lock = threading.Lock()


def _bad_request(msg: str):
    return jsonify({"error": msg}), 400


def _busy_response():
    return jsonify({"error": "Dang co thao tac khac chay, vui long doi roi thu lai."}), 409


def _get_client() -> tuple[SheetsClient | None, tuple | None]:
    """Tra ve (client, None) hoac (None, error_response) neu chua cau hinh / ket noi loi."""
    global _client, _client_key
    cfg = AppConfig.load()
    if not cfg.spreadsheet_url or not cfg.credentials_path:
        return None, _bad_request(
            "Chua cau hinh spreadsheet URL / credentials.json - vao muc Cau hinh o tab nay truoc."
        )
    key = (cfg.credentials_path, cfg.spreadsheet_url)
    if _client is not None and _client_key == key:
        return _client, None
    try:
        _client = SheetsClient(cfg.credentials_path, cfg.spreadsheet_url)
        _client_key = key
    except Exception as exc:  # noqa: BLE001 - surfaced to caller, not swallowed
        _client = None
        _client_key = None
        return None, _bad_request(f"Loi ket noi Google Sheets: {exc}")
    return _client, None


def _reload_from_sheet(client: SheetsClient) -> list[AccountRow]:
    """Doc danh sach profile + thong ke job tu Sheet, giu lai qty da nhap cuc bo cho profile
    da co san, luu vao accounts_state. Tuong duong ReloadWorker + _on_reload_finished."""
    profiles = client.read_profiles()
    results = client.get_job_stats_bulk(profiles)
    existing_qty = {r.profile: r.qty for r in accounts_state.load_accounts()}
    rows = [
        AccountRow(
            profile=p,
            selected=True,
            qty=existing_qty.get(p, 0),
            current_jobs=total,
            completed_jobs=completed,
            pending_jobs=pending,
        )
        for p, total, completed, pending in results
    ]
    accounts_state.save_accounts(rows)
    return rows


def register(app) -> None:
    """Dang ky toan bo route /api/gsheet/* len `app`. Goi 1 lan luc khoi dong server."""

    @app.route("/api/gsheet/config", methods=["GET"])
    def gsheet_get_config():
        return jsonify(asdict(AppConfig.load()))

    @app.route("/api/gsheet/config", methods=["POST"])
    def gsheet_save_config():
        body = request.get_json(force=True, silent=True) or {}
        cfg = AppConfig(
            spreadsheet_url=str(body.get("spreadsheet_url", "")).strip(),
            video_folder=str(body.get("video_folder", "")).strip(),
            credentials_path=str(body.get("credentials_path", "")).strip(),
        )
        cfg.save()
        return jsonify(asdict(cfg))

    @app.route("/api/gsheet/accounts", methods=["GET"])
    def gsheet_list_accounts():
        rows = accounts_state.load_accounts()
        return jsonify({"accounts": [asdict(r) for r in rows]})

    @app.route("/api/gsheet/accounts/import", methods=["POST"])
    def gsheet_import_accounts():
        body = request.get_json(force=True, silent=True) or {}
        profiles = [str(p).strip() for p in (body.get("profiles") or []) if str(p).strip()]
        if not profiles:
            return _bad_request("Thieu 'profiles' (danh sach ten tai khoan).")
        client, err = _get_client()
        if err:
            return err
        if not _busy_lock.acquire(blocking=False):
            return _busy_response()
        try:
            added = client.append_new_profiles(profiles)
            for profile in added:
                client.ensure_account_sheet(profile)
            rows = _reload_from_sheet(client)
        finally:
            _busy_lock.release()
        return jsonify({"added": added, "accounts": [asdict(r) for r in rows]})

    @app.route("/api/gsheet/accounts/reload", methods=["POST"])
    def gsheet_reload_accounts():
        client, err = _get_client()
        if err:
            return err
        if not _busy_lock.acquire(blocking=False):
            return _busy_response()
        try:
            rows = _reload_from_sheet(client)
        finally:
            _busy_lock.release()
        return jsonify({"accounts": [asdict(r) for r in rows]})

    @app.route("/api/gsheet/accounts/clear_list", methods=["POST"])
    def gsheet_clear_accounts_list():
        accounts_state.save_accounts([])
        return jsonify({"ok": True})

    @app.route("/api/gsheet/accounts/update", methods=["POST"])
    def gsheet_update_account():
        body = request.get_json(force=True, silent=True) or {}
        profile = body.get("profile")
        field = body.get("field")
        if not profile or field not in ("selected", "qty"):
            return _bad_request("Thieu 'profile' hoac 'field' khong hop le (selected|qty).")
        rows = accounts_state.load_accounts()
        for row in rows:
            if row.profile == profile:
                if field == "selected":
                    row.selected = bool(body.get("value"))
                else:
                    try:
                        row.qty = max(0, int(body.get("value", 0)))
                    except (TypeError, ValueError):
                        return _bad_request("'value' phai la so nguyen cho field 'qty'.")
                break
        else:
            return _bad_request(f"Khong tim thay tai khoan '{profile}' trong danh sach.")
        accounts_state.save_accounts(rows)
        return jsonify({"ok": True})

    @app.route("/api/gsheet/accounts/bulk", methods=["POST"])
    def gsheet_bulk_update_accounts():
        body = request.get_json(force=True, silent=True) or {}
        profiles = set(body.get("profiles") or [])
        field = body.get("field")
        if field not in ("selected", "qty"):
            return _bad_request("'field' khong hop le (selected|qty).")
        rows = accounts_state.load_accounts()
        if field == "selected":
            value = bool(body.get("value"))
            for row in rows:
                if row.profile in profiles:
                    row.selected = value
        else:
            try:
                value = max(0, int(body.get("value", 0)))
            except (TypeError, ValueError):
                return _bad_request("'value' phai la so nguyen cho field 'qty'.")
            for row in rows:
                if row.profile in profiles:
                    row.qty = value
        accounts_state.save_accounts(rows)
        return jsonify({"accounts": [asdict(r) for r in rows]})

    @app.route("/api/gsheet/scan", methods=["POST"])
    def gsheet_scan():
        global _scanned_pool, _scanned_folder
        body = request.get_json(force=True, silent=True) or {}
        video_folder = str(body.get("video_folder", "")).strip()
        if not video_folder:
            return _bad_request("Thieu 'video_folder'.")
        if not _busy_lock.acquire(blocking=False):
            return _busy_response()
        try:
            results_path = find_results_file(Path(video_folder))
            if results_path is None:
                return _bad_request(f"Khong tim thay file *_results.xlsx trong {video_folder}")
            matched = build_matched_pool(video_folder)
            cached_ids = push_cache.get_pushed_ids(video_folder)
            pool = filter_unused(matched, cached_ids)
            _scanned_pool = pool
            _scanned_folder = video_folder
        finally:
            _busy_lock.release()

        log = [
            f"Dùng file dữ liệu: {results_path.name}",
            f"Quét xong: {len(matched)} job hợp lệ (khớp file video trong thư mục + có trong "
            f"_results.xlsx). Sau khi loại {len(cached_ids)} job đã push theo cache local: "
            f"còn {len(pool)} job sẵn sàng để push.",
        ]
        return jsonify({"folder": video_folder, "pool_size": len(pool), "log": log})

    @app.route("/api/gsheet/push", methods=["POST"])
    def gsheet_push():
        global _scanned_pool
        body = request.get_json(force=True, silent=True) or {}
        accounts_in = body.get("accounts")
        if not isinstance(accounts_in, list) or not accounts_in:
            return _bad_request("Thieu 'accounts' (danh sach {profile, qty}).")
        try:
            accounts = [
                (str(a["profile"]), max(0, int(a.get("qty", 0)))) for a in accounts_in
            ]
        except (KeyError, TypeError, ValueError):
            return _bad_request("'accounts' phai la danh sach {profile, qty}.")
        accounts = [(p, q) for p, q in accounts if q > 0]
        if not accounts:
            return _bad_request("Khong co tai khoan nao voi so luong > 0.")

        if _scanned_pool is None or _scanned_folder is None:
            return _bad_request("Chua quet du lieu - bam 'Quét dữ liệu' truoc khi push.")

        cfg = AppConfig.load()
        video_folder = cfg.video_folder.strip()
        if not video_folder or normalize_folder(video_folder) != normalize_folder(_scanned_folder):
            return _bad_request(
                "Thu muc video trong Cau hinh khac voi luc quet - vui long quet lai truoc khi push."
            )

        client, err = _get_client()
        if err:
            return err

        if not _busy_lock.acquire(blocking=False):
            return _busy_response()
        try:
            profiles = client.read_profiles()
            sheet_used_ids = client.get_used_video_ids(profiles, video_folder)
            cached_ids = push_cache.get_pushed_ids(video_folder)
            pool = filter_unused(_scanned_pool, sheet_used_ids | cached_ids)

            log = [
                f"Dùng dữ liệu đã quét trước đó ({len(_scanned_pool)} job), không quét lại "
                f"thư mục. Sau khi loại job đã push (sheet: {len(sheet_used_ids)}, cache "
                f"local: {len(cached_ids)}): còn {len(pool)} job."
            ]

            allocations = allocate_sequential(pool, accounts)
            # 1 lan goi duy nhat cho TOAN BO account (khong lap client.push_rows() tung
            # account 1) - push_rows_bulk() tu resolve worksheet + phan luong (pacing) giua
            # cac account, thay vi de moi vong lap tu ban 1 loat request lien tuc de bi
            # Google tra 429 (bao cao thuc te 2026-08-31: 20 account x 80 job/account).
            client.push_rows_bulk(
                [
                    (allocation.profile, [to_sheet_row(r, video_folder) for r in allocation.rows])
                    for allocation in allocations
                ]
            )
            pushed_ids: list[str] = []
            per_account: list[dict] = []
            for allocation in allocations:
                pushed_ids.extend(r.sp_id for r in allocation.rows)
                per_account.append(
                    {
                        "profile": allocation.profile,
                        "requested": allocation.requested,
                        "fulfilled": allocation.fulfilled,
                    }
                )
                if allocation.shortfall > 0:
                    log.append(
                        f"- {allocation.profile}: đã push {allocation.fulfilled}/"
                        f"{allocation.requested} job (thiếu {allocation.shortfall} do hết pool)."
                    )
                else:
                    log.append(f"- {allocation.profile}: đã push {allocation.fulfilled} job.")

            push_cache.add_pushed_ids(video_folder, pushed_ids)
            pushed_id_set = set(pushed_ids)
            remaining_pool = [r for r in pool if r.sp_id not in pushed_id_set]
            _scanned_pool = remaining_pool
        finally:
            _busy_lock.release()

        jobs_by_profile = {pa["profile"]: pa["fulfilled"] for pa in per_account}
        rows = accounts_state.load_accounts()
        for row in rows:
            pushed = jobs_by_profile.get(row.profile)
            if pushed:
                row.current_jobs += pushed
                row.pending_jobs += pushed
        accounts_state.save_accounts(rows)

        pushed_count = sum(jobs_by_profile.values())
        log.append(
            f"Hoàn tất: đã push {pushed_count} job, còn lại {len(remaining_pool)} job chưa "
            "push trong dữ liệu đã quét (bấm Push lại để dùng tiếp, không cần quét lại)."
        )
        return jsonify(
            {
                "log": log,
                "per_account": per_account,
                "remaining_pool_size": len(remaining_pool),
                "accounts": [asdict(r) for r in rows],
            }
        )

    @app.route("/api/gsheet/cleanup", methods=["POST"])
    def gsheet_cleanup():
        body = request.get_json(force=True, silent=True) or {}
        profiles = [str(p) for p in (body.get("profiles") or [])]
        if not profiles:
            return _bad_request("Thieu 'profiles' (danh sach tai khoan can don dep).")
        client, err = _get_client()
        if err:
            return err
        if not _busy_lock.acquire(blocking=False):
            return _busy_response()
        try:
            cleared_profiles = client.clear_accounts_bulk(profiles)
        finally:
            _busy_lock.release()

        cleared_set = set(cleared_profiles)
        log = [
            (
                f"- {p}: đã dọn dẹp dữ liệu (giữ nguyên hàng tiêu đề)."
                if p in cleared_set
                else f"- {p}: chưa có sheet dữ liệu, bỏ qua."
            )
            for p in profiles
        ]

        rows = accounts_state.load_accounts()
        for row in rows:
            if row.profile in cleared_set:
                row.current_jobs = 0
                row.completed_jobs = 0
                row.pending_jobs = 0
        accounts_state.save_accounts(rows)

        return jsonify(
            {"cleared": cleared_profiles, "log": log, "accounts": [asdict(r) for r in rows]}
        )

    @app.route("/api/gsheet/clear_folder_cache", methods=["POST"])
    def gsheet_clear_folder_cache():
        body = request.get_json(force=True, silent=True) or {}
        video_folder = str(body.get("video_folder", "")).strip()
        if not video_folder:
            return _bad_request("Thieu 'video_folder'.")
        cleared = push_cache.clear_folder_cache(video_folder)
        msg = "Đã xoá cache cho thư mục này." if cleared else "Thư mục này chưa có cache."
        return jsonify({"cleared": cleared, "log": [msg]})

    @app.route("/api/gsheet/delete_pushed_files", methods=["POST"])
    def gsheet_delete_pushed_files():
        body = request.get_json(force=True, silent=True) or {}
        video_folder = str(body.get("video_folder", "")).strip()
        if not video_folder:
            return _bad_request("Thieu 'video_folder'.")
        if not body.get("confirm"):
            return _bad_request(
                "Thieu xac nhan 'confirm' - day la thao tac xoa vinh vien, khong hoan tac."
            )

        client, err = _get_client()
        if err:
            return err
        if not _busy_lock.acquire(blocking=False):
            return _busy_response()
        try:
            profiles = client.read_profiles()
            pushed_ids = client.get_used_video_ids(profiles, video_folder)

            folder = Path(video_folder)
            deleted = 0
            missing = 0
            log: list[str] = []
            for sp_id in sorted(pushed_ids):
                file_path = folder / f"{sp_id}.mp4"
                if not file_path.exists():
                    missing += 1
                    continue
                try:
                    file_path.unlink()
                    deleted += 1
                    log.append(f"- Đã xoá vĩnh viễn: {file_path.name}")
                except OSError as exc:
                    log.append(f"- Không xoá được {file_path.name}: {exc}")
        finally:
            _busy_lock.release()

        log.append(
            f"Đã xoá vĩnh viễn {deleted} file video đã push"
            + (
                f" ({missing} job đã push nhưng không còn thấy file để xoá)."
                if missing
                else "."
            )
        )
        return jsonify({"deleted": deleted, "missing": missing, "log": log})

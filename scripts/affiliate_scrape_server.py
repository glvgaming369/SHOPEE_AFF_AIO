"""Local HTTP server - cau noi giua Tampermonkey (chay trong tung Chrome profile, tu goi
API affiliate.shopee.* that qua fetch() cua trang, co san cookie dang nhap) va SQLite
(shopee_db.py). Ly do can server rieng: Tampermonkey/trinh duyet khong doc/ghi SQLite truc
tiep duoc; nhieu Chrome profile chay SONG SONG (nhieu tai khoan) can 1 noi TRUNG TAM giu
tinh nguyen tu khi gan item vao group (try_assign_verified) de khong bi 2 profile gianh
trung 1 san pham cho 2 group khac nhau.

Chi bind 127.0.0.1 (khong 0.0.0.0) - server nay khong danh cho truy cap tu may khac.
Tampermonkey goi qua GM_xmlhttpRequest (khong phai fetch() thuong cua trang) de ne CORS
hoan toan - server KHONG can bat CORS.

Chay:
    python scripts/affiliate_scrape_server.py
    python scripts/affiliate_scrape_server.py --port 8877 --db-path artifacts/db/shopee.db
"""
import argparse
import atexit
import ctypes
import io
import json
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request
from openpyxl import Workbook, load_workbook

import chrome_launcher
import dongvanfb_client
import gsheet_push_api
import gsheet_video_scanner
import microsoft_mail_client
import shopee_categories
import shopee_db
import shopee_video_post
import videoai_client

import antidetect as _ad  # adapter chung GPM/GemLogin - xem scripts/antidetect.py

app = Flask(__name__)
gsheet_push_api.register(app)  # tab "Push Sheet" - xem scripts/gsheet_push_api.py
DB_PATH = shopee_db.DB_PATH_DEFAULT  # ghi de qua --db-path luc khoi dong, xem main()
VIDEO_PORT = None  # gan trong main() - port RIENG cho traffic dang video, xem index() + main()
VIDEO_PORTS = []  # gan trong main() (nhanh process cha) - TOAN BO port cua cac video-worker
# process con (>= 1 phan tu, xem --video-workers trong main()) - dashboard round-robin qua day
# de rai deu request post_next() len NHIEU process (nhieu loi CPU that su), xem index().
MAIN_PORT = None  # gan trong main() - port cua CHINH process nay, dung lam mainOrigin fallback
# cho node_pool khi VIDEO_PORTS rong (xem video_sources_node_pool_start()).
_KILL_ON_CLOSE_JOB = None  # gan trong main() - Windows Job Object kill-on-close, xem
# _create_kill_on_close_job()/_assign_process_to_job() - dung chung cho ca video-worker process
# con (main()) LAN process Node cua node_pool (route ben duoi) de tranh mo côi ca 2 loai.
_NODE_POOL_RUNS = {}  # run_id (str) -> {"proc", "status_path", "stop_path", "log_file",
# "source_id"} - theo doi cac phien dang video chay qua Node.js (xem khoi route
# /api/video_sources/<id>/node_pool/* ben duoi, yeu cau nguoi dung 2026-09-12 "chạy qua node
# js" - Node goi post_next() truc tiep, khong qua vong lap fetch() trong trinh duyet, tranh
# duoc van de "Initial connection" bi ket da xac nhan qua thuc nghiem chi xay ra qua Chrome).
LAUNCH_URL_DEFAULT = "https://affiliate.shopee.ph/offer/product_offer"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
USERSCRIPTS_DIR = os.path.join(SCRIPTS_DIR, "userscripts")
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)  # thu muc goc git (chua .git/) - dung cho /api/update/*
DEVICE_FINGERPRINTS_PATH = os.path.join(SCRIPTS_DIR, "device_fingerprints.json")


def _load_device_fingerprint_pool():
    """Doc lai file scripts/device_fingerprints.json MOI LAN goi (khong cache) - file nay nho
    (~75 dong) va co the duoc sua tay/ghi de bang tay giua luc server dang chay, doc lai luon
    tranh phai restart server moi khi cap nhat mau. Tra ve (templates, rn_version_default);
    file thieu/loi -> tra ve ([], '') va de caller tu bao loi ro rang (KHONG fallback bia du
    lieu gia)."""
    try:
        with open(DEVICE_FINGERPRINTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("templates") or [], data.get("rn_version_default") or ""
    except (OSError, ValueError):
        return [], ""
UPDATE_RESTART_EXIT_CODE = 42  # start_affiliate_scraper.bat doc ma nay de tu khoi dong lai

# Nguon chan ly DUY NHAT cho moi thu lien quan userscript - dashboard (index.html) doc
# qua GET /api/userscripts thay vi hardcode ten/mo ta rieng, tranh 2 noi bi lech nhau.
# Cung la danh sach trang cho /userscripts/<file> (KHONG phuc vu file ngoai danh sach nay,
# du server chi bind 127.0.0.1).
USERSCRIPTS = [
    {
        "file": "tampermonkey_affiliate_group_scraper.user.js",
        "title": "Affiliate Offer Group Scraper",
        "description": "Script CHÍNH: Chạy trên affiliate.shopee.* để gom nhóm 6 sản phẩm/group (root đạt chuẩn + tối đa 5 sản phẩm tương tự). Cần cho MỖI tài khoản/profile đang dùng để cào.",
    },
    {
        "file": "shopee_collector.user.js",
        "title": "Shopee Product Link Collector",
        "description": "Chạy trên trang Shopee thường (không phải affiliate) - cuộn trang tự động thu thập link sản phẩm, đẩy thẳng làm root vào DB hoặc xuất TXT/JSON/CSV.",
    },
    {
        "file": "shopee_ph_phone_checker.user.js",
        "title": "Shopee PH Phone Checker (SMSPool + 5sim + dongvanfb Mail)",
        "description": "1 script, 2 vai trò theo domain đang mở: Trên bất kỳ trang nào của shopee.ph - lấy số PH từ SMSPool/5sim qua API key hoặc dongvanfb mail, kiểm tra check_phone_exist, tự hủy số đã tồn tại; Trên 5sim.net - mua/hủy số bằng chính session trình duyệt (không qua API key, né rate limit riêng), gửi yêu cầu check sang tab shopee.ph qua GM_addValueChangeListener rồi tự quyết định hủy/giữ.",
    },
    {
        "file": "tampermonkey_affiliate_root_navigator.user.js",
        "title": "Affiliate Root Navigator (Navigation) - bản chống Page Unavailable",
        "description": "THAY THẾ group scraper cũ khi Shopee chặn gọi offer/product liên tiếp (Page Unavailable sau ~1 root). Shopee chỉ chấp nhận token 'af-ac-enc-sz-token' mint từ 1 report (df.infra) và mỗi report chỉ dùng được ĐÚNG 1 lần - chỉ load trang thật mới kích engine gửi report. Script điều hướng tab tới offer/product_offer/<item_id> cho TỪNG root (như người mở link), hook fetch từ document-start để chụp response offer do CHÍNH TRANG gọi (token hợp lệ), rồi đẩy server local xử lý verify/seed/gan group/finish - KHÔNG gọi thêm request thật nào tới Shopee. Cách dùng: cài script này + TẮT script 'Affiliate Offer Group Scraper' cũ, nhập device key, Start. Mỗi root tốn 1 lần load trang (~3-8s) nhưng không bị chặn kiểu token reuse.",
    },
]
USERSCRIPT_ALLOWLIST = {u["file"] for u in USERSCRIPTS}

_VERSION_RE = re.compile(r"^//\s*@version\s+(\S+)", re.MULTILINE)


def _userscript_version(filename):
    """Doc truc tiep dong '// @version' tu file .user.js that (KHONG hardcode trong
    USERSCRIPTS - se le voi file that moi lan bump version). None neu khong doc duoc/khong
    co dong @version."""
    try:
        with open(os.path.join(USERSCRIPTS_DIR, filename), "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None
    m = _VERSION_RE.search(content)
    return m.group(1) if m else None


@app.route("/", methods=["GET"])
def index():
    # video_ports duoc nhung thang vao trang qua window.__VIDEO_API_PORTS__ (xem template) - JS
    # postVideoPoolWorker round-robin qua danh sach nay de goi post_next() sang NHIEU PORT
    # (moi port = 1 process rieng, xem main()) thay vi cung origin voi trang: (1) tranh video
    # traffic (toi da ~50 request dong thoi) chiem het hang doi ~6 ket noi HTTP/1.1/domain cua
    # Chrome khien cac tab khac (vd Quan ly account) "giả treo", (2) tan dung NHIEU LOI CPU
    # that su (moi process video co GIL rieng, khong con bi 1 process/1 loi gioi han thong luong).
    return render_template("index.html", video_ports=VIDEO_PORTS)


@app.route("/api/userscripts", methods=["GET"])
def list_userscripts():
    items = [dict(u, version=_userscript_version(u["file"])) for u in USERSCRIPTS]
    return jsonify({"userscripts": items})


@app.route("/userscripts/<name>", methods=["GET"])
def userscript(name):
    """Phuc vu file .user.js THAT tu thu muc scripts/userscripts/ - dat @updateURL/
    @downloadURL trong header script tro ve day de Tampermonkey TU phat hien ban moi (so
    @version) va hien man hinh "Update" - khong can copy/dan tay nua. Content-Type dung
    de Tampermonkey/trinh duyet nhan dien day la userscript."""
    if name not in USERSCRIPT_ALLOWLIST:
        abort(404)
    path = os.path.join(USERSCRIPTS_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return Response(content, content_type="text/javascript; charset=utf-8")


def _run_git(args, timeout=30):
    """Chay 1 lenh git trong REPO_ROOT (KHONG dua vao cwd cua tien trinh - server co the
    duoc khoi dong tu bat ky thu muc nao). Tra ve subprocess.CompletedProcess (khong raise
    khi git tra ma loi khac 0 - nguoi goi tu kiem tra returncode)."""
    return subprocess.run(
        ["git"] + args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@app.route("/api/update/check", methods=["GET"])
def update_check():
    """Kiem tra co ban code moi tren remote 'origin' khong (git fetch + so sanh HEAD local
    voi origin/master) - dung cho nut 'Kiem tra cap nhat' tren dashboard. Repo PUBLIC
    (SHOPEE_AFF_AIO) nen fetch khong can dang nhap/token gi ca."""
    fetch = _run_git(["fetch", "origin", "master"])
    if fetch.returncode != 0:
        return jsonify({"error": f"git fetch that bai: {fetch.stderr.strip() or fetch.stdout.strip()}"}), 500

    local = _run_git(["rev-parse", "HEAD"])
    remote = _run_git(["rev-parse", "origin/master"])
    if local.returncode != 0 or remote.returncode != 0:
        return jsonify({"error": "Khong doc duoc commit hien tai - thu muc nay co phai git repo (da git clone) khong?"}), 500

    local_sha = local.stdout.strip()
    remote_sha = remote.stdout.strip()
    update_available = local_sha != remote_sha

    commits = []
    if update_available:
        log = _run_git(["log", f"{local_sha}..{remote_sha}", "--pretty=format:%h %s"])
        commits = [line for line in log.stdout.splitlines() if line.strip()]

    return jsonify({
        "update_available": update_available,
        "local_commit": local_sha[:7],
        "remote_commit": remote_sha[:7],
        "commits": commits,
    })


@app.route("/api/update/apply", methods=["POST"])
def update_apply():
    """Tai ban code moi nhat (git pull --ff-only tu origin/master) roi TU KHOI DONG LAI -
    dung cho nut 'Cap nhat ngay'. --ff-only: tu choi neu co local change/lich su re nhanh
    (an toan - KHONG bao gio tao merge commit hay ghi de am tham), tra loi ro nguyen nhan
    thay vi lam hong thu muc lam viec.

    Tu restart bang cach thoat tien trinh voi UPDATE_RESTART_EXIT_CODE (42) - script khoi
    dong (start_affiliate_scraper.bat) doc ma nay va TU chay lai python, ap dung code vua
    pull. Tra response VE TRUOC (qua thread nen + sleep ngan) de trinh duyet nhan duoc ket
    qua truoc khi tien trinh bi os._exit() (ngat ngang, khong chay cleanup) - day la ly do
    can thread rieng thay vi goi os._exit() ngay tai day."""
    pull = _run_git(["pull", "--ff-only", "origin", "master"])
    if pull.returncode != 0:
        return jsonify({"error": f"git pull that bai: {pull.stderr.strip() or pull.stdout.strip()}"}), 500

    def _restart_soon():
        time.sleep(1)
        os._exit(UPDATE_RESTART_EXIT_CODE)

    threading.Thread(target=_restart_soon, daemon=True).start()
    return jsonify({
        "ok": True,
        "output": pull.stdout.strip(),
        "message": "Da cap nhat code moi nhat - server dang tu khoi dong lai...",
    })


def _bad_request(msg):
    return jsonify({"error": msg}), 400


@app.errorhandler(Exception)
def _handle_error(e):
    # Khong de loi tran ra thanh HTML mac dinh cua Flask - Tampermonkey/dashboard doc
    # JSON. QUAN TRONG: HTTPException (vd tu abort(404)) da co san status code dung -
    # phai giu nguyen, khong de tat ca roi xuong 500 (da gap bug that: abort(404) o
    # /userscripts/<name> bi handler nay de thanh 500).
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    return jsonify({"error": str(e)}), 500


def _parse_cat_id(value):
    if value in (None, ""):
        return None
    return int(value)


@app.route("/api/roots/import", methods=["POST"])
def import_roots():
    body = request.get_json(force=True, silent=True) or {}
    links = body.get("links")
    if not isinstance(links, list) or not links:
        return _bad_request("thieu 'links' (danh sach string)")

    # cat_ids: cat_id RIENG cho tung link (danh sach song song voi links) - Shopee Product
    # Link Collector gui theo dinh dang nay khi cao nhieu danh muc truoc khi day vao DB 1
    # lan, tranh gan sai cat_id cuoi cung cho toan bo lo (xem shopee_db.import_roots_as_pending).
    cat_ids_raw = body.get("cat_ids")
    if cat_ids_raw is not None:
        if not isinstance(cat_ids_raw, list) or len(cat_ids_raw) != len(links):
            return _bad_request("'cat_ids' phai la danh sach cung do dai voi 'links'")
        try:
            cat_ids = [_parse_cat_id(v) for v in cat_ids_raw]
        except (TypeError, ValueError):
            return _bad_request("'cat_ids' chi duoc chua so nguyen hoac null")
        added = shopee_db.import_roots_as_pending(DB_PATH, links, cat_ids=cat_ids)
        return jsonify({"added": added})

    try:
        cat_id = _parse_cat_id(body.get("cat_id"))
    except (TypeError, ValueError):
        return _bad_request("'cat_id' phai la so nguyen")
    added = shopee_db.import_roots_as_pending(DB_PATH, links, cat_id=cat_id)
    return jsonify({"added": added})


@app.route("/api/roots/claim", methods=["POST"])
def claim_root():
    body = request.get_json(force=True, silent=True) or {}
    device_key = body.get("device_key")
    market = body.get("market")
    if not device_key:
        return _bad_request("thieu 'device_key' (ten tai khoan/profile dang claim)")
    if not market:
        return _bad_request("thieu 'market' (tab chi duoc claim root DUNG market no dang mo)")
    row = shopee_db.claim_root(DB_PATH, device_key, market)
    return jsonify({"root": row})


@app.route("/api/roots/<itemid>/assign", methods=["POST"])
def assign_root(itemid):
    body = request.get_json(force=True, silent=True) or {}
    device_key = body.get("device_key")
    market = body.get("market")
    if not device_key:
        return _bad_request("thieu 'device_key'")
    if not market:
        return _bad_request("thieu 'market'")
    result = shopee_db.assign_root_to_worker(DB_PATH, itemid, device_key, market)
    return jsonify(result), (200 if result["ok"] else 409)


@app.route("/api/workers/<device_key>/assigned_root", methods=["GET"])
def assigned_root(device_key):
    market = request.args.get("market")
    if not market:
        return _bad_request("thieu 'market' (tab chi duoc giao root DUNG market no dang mo)")
    root = shopee_db.get_assigned_root_for_worker(DB_PATH, device_key, market)
    return jsonify({"root": root})


@app.route("/api/workers/heartbeat", methods=["POST"])
def workers_heartbeat():
    body = request.get_json(force=True, silent=True) or {}
    device_key = body.get("device_key")
    status = body.get("status")
    if not device_key or not status:
        return _bad_request("thieu 'device_key' hoac 'status'")
    shopee_db.worker_heartbeat(DB_PATH, device_key, status, body.get("current_root"), body.get("market"))
    return jsonify({"ok": True})


@app.route("/api/workers", methods=["GET"])
def workers_list():
    return jsonify({"workers": shopee_db.list_workers(DB_PATH)})


@app.route("/api/workers/<device_key>", methods=["DELETE"])
def remove_worker(device_key):
    """Xoa 1 device_key khoi bang 'workers' - dung cho nut 'Xoa' o tab 'Van hanh'. Nha kem
    claim (assigned_key) cua device_key nay tren products, xem shopee_db.remove_worker()."""
    result = shopee_db.remove_worker(DB_PATH, device_key)
    return jsonify(result)


@app.route("/api/roots/<itemid>/reset", methods=["POST"])
def reset_root(itemid):
    body = request.get_json(force=True, silent=True) or {}
    market = body.get("market")
    if not market:
        return _bad_request("thieu 'market'")
    ok = shopee_db.reset_root_to_pending(DB_PATH, itemid, market)
    if not ok:
        return _bad_request(f"khong tim thay root '{itemid}' o market '{market}'")
    return jsonify({"ok": True})


@app.route("/api/roots/<itemid>/fail", methods=["POST"])
def fail_root(itemid):
    """Worker goi khi API tra loi that su cho chinh root (vd 'invalid item id') - nha
    claim + chuyen status_link='fail' de KHONG bi nhan lai vo han lan sau."""
    body = request.get_json(force=True, silent=True) or {}
    reason = body.get("reason") or "unknown_error"
    market = body.get("market")
    if not market:
        return _bad_request("thieu 'market'")
    shopee_db.mark_root_failed(DB_PATH, itemid, market, reason)
    return jsonify({"ok": True})


@app.route("/api/roots/<itemid>/recompute_merged", methods=["POST"])
def recompute_merged(itemid):
    """Tinh lai merged_link thu cong - dung cho group da 'done' TU TRUOC KHI co tinh nang
    nay (finish_root() tu dong lam viec nay cho group hoan tat SAU nay)."""
    body = request.get_json(force=True, silent=True) or {}
    market = body.get("market")
    if not market:
        return _bad_request("thieu 'market'")
    total = shopee_db.compute_merged_links(DB_PATH, itemid, market)
    return jsonify({"ok": True, "total_links": total})


@app.route("/api/roots/recompute_merged_all", methods=["POST"])
def recompute_merged_all():
    result = shopee_db.recompute_all_merged_links(DB_PATH)
    return jsonify(result)


@app.route("/api/roots/reset_insufficient_all", methods=["POST"])
def reset_insufficient_all():
    result = shopee_db.reset_all_insufficient_roots(DB_PATH)
    return jsonify(result)


@app.route("/api/candidates/recheck_cached", methods=["POST"])
def recheck_cached_candidates():
    """1 lan bam nut = 2 buoc: (1) giai phong related cua cac root DA TUNG dat nhung KHONG
    CON dat dieu kien HIEN TAI (release_disqualified_root_members() - dam bao dung nguyen
    tac "root khong dat thi khong duoc giu related" MOI LUC, khong chi luc cao lan dau),
    (2) quet lai TOAN BO candidate 'cached' (gom ca vua giai phong o buoc 1) doi chieu voi
    dieu kien hien tai va gan lai cho root phu hop neu co (recheck_cached_candidates()).
    Ca 2 buoc KHONG goi API Shopee. body (optional): {"market": "ph"} de gioi han 1 thi
    truong."""
    body = request.get_json(force=True, silent=True) or {}
    market = body.get("market") or None
    release_result = shopee_db.release_disqualified_root_members(DB_PATH, market=market)
    recheck_result = shopee_db.recheck_cached_candidates(DB_PATH, market=market)
    return jsonify({
        "roots_disqualified": release_result["roots_disqualified"],
        "released": len(release_result["released_itemids"]),
        "checked": recheck_result["checked"],
        "assigned": recheck_result["assigned"],
    })


@app.route("/api/roots/nav_complete", methods=["POST"])
def nav_complete():
    """1 lan goi duy nhat cho 1 root o che do "Root Navigator" (userscript dieu huong trang
    that): server nhan offer_data MA CHINH TRANG Shopee da goi (token da hop le), tu verify
    root, neu DAT thi seed + gan related (toi 5) + finish - gom toan bo logic truoc day
    userscript phai goi nhieu lan (verify/seed/items.verify/finish) thanh 1 request local duy
    nhat, giam diem loi va round-trip. KHONG goi bat ky API Shopee nao o day."""
    body = request.get_json(force=True, silent=True) or {}
    offer_data = body.get("offer_data")
    if not isinstance(offer_data, dict):
        return _bad_request("thieu 'offer_data' (object response.data cua offer/product)")
    itemid = str(offer_data.get("item_id") or "")
    market = body.get("market") or shopee_db.market_from_link(offer_data.get("product_link"))
    if not itemid or not market:
        return _bad_request("khong suy duoc itemid/market tu offer_data")
    row = shopee_db.map_v2_data_to_row(
        offer_data, link_type="root", groupid=itemid, market=market
    )
    verify = shopee_db.verify_root(DB_PATH, offer_data)
    if not verify.get("passes"):
        shopee_db.finish_root(DB_PATH, itemid, market)
        return jsonify({"ok": True, "outcome": "rejected", "itemid": itemid})

    settings = shopee_db.get_settings(DB_PATH)
    sold_min = settings.get("sold_min") or 0
    similar = (offer_data.get("similar_product_offers") or {}).get("list") or []
    claimed = shopee_db.seed_and_claim_candidates(DB_PATH, itemid, similar, market=market) or []
    claimed_set = {str(c) for c in claimed}
    candidates = []
    for it in similar:
        sid = str(it.get("item_id") or "")
        if not sid or sid == itemid or sid not in claimed_set:
            continue
        try:
            sold = int((it.get("batch_item_for_item_card_full") or {}).get("sold") or 0)
        except (TypeError, ValueError):
            sold = 0
        if sold <= sold_min:
            continue
        candidates.append((sold, it))
    candidates.sort(key=lambda c: -c[0])

    member = 0
    errors = []
    detail = {"similar_total": len(similar), "claimed": len(claimed), "sold_passed": 0,
              "outcomes": {"assigned": 0, "already_member": 0, "failed_criteria": 0,
                           "claimed_by_other": 0, "error": 0}}
    for sold, it in candidates:
        if member >= 5:  # GROUP_TARGET-1
            break
        detail["sold_passed"] += 1
        try:
            related_row = shopee_db.map_v2_data_to_row(
                it, link_type="related", groupid=itemid, market=market
            )
            if not related_row.get("itemid"):
                continue
            # Candidate chi co COMMISSION THEO TY LE % (seller_commission_rate/default_commission_rate)
            # ma khong kem so tien (da xac nhan that 2026-09-03: similar_product_offers.list chi
            # tra rate, cr=null) - tieu chi hien tai can so tien. Uoc luong: pct% * gia hien thi
            # (price int / 100000 = gia hien thi PHP/TH, vi du 19900000 -> ₱199.00) de co so sanh.
            # Neu response da co so tien that (commission_rate.seller_commission) thi giu nguyen.
            if not related_row.get("seller_commission"):
                it_batch = it.get("batch_item_for_item_card_full") or {}
                pct_raw = it.get("seller_commission_rate") or it.get("default_commission_rate")
                try:
                    price_raw = int(it_batch.get("price") or 0)
                except (TypeError, ValueError):
                    price_raw = 0
                if pct_raw and price_raw:
                    try:
                        pct = float(str(pct_raw).replace("%", "").strip()) / 100.0
                        price_display = price_raw / 100000.0 if price_raw > 100000 else float(price_raw)
                        est = round(pct * price_display, 2)
                        if est > 0:
                            related_row["seller_commission"] = est
                    except (TypeError, ValueError):
                        pass
            out = shopee_db.try_assign_verified(DB_PATH, related_row, itemid)
            if out:
                oc = out.get("outcome")
                if oc in detail["outcomes"]:
                    detail["outcomes"][oc] += 1
            if out and out.get("outcome") in ("assigned", "already_member"):
                member = out.get("group_member_count") or member
        except Exception as e:  # noqa: BLE001 - 1 candidate loi khong duoc lam chet ca root
            errors.append({"itemid": it.get("item_id"), "error": str(e)[:200]})
            detail["outcomes"]["error"] += 1
    shopee_db.finish_root(DB_PATH, itemid, market)
    return jsonify({
        "ok": True,
        "outcome": "done",
        "itemid": itemid,
        "member_count": member,
        "errors": errors[:20],
        "detail": detail,
    })


@app.route("/api/roots/finish", methods=["POST"])
def finish_root():
    body = request.get_json(force=True, silent=True) or {}
    itemid = body.get("itemid")
    market = body.get("market")
    if not itemid:
        return _bad_request("thieu 'itemid'")
    if not market:
        return _bad_request("thieu 'market'")
    shopee_db.finish_root(DB_PATH, itemid, market)
    return jsonify({"ok": True})


@app.route("/api/candidates/seed", methods=["POST"])
def seed_candidates():
    """items: nguyen si similar_product_offers.list[] tu response Shopee. Tra ve
    'claimed_item_ids' - CHI cac item_id nhom nay thuc su duoc giu (moi hoac da la cua
    minh tu truoc); item da bi nhom khac giu se KHONG co trong danh sach - BFS phia goi
    dung danh sach nay de biet item nao dang duoc xep vao hang doi cua chinh minh."""
    body = request.get_json(force=True, silent=True) or {}
    groupid = body.get("groupid")
    items = body.get("items")
    if not groupid or not isinstance(items, list):
        return _bad_request("thieu 'groupid' hoac 'items' (danh sach)")
    claimed = shopee_db.seed_and_claim_candidates(DB_PATH, groupid, items)
    return jsonify({"claimed_item_ids": claimed})


@app.route("/api/roots/verify", methods=["POST"])
def verify_root():
    """offer_data: nguyen si response.data tu goi that offer/product?item_id=<root>. Chi
    cap nhat metrics that cho dong root (KHONG doi status_link/claim). Tra ve 'passes' de
    userscript quyet dinh: KHONG dat -> loai luon (khong lay san pham tuong tu); DAT -> lay
    toi da 5 san pham tuong tu tu chinh similar_product_offers cua root cho du nhom 6."""
    body = request.get_json(force=True, silent=True) or {}
    offer_data = body.get("offer_data")
    if not isinstance(offer_data, dict):
        return _bad_request("thieu 'offer_data' (object)")
    result = shopee_db.verify_root(DB_PATH, offer_data)
    return jsonify(result)


@app.route("/api/items/verify", methods=["POST"])
def verify_item():
    """offer_data: nguyen si response.data tu goi that offer/product?item_id=<candidate> -
    tuc DA ton 1 request that toi Shopee cho item nay. Server tinh tieu chi + gan group
    (nguyen tu, an toan khi nhieu profile goi song song)."""
    body = request.get_json(force=True, silent=True) or {}
    groupid = body.get("groupid")
    offer_data = body.get("offer_data")
    if not groupid or not isinstance(offer_data, dict):
        return _bad_request("thieu 'groupid' hoac 'offer_data' (object)")
    row = shopee_db.map_v2_data_to_row(offer_data, link_type="related", groupid=groupid)
    if not row.get("itemid"):
        return _bad_request("offer_data khong co item_id hop le")
    result = shopee_db.try_assign_verified(DB_PATH, row, groupid)
    return jsonify(result)


@app.route("/api/items/filter_new", methods=["POST"])
def filter_new():
    body = request.get_json(force=True, silent=True) or {}
    itemids = body.get("itemids")
    if not isinstance(itemids, list):
        return _bad_request("thieu 'itemids' (danh sach)")
    new_ids = shopee_db.filter_new_itemids(DB_PATH, itemids)
    return jsonify({"new_itemids": new_ids})


@app.route("/api/items/list", methods=["GET"])
def list_items():
    """Danh sach san pham da cao (tab 'San pham' tren dashboard) - loc theo
    link_type/status_link/search (khop ten cot itemid/name/shop_name)/groupid (khop chinh
    xac 1 nhom), gioi han so dong. status_link='video_ready' = loc 'du dieu kien tao video'
    (root co merged_link, chua tao job VideoAI, co product_link - cung dieu kien voi hang
    doi tao video). Tra kem 'total' = tong so link khop bo loc (khong gioi han)."""
    link_type = request.args.get("link_type") or None
    status_link = request.args.get("status_link") or None
    search = request.args.get("search") or None
    groupid = request.args.get("groupid") or None
    market = request.args.get("market") or None
    limit = min(max(1, request.args.get("limit", 200, type=int)), 500)
    if status_link == "video_ready":
        items, total = shopee_db.video_ready_items(
            DB_PATH, market=market, search=search, groupid=groupid, limit=limit
        )
        return jsonify({"items": items, "total": total, "mode": "video_ready"})
    items = shopee_db.fetch_all_items(
        DB_PATH, link_type=link_type, status_link=status_link, search=search,
        groupid=groupid, market=market, limit=limit,
    )
    total = shopee_db.count_all_items(
        DB_PATH, link_type=link_type, status_link=status_link, search=search,
        groupid=groupid, market=market,
    )
    return jsonify({"items": items, "total": total})


@app.route("/api/items/<itemid>", methods=["DELETE"])
def delete_item(itemid):
    market = request.args.get("market")
    if not market:
        return _bad_request("thieu query param 'market'")
    ok = shopee_db.delete_item(DB_PATH, itemid, market)
    if not ok:
        return _bad_request(f"khong tim thay item '{itemid}' o market '{market}'")
    return jsonify({"ok": True})


@app.route("/api/groups/<groupid>/count", methods=["GET"])
def group_count(groupid):
    market = request.args.get("market")
    if not market:
        return _bad_request("thieu query param 'market'")
    count = shopee_db.count_group_members(DB_PATH, groupid, market)
    return jsonify({"groupid": groupid, "member_count": count})


@app.route("/api/roots/list", methods=["GET"])
def list_roots():
    market = request.args.get("market") or None
    status = request.args.get("status") or None
    return jsonify({"roots": shopee_db.list_roots_with_counts(DB_PATH, status=status, market=market)})


@app.route("/api/roots/reset_by_filter", methods=["POST"])
def reset_roots_by_filter():
    """Dat lai (ve 'pending') toan bo root theo bo loc dang chon o UI (market + trang thai) -
    dung cho nut 'Dat lai Root (theo bo loc)' o khoi 'Danh sach Root'. Chi cho phep reset root
    dang 'done'/'fail' (root pending khong co gi de reset). Nha claim + xoa fail_reason."""
    body = request.get_json(force=True, silent=True) or {}
    market = (body.get("market") or "").strip() or None
    status = (body.get("status") or "").strip()
    if status not in ("done", "fail"):
        return _bad_request("'status' chi ho tro 'done' hoac 'fail'")
    count = shopee_db.reset_roots_by_filter(DB_PATH, market=market, statuses=(status,))
    return jsonify({"ok": True, "reset_count": count, "market": market, "status": status})


@app.route("/api/items/category_stats", methods=["GET"])
def items_category_stats():
    market = request.args.get("market") or None
    return jsonify(shopee_db.category_stats(DB_PATH, market=market))


@app.route("/api/categories/name", methods=["GET"])
def category_name():
    # Dung cho Shopee Product Link Collector (shopee_collector.user.js) - hien ten danh muc
    # (vd "Pets") thay vi chi cat_id tho tren panel, ngay luc dang cao. Nhan 'url' (trang
    # Shopee hien tai) thay vi 'market' truc tiep - suy market qua market_from_link() DUNG
    # HAM CHUNG voi phan import root, tranh trung logic map domain->market o phia client.
    url = request.args.get("url") or ""
    market = shopee_db.market_from_link(url)
    try:
        cat_id = int(request.args.get("cat_id")) if request.args.get("cat_id") not in (None, "") else None
    except (TypeError, ValueError):
        return _bad_request("'cat_id' phai la so nguyen")
    cat_name = shopee_categories.cat_name_for(market, cat_id) if cat_id is not None else None
    return jsonify({"market": market, "cat_name": cat_name})


@app.route("/api/categories/list", methods=["GET"])
def categories_list():
    """Danh sach danh muc cap 1 cua market (suy tu 'url', dung ham chung market_from_link()
    - xem category_name() o tren) - dung cho dropdown chon danh muc khi cao theo tu khoa o
    Shopee Product Link Collector (tranh link cao tu tu khoa bi "mo coi" khong co danh muc)."""
    url = request.args.get("url") or ""
    market = shopee_db.market_from_link(url)
    return jsonify({"market": market, "categories": shopee_categories.list_categories(market)})


@app.route("/api/roots/market_stats", methods=["GET"])
def roots_market_stats():
    """Tong hop so root theo tung market - dung cho bang "Root theo market" + dropdown
    chon market cho auto-assign o tab "Van hanh"."""
    return jsonify({"markets": shopee_db.count_roots_by_market(DB_PATH)})


@app.route("/api/roots/<groupid>/members", methods=["GET"])
def root_members(groupid):
    market = request.args.get("market")
    if not market:
        return _bad_request("thieu query param 'market'")
    return jsonify({"members": shopee_db.list_group_members(DB_PATH, groupid, market)})


@app.route("/api/accounts", methods=["GET"])
def list_accounts():
    return jsonify({"accounts": shopee_db.list_devices(DB_PATH)})


@app.route("/api/accounts", methods=["POST"])
def add_account():
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    profile_path = body.get("profile_path")
    if not name or not profile_path:
        return _bad_request("thieu 'name' hoac 'profile_path'")
    shopee_db.add_device(DB_PATH, name, profile_path)
    return jsonify({"ok": True})


@app.route("/api/accounts/<path:profile_path>", methods=["DELETE"])
def remove_account(profile_path):
    shopee_db.remove_device(DB_PATH, profile_path)
    return jsonify({"ok": True})


@app.route("/api/accounts/<int:device_id>", methods=["PUT"])
def update_account(device_id):
    body = request.get_json(force=True, silent=True) or {}
    name = body.get("name")
    profile_path = body.get("profile_path")
    if not name or not profile_path:
        return _bad_request("thieu 'name' hoac 'profile_path'")
    shopee_db.update_device(DB_PATH, device_id, name, profile_path)
    return jsonify({"ok": True})


@app.route("/api/accounts/<name>/launch", methods=["POST"])
def launch_account(name):
    accounts = shopee_db.list_devices(DB_PATH)
    match = next((a for a in accounts if a["name"] == name), None)
    if not match:
        return _bad_request(f"khong tim thay tai khoan '{name}'")
    body = request.get_json(force=True, silent=True) or {}
    url = body.get("url") or LAUNCH_URL_DEFAULT
    try:
        proc = chrome_launcher.launch_profile(match["serial"], url)
    except (RuntimeError, ValueError) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "pid": proc.pid})


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(shopee_db.get_settings(DB_PATH))


@app.route("/api/settings", methods=["POST"])
def update_settings():
    body = request.get_json(force=True, silent=True) or {}
    result = shopee_db.update_settings(
        DB_PATH,
        promoted_7d_max=body.get("promoted_7d_max"),
        sold_min=body.get("sold_min"),
        seller_commission_vnd_min=body.get("seller_commission_vnd_min"),
        auto_assign=body.get("auto_assign"),
        dongvanfb_api_key=body.get("dongvanfb_api_key"),
        auto_assign_market=body.get("auto_assign_market"),
        kw_auto_assign=body.get("kw_auto_assign"),
        kw_auto_market=body.get("kw_auto_market"),
    )
    return jsonify(result)


@app.route("/api/video_machines", methods=["GET"])
def list_video_machines():
    return jsonify({"machines": shopee_db.list_video_machines(DB_PATH)})


@app.route("/api/video_machines", methods=["POST"])
def add_video_machine():
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    tag = (body.get("tag") or "").strip()
    pool = (body.get("pool") or "selfhostPool").strip()
    if not name or not api_key or not tag:
        return _bad_request("thieu 'name', 'api_key' hoac 'tag'")
    shopee_db.add_video_machine(DB_PATH, name, api_key, tag, pool)
    return jsonify({"ok": True})


@app.route("/api/video_machines/<int:machine_id>", methods=["DELETE"])
def remove_video_machine(machine_id):
    ok = shopee_db.remove_video_machine(DB_PATH, machine_id)
    if not ok:
        return _bad_request(f"khong tim thay may id={machine_id}")
    return jsonify({"ok": True})


@app.route("/api/video_machines/<int:machine_id>/toggle", methods=["POST"])
def toggle_video_machine(machine_id):
    body = request.get_json(force=True, silent=True) or {}
    enabled = body.get("enabled")
    if enabled is None:
        return _bad_request("thieu 'enabled' (true/false)")
    ok = shopee_db.set_video_machine_enabled(DB_PATH, machine_id, enabled)
    if not ok:
        return _bad_request(f"khong tim thay may id={machine_id}")
    return jsonify({"ok": True})


@app.route("/api/video_machines/<int:machine_id>/tag", methods=["POST"])
def update_video_machine_tag(machine_id):
    """Doi Tag (thu muc) cua 1 may tao video - dung cho nut 'Sua' canh 'Xoa' o tab 'Tao
    video'."""
    body = request.get_json(force=True, silent=True) or {}
    tag = (body.get("tag") or "").strip()
    if not tag:
        return _bad_request("thieu 'tag'")
    ok = shopee_db.set_video_machine_tag(DB_PATH, machine_id, tag)
    if not ok:
        return _bad_request(f"khong tim thay may id={machine_id}")
    return jsonify({"ok": True})


@app.route("/api/videos/stats", methods=["GET"])
def video_stats():
    return jsonify(shopee_db.count_video_push_stats(DB_PATH))


@app.route("/api/videos/stats_by_market", methods=["GET"])
def video_stats_by_market():
    """Thong ke link tao video theo TUNG market - dung cho bang "Link theo market" o tab
    "Tao video"."""
    return jsonify({"markets": shopee_db.count_video_push_stats_by_market(DB_PATH)})


@app.route("/api/videos/export.xlsx", methods=["GET"])
def export_videos_xlsx():
    """Xuat toan bo san pham DA TAO VIDEO (job_id khong null) ra .xlsx, cot A|B|C =
    itemId|Ten san pham|Link gop - dung cho nut 'Xuat Excel' tren tab 'Tao video'."""
    items = shopee_db.list_video_created_items(DB_PATH)
    wb = Workbook()
    ws = wb.active
    ws.title = "Da tao video"
    ws.append(["itemId", "Ten san pham", "Link gop"])
    for it in items:
        ws.append([it["itemid"], it["name"], it["merged_link"]])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"shopee_video_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/videos/reset", methods=["POST"])
def reset_videos():
    """Dat lai trang thai 've cho tao video' cho san pham da tao xong (job_id khong null) -
    dung cho nut 'Dat lai trang thai' o tab 'Tao video'. market (query/body, optional): chi
    reset 1 thi truong dang chon tren dropdown; bo trong = tat ca."""
    body = request.get_json(force=True, silent=True) or {}
    market = body.get("market") or None
    count = shopee_db.reset_video_jobs(DB_PATH, market=market)
    return jsonify({"reset": count})


@app.route("/api/videos/push", methods=["POST"])
def push_videos():
    """1 lo (toi da 200, gioi han cua chinh VideoAI): day cache (cho dong chua
    cache_uploaded) + tao task video (cho dong da/vua co cache) cho toi da 'limit' san pham
    dang cho (list_video_push_candidates), dung API key/tag/pool cua 1 may tao video cu the
    (machine_id, xem quan ly may o tab 'Tao video'). Dashboard tu goi lap lai endpoint nay
    (xem runVideoPush() trong index.html) toi khi 'done'=0 de xu ly het hang doi - moi lan
    goi CHI xu ly 1 lo, giu request nhanh + co progress ro rang thay vi 1 request khong lo
    cho hang nghin san pham."""
    body = request.get_json(force=True, silent=True) or {}
    limit = min(max(1, int(body.get("limit") or 200)), videoai_client.BATCH_LIMIT)
    machine_id = body.get("machine_id")
    market = body.get("market") or None
    if not machine_id:
        return _bad_request("thieu 'machine_id' - chon 1 may tao video truoc khi chay.")

    machine = shopee_db.get_video_machine(DB_PATH, machine_id)
    if not machine:
        return _bad_request(f"khong tim thay may id={machine_id}")
    if not machine.get("enabled"):
        return _bad_request(f"may '{machine['name']}' dang tat - bat len truoc khi dung.")
    api_key = machine["api_key"]
    tag = machine["tag"]
    pool = machine["pool"] or "selfhostPool"

    candidates = shopee_db.list_video_push_candidates(DB_PATH, limit, market=market)
    if not candidates:
        return jsonify({"done": 0, "pushed": 0, "created": 0, "errors": []})

    # Gom theo market (cot 'market' gio da dang tin - moi insert path deu tu suy dung tu
    # domain link, xem shopee_db.market_from_link()) vi language/prefix anh phu thuoc
    # market, va API tao task chi nhan 1 'language' chung cho ca lo.
    by_market = {}
    for row in candidates:
        by_market.setdefault(row.get("market"), []).append(row)

    total_pushed = 0
    total_created = 0
    errors = []

    for market, rows in by_market.items():
        language = videoai_client.language_for_market(market)
        url_to_itemid = {r["product_link"]: r["itemid"] for r in rows}
        url_to_merged_link = {r["product_link"]: r.get("merged_link") for r in rows}
        ready_urls = [r["product_link"] for r in rows if r.get("cache_uploaded")]
        need_cache_rows = [r for r in rows if not r.get("cache_uploaded")]

        if need_cache_rows:
            items = []
            for r in need_cache_rows:
                item = videoai_client.build_cache_item(r, market)
                if item is None:
                    errors.append({"itemid": r["itemid"], "reason": "thieu du lieu bat buoc (ten/link)"})
                    continue
                items.append(item)
            if items:
                try:
                    result = videoai_client.push_cache_batch(items, api_key)
                except Exception as e:
                    errors.append({"reason": f"loi day cache ({market}): {e}"})
                else:
                    failed_urls = {e.get("url") for e in (result.get("errors") or [])}
                    ok_itemids = []
                    for it in items:
                        if it["url"] in failed_urls:
                            errors.append({"itemid": url_to_itemid.get(it["url"]), "reason": "day cache that bai"})
                            continue
                        ok_itemids.append(url_to_itemid[it["url"]])
                        ready_urls.append(it["url"])
                    if ok_itemids:
                        shopee_db.mark_cache_uploaded(DB_PATH, [(iid, market) for iid in ok_itemids])
                        total_pushed += len(ok_itemids)

        if ready_urls:
            ready_items = [
                {"url": u, "merged_link": url_to_merged_link.get(u)} for u in ready_urls
            ]
            try:
                task_results = videoai_client.create_video_batch(
                    ready_items, api_key, tag=tag, pool=pool, language=language
                )
            except Exception as e:
                errors.append({"reason": f"loi tao video ({market}): {e}"})
            else:
                job_updates = []
                for it in task_results:
                    itemid = url_to_itemid.get(it.get("url"))
                    if not itemid:
                        continue
                    if it.get("jobId"):
                        job_updates.append((itemid, market, it["jobId"]))
                        total_created += 1
                    else:
                        errors.append({"itemid": itemid, "reason": it.get("error") or "tao video that bai"})
                if job_updates:
                    shopee_db.mark_video_jobs(DB_PATH, job_updates)

    shopee_db.log_video_push(
        DB_PATH, market, machine_id, machine["name"], limit,
        len(candidates), total_pushed, total_created, errors,
    )
    return jsonify({
        "done": len(candidates),
        "pushed": total_pushed,
        "created": total_created,
        "errors": errors,
    })


@app.route("/api/videos/log", methods=["GET"])
def videos_log():
    market = request.args.get("market") or None
    try:
        limit = min(max(1, int(request.args.get("limit") or 50)), 500)
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        return _bad_request("'limit'/'offset' phai la so nguyen")
    return jsonify(shopee_db.list_video_push_log(DB_PATH, market=market, limit=limit, offset=offset))


@app.route("/api/videos/log/clear", methods=["POST"])
def clear_videos_log():
    """Xoa lich su 'Nhat ky tao video' - dung cho nut 'Xoa nhat ky' o tab 'Tao video'. market
    (body, optional): chi xoa 1 thi truong dang chon tren dropdown; bo trong = xoa tat ca."""
    body = request.get_json(force=True, silent=True) or {}
    market = body.get("market") or None
    count = shopee_db.clear_video_push_log(DB_PATH, market=market)
    return jsonify({"deleted": count})


# ---- Tab "Tao tai khoan Shopee" (mua mail dongvanfb + doc code) ----

@app.route("/api/mail_accounts/account_types", methods=["GET"])
def mail_account_types():
    return jsonify({"account_types": dongvanfb_client.ACCOUNT_TYPES})


@app.route("/api/mail_accounts/balance", methods=["GET"])
def mail_accounts_balance():
    api_key = (shopee_db.get_settings(DB_PATH).get("dongvanfb_api_key") or "").strip()
    if not api_key:
        return _bad_request("chua cau hinh dongvanfb API key (o tab 'Tao tai khoan Shopee').")
    balance = dongvanfb_client.get_balance(api_key)
    return jsonify({"balance": balance})


@app.route("/api/mail_accounts/buy", methods=["POST"])
def mail_accounts_buy():
    api_key = (shopee_db.get_settings(DB_PATH).get("dongvanfb_api_key") or "").strip()
    if not api_key:
        return _bad_request("chua cau hinh dongvanfb API key (o tab 'Tao tai khoan Shopee').")
    body = request.get_json(force=True, silent=True) or {}
    account_type = str(body.get("account_type") or "")
    quantity = max(1, int(body.get("quantity") or 1))
    if not account_type:
        return _bad_request("thieu 'account_type'")
    result = dongvanfb_client.buy_mail(api_key, account_type, quantity)
    if not result or not result.get("status"):
        return _bad_request("Mua mail that bai: " + str(result.get("message") if result else "khong ro loi"))
    data = result.get("data") or {}
    lines = data.get("list_data") or []
    added = shopee_db.add_mail_accounts_from_buy(DB_PATH, lines, account_type, data.get("order_code"))
    return jsonify({
        "ok": True, "added": added,
        "total_amount": data.get("total_amount"), "balance": data.get("balance"),
    })


@app.route("/api/mail_accounts/add_manual", methods=["POST"])
def mail_accounts_add_manual():
    body = request.get_json(force=True, silent=True) or {}
    lines = str(body.get("lines_text") or "").splitlines()
    result = shopee_db.add_mail_accounts_manual(DB_PATH, lines)
    return jsonify({"ok": True, **result})


@app.route("/api/mail_accounts/list", methods=["GET"])
def mail_accounts_list():
    market = request.args.get("market") or None
    slot = request.args.get("slot") or None
    search = request.args.get("search") or None
    group_gpm = request.args.get("group_gpm") or None
    # has_id_gpm: '0' = loc CHUA co GPM ID, '1' = loc DA co, khong truyen = khong loc.
    has_id_gpm_raw = request.args.get("has_id_gpm")
    has_id_gpm = None if has_id_gpm_raw is None else has_id_gpm_raw == "1"
    # login_status: 'ok'/'failed'/'unchecked', khong truyen = khong loc - xem
    # shopee_db.list_mail_accounts() va yeu cau nguoi dung 2026-09-11.
    login_status = request.args.get("login_status") or None
    limit = request.args.get("limit", 500, type=int)
    rows = shopee_db.list_mail_accounts(DB_PATH, market=market, slot=slot, search=search, group_gpm=group_gpm, has_id_gpm=has_id_gpm, login_status=login_status, limit=limit)
    # with_today_count=1: kem so video da dang THANH CONG HOM NAY moi dong - dung cho bang
    # chon tai khoan o tab "Đăng video" (doi chieu voi rate_limit_video). 1 query GROUP BY
    # chung cho CA trang (xem count_success_today_by_account()), khong phai N query rieng.
    if request.args.get("with_today_count"):
        today_counts = shopee_db.count_success_today_by_account(DB_PATH)
        for r in rows:
            r["posted_today"] = today_counts.get(r["id"], 0)
    return jsonify({"accounts": rows})


@app.route("/api/mail_accounts/groups", methods=["GET"])
def mail_accounts_groups():
    """Danh sach GROUP GPM/GEM dang co trong DB (khong rong) - dung cho dropdown loc "Nhom
    GPM" o toolbar (xem shopee_db.list_mail_account_groups())."""
    groups = shopee_db.list_mail_account_groups(DB_PATH)
    return jsonify({"groups": groups})


@app.route("/api/device_fingerprints", methods=["GET"])
def device_fingerprints_list():
    """Tra ve nguyen pool mau device fingerprint (xem scripts/device_fingerprints.json) - chu
    yeu de debug/xem lai trong DevTools, nut "Tạo device fingerprint" tren UI goi thang
    /assign_bulk ben duoi chu khong can fetch pool nay truoc."""
    templates, rn_default = _load_device_fingerprint_pool()
    return jsonify({"templates": templates, "rn_version_default": rn_default, "count": len(templates)})


@app.route("/api/mail_accounts/device_fingerprint/assign_bulk", methods=["POST"])
def mail_accounts_device_fingerprint_assign_bulk():
    """Nut "Tạo device fingerprint" hang loat cho cac dong dang TICH CHON: MOI dong duoc gan
    NGAU NHIEN 1 mau (device_model + device_os_version that, xem scripts/device_fingerprints.
    json) - random.choice() TUNG dong rieng (khong phai 1 mau chung ca lo) de cac tai khoan
    khac fingerprint nhau that su. device_rn_version LUON gan dung 1 gia tri 'rn_version_default'
    (KHONG random - day la phien ban bundle React Native cua app Shopee, khong phai thong so
    rieng tung may, xem ghi chu trong file JSON). device_id KHONG dong cham toi - van la hang
    so tinh trong shopee_video_post.py, khong thuoc dien "fingerprint" duoc random o day."""
    templates, rn_default = _load_device_fingerprint_pool()
    if not templates:
        return _bad_request(f"Khong doc duoc pool mau device fingerprint ({DEVICE_FINGERPRINTS_PATH}) - kiem tra file scripts/device_fingerprints.json.")
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return _bad_request("thieu 'ids'")
    results = []
    assigned = 0
    for raw in ids:
        try:
            aid = int(str(raw).strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "id khong hop le."})
            continue
        tpl = random.choice(templates)
        row = shopee_db.update_mail_account_fields(
            DB_PATH, aid,
            device_model=tpl.get("device_model", ""),
            device_os_version=tpl.get("device_os_version", ""),
            device_rn_version=rn_default,
        )
        if row is None:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        assigned += 1
        results.append({"id": aid, "ok": True, "device_model": tpl.get("device_model", ""), "device_os_version": tpl.get("device_os_version", "")})
    return jsonify({"ok": True, "assigned": assigned, "skipped": len(results) - assigned, "results": results})


# ============================================================================================
# Tab "Quan ly nguon video" - dang ky thu muc video (<sp_id>.mp4 + *_results.xlsx, xem
# gsheet_video_scanner.build_matched_pool()) lam "nguon", theo doi thong ke da dang/pending/
# loi (dua tren shopee_db video_post_log - bang ghi lai tung lan goi shopee_video_post.
# post_video_to_shopee(), xem post_videos_cli.py). Route prefix '/api/video_sources/*' - CO Y
# KHONG dung '/api/videos/*' vi prefix do DA thuoc ve tinh nang khac hoan toan (tab "video" cu
# = VideoAI tu dong TAO video tu san pham, xem video_machines/video_push_log O TREN) - 2 tinh
# nang cung ten "video" nhung khong lien quan gi nhau, tranh nham lan route.
# ============================================================================================

def _scan_video_source(row):
    """Quet 1 nguon (build_matched_pool() + dem theo status trong video_post_log), cap nhat
    cache trong video_sources, tra ve row MOI NHAT sau khi cap nhat. Loi (thu muc bi xoa/mat
    quyen doc, thieu hoac thua file *_results.xlsx) KHONG raise - ghi vao last_scan_error va
    GIU NGUYEN cac count cu (xem update_video_source_stats()), de 1 nguon loi tam thoi khong
    lam mat du lieu thong ke da co. Luon xoa cache _MATCHED_POOL_CACHE cua thu muc nay - dung
    y (KHONG doi den khi dinh nghia cache, ham nay dung TRUOC trong file nhung Python chi
    resolve bien global luc GOI ham, khong phai luc dinh nghia): moi lan nguoi dung chu dong
    quet lai (rescan/rescan_all/them nguon/xoa video) phai phan anh dung ngay o post_next()."""
    folder, market, source_id = row["folder"], row["market"], row["id"]
    _MATCHED_POOL_CACHE.pop(str(folder), None)
    try:
        matched = gsheet_video_scanner.build_matched_pool(folder)
    except (FileNotFoundError, ValueError, OSError) as e:
        return shopee_db.update_video_source_stats(DB_PATH, source_id, last_scan_error=str(e))
    total = len(matched)
    logs = shopee_db.list_video_post_log(DB_PATH, market=market, folder=folder, limit=1000000)
    log_by_sp_id = {log_row["sp_id"]: log_row for log_row in logs}
    success = sum(1 for r in matched if (log_by_sp_id.get(r.sp_id) or {}).get("success"))
    error = sum(1 for r in matched if r.sp_id in log_by_sp_id and not log_by_sp_id[r.sp_id]["success"])
    pending = total - success - error
    return shopee_db.update_video_source_stats(
        DB_PATH, source_id, total_count=total, success_count=success,
        error_count=error, pending_count=pending, last_scan_error=None,
    )


@app.route("/api/video_sources", methods=["GET"])
def video_sources_list():
    """Danh sach nguon + thong ke DA CACHE (khong tu quet lai - bam 'Cập nhật lại nguồn'/
    'Cập nhật lại DB' de lam moi, xem _scan_video_source())."""
    return jsonify({"sources": shopee_db.list_video_sources(DB_PATH)})


@app.route("/api/video_sources", methods=["POST"])
def video_sources_add():
    """Dang ky 1 nguon moi: kiem tra thu muc ton tai + co DUNG 1 file *_results.xlsx truoc khi
    luu (bao loi ro ngay, khong luu nguon hong), roi quet ngay lan dau de co thong ke."""
    body = request.get_json(force=True, silent=True) or {}
    folder = str(body.get("folder") or "").strip()
    market = str(body.get("market") or "").strip().lower()
    if not folder:
        return _bad_request("thieu 'folder'")
    if market not in shopee_video_post.MARKET_CONFIG:
        supported = ", ".join(sorted(shopee_video_post.MARKET_CONFIG))
        return _bad_request(f"market '{market}' chua ho tro dang video (chi: {supported}) - xem shopee_video_post.MARKET_CONFIG")
    if not os.path.isdir(folder):
        return _bad_request(f"Khong tim thay thu muc: {folder}")
    try:
        results_file = gsheet_video_scanner.find_results_file(Path(folder))
    except ValueError as e:
        return _bad_request(str(e))
    if results_file is None:
        return _bad_request(f"Thu muc '{folder}' thieu file *_results.xlsx (xem quy uoc cot A/B/P trong gsheet_video_scanner.py)")
    try:
        source_id = shopee_db.add_video_source(DB_PATH, folder, market)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    row = shopee_db.get_video_source(DB_PATH, source_id)
    row = _scan_video_source(row)
    return jsonify({"ok": True, "source": row})


@app.route("/api/video_sources/<int:source_id>", methods=["DELETE"])
def video_sources_delete(source_id):
    """Go dang ky nguon - KHONG dong toi lich su video_post_log (xem shopee_db.delete_video_source())."""
    ok = shopee_db.delete_video_source(DB_PATH, source_id)
    if not ok:
        return _bad_request(f"khong tim thay nguon id={source_id}")
    return jsonify({"ok": True})


@app.route("/api/video_sources/<int:source_id>/rescan", methods=["POST"])
def video_sources_rescan(source_id):
    """Nut 'Cập nhật lại nguồn' (1 dong): quet lai DUNG thu muc nay."""
    row = shopee_db.get_video_source(DB_PATH, source_id)
    if not row:
        return _bad_request(f"khong tim thay nguon id={source_id}")
    return jsonify({"ok": True, "source": _scan_video_source(row)})


@app.route("/api/video_sources/rescan_all", methods=["POST"])
def video_sources_rescan_all():
    """Nut 'Cập nhật lại DB' (toolbar chung): quet lai TAT CA nguon dang dang ky, 1 luot."""
    sources = shopee_db.list_video_sources(DB_PATH)
    results = [_scan_video_source(row) for row in sources]
    errors = [r for r in results if r and r.get("last_scan_error")]
    return jsonify({"ok": True, "scanned": len(results), "errors": len(errors), "sources": results})


def _delete_video_files_and_log(source_row, success):
    """Dung chung cho nut 'Xóa video đã đăng' (success=True) / 'Xóa video lỗi' (success=False):
    xoa DONG THOI file .mp4 that tren dia (neu con) VA dong log video_post_log tuong ung - dung
    y nguoi dung 2026-09-10 ("xoa video da dang/loi" = don dep that su, khong chi xoa log).
    sp_id lay THANG tu DB (khong phai tu request nguoi dung) nen an toan ghep duong dan file -
    khong co rui ro path traversal."""
    folder, market = source_row["folder"], source_row["market"]
    sp_ids = shopee_db.delete_video_post_log_rows(DB_PATH, market, folder, success)
    deleted_files = 0
    missing_files = 0
    for sp_id in sp_ids:
        path = Path(folder) / f"{sp_id}.mp4"
        try:
            path.unlink()
            deleted_files += 1
        except FileNotFoundError:
            missing_files += 1
        except OSError:
            missing_files += 1
    return len(sp_ids), deleted_files, missing_files


@app.route("/api/video_sources/<int:source_id>/delete_posted", methods=["POST"])
def video_sources_delete_posted(source_id):
    """Nut 'Xóa video đã đăng': xoa file .mp4 + dong log cua MOI sp_id da dang THANH CONG
    (success=1) thuoc nguon nay - giai phong dung luong dia, khong the hoan tac."""
    row = shopee_db.get_video_source(DB_PATH, source_id)
    if not row:
        return _bad_request(f"khong tim thay nguon id={source_id}")
    log_rows, files, missing = _delete_video_files_and_log(row, success=True)
    updated = _scan_video_source(shopee_db.get_video_source(DB_PATH, source_id))
    return jsonify({"ok": True, "log_rows_deleted": log_rows, "files_deleted": files, "files_missing": missing, "source": updated})


@app.route("/api/video_sources/<int:source_id>/delete_errors", methods=["POST"])
def video_sources_delete_errors(source_id):
    """Nut 'Xóa video lỗi': xoa file .mp4 + dong log cua MOI sp_id da dang THAT BAI (success=0)
    thuoc nguon nay - khong the hoan tac."""
    row = shopee_db.get_video_source(DB_PATH, source_id)
    if not row:
        return _bad_request(f"khong tim thay nguon id={source_id}")
    log_rows, files, missing = _delete_video_files_and_log(row, success=False)
    updated = _scan_video_source(shopee_db.get_video_source(DB_PATH, source_id))
    return jsonify({"ok": True, "log_rows_deleted": log_rows, "files_deleted": files, "files_missing": missing, "source": updated})


@app.route("/api/video_sources/reset_db", methods=["POST"])
def video_sources_reset_db():
    """Nut 'Xóa DB' (video): xoa TOAN BO bang video_post_log (moi nguon/market) - KHONG dong
    toi danh sach nguon dang ky (video_sources), KHONG xoa file .mp4 nao - chi mat lich su
    dang. Khong the hoan tac."""
    deleted = shopee_db.clear_all_video_post_log(DB_PATH)
    sources = shopee_db.list_video_sources(DB_PATH)
    results = [_scan_video_source(row) for row in sources]
    return jsonify({"ok": True, "log_rows_deleted": deleted, "sources": results})


# ============================================================================================
# Tab "Đăng video" - dieu phoi tung luot dang (1 nguon x N tai khoan xoay vong), xem
# CHILL68_VIDEO_UPLOAD_RE.md + shopee_video_post.py. Kien truc: MOI request server CHI dang
# DUNG 1 video roi tra ve ngay (KHONG chay ngam trong thread) - dung y giong het pattern co
# san cua tab "Tạo Video" (VideoAI, xem push_videos()/runVideoPush() trong index.html): 1 video
# mat ~30-90s (upload that + doi xu ly + retry anti-bot) nen KHONG the nhoi vao 1 batch lon
# trong 1 request HTTP (se timeout) - JS o trinh duyet tu lap lai goi endpoint nay toi khi het
# video hoac nguoi dung bam Dung, vua co progress/log realtime vua khong can ha tang
# thread/queue rieng o server.
# ============================================================================================

_MATCHED_POOL_CACHE = {}  # folder(str) -> (xlsx_mtime, pool_list) - xem _get_matched_pool_cached()

# Video dang duoc 1 request post_next() KHAC xu ly (chua kip ghi video_post_log) - can danh
# dau de 2 request (vd 2 phien, hoac 2 process/worker khac nhau - xem "video-worker" trong
# main()) khong cung doc thay CUNG 1 sp_id "chua duoc dang" (video_post_log chua kip co dong
# nao) roi CUNG dang trung no. Claim nam trong DB (video_claims, xem shopee_db.try_claim_video()/
# release_video_claim()) - KHONG con la bien Python trong bo nho (_IN_FLIGHT_SP_IDS cu) tu khi
# trien khai NHIEU PROCESS video song song (2026-09-11, yeu cau nguoi dung "triển khai multi-
# process ... tối ưu nhất" de dung nhieu loi CPU that su - moi process co bo nho RIENG, 1 dict
# Python trong process nay KHONG the ngan process KHAC claim trung sp_id nua). Chi giu claim
# trong luc CHON video (cuc nhanh) - KHONG giu trong luc goi Shopee that (30-90s).


def _claim_next_pending(matched, market, folder):
    """Chon + 'giu cho' (claim, qua DB - xem shopee_db.try_claim_video()) video PENDING dau
    tien CHUA co request/process nao khac dang xu ly. Tra ve ProductRow hoac None (het video /
    video con lai deu dang bi giu boi noi khac - RAT HIEM, chi xay ra neu nhieu phien/process
    chay dong thoi tren 1 nguon nho). Nho goi shopee_db.release_video_claim() trong finally sau
    khi dang xong (thanh cong hay that bai)."""
    for r in matched:
        if shopee_db.already_posted(DB_PATH, r.sp_id, market, folder):
            continue
        if shopee_db.try_claim_video(DB_PATH, folder, r.sp_id):
            return r
    return None


def _get_matched_pool_cached(folder):
    """Nhu gsheet_video_scanner.build_matched_pool() nhung CACHE theo mtime file xlsx - tranh
    doc lai file .xlsx (co the vai nghin dong) o MOI lan goi post_next() lien tiep trong 1
    phien dang. Cache bi xoa (xem _scan_video_source()) moi khi nguon duoc quet lai / video bi
    xoa, nen luon phan anh dung trang thai moi nhat sau cac thao tac do."""
    results_file = gsheet_video_scanner.find_results_file(Path(folder))
    if results_file is None:
        raise FileNotFoundError(f"Không tìm thấy file *_results.xlsx trong {folder}")
    mtime = results_file.stat().st_mtime
    cached = _MATCHED_POOL_CACHE.get(folder)
    if cached and cached[0] == mtime:
        return cached[1]
    pool = gsheet_video_scanner.build_matched_pool(folder)
    _MATCHED_POOL_CACHE[folder] = (mtime, pool)
    return pool


def _load_video_signing_config():
    """Doc 2 API key that (license Chill 68) tu .env o goc repo - CUNG quy uoc voi
    telegram_notifier.load_notifier_from_env() (python-dotenv), xem .env.example. Doc lai MOI
    LAN goi (khong cache) - .env co the duoc sua tay giua luc server dang chay, cache se khien
    phai restart server moi ap dung key moi."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    server2_api_key = os.environ.get("SHOPEE_VIDEO_SERVER2_API_KEY", "").strip()
    token_api_key = os.environ.get("SHOPEE_VIDEO_TOKEN_API_KEY", "").strip()
    if not server2_api_key or not token_api_key:
        raise RuntimeError(
            "Thiếu SHOPEE_VIDEO_SERVER2_API_KEY / SHOPEE_VIDEO_TOKEN_API_KEY trong .env "
            "(copy từ .env.example, xem CHILL68_VIDEO_UPLOAD_RE.md mục 4)."
        )
    return shopee_video_post.SigningConfig(
        server2_url="https://creditmls2026video.toolshopee.vn/api/sign",
        server2_api_key=server2_api_key,
        token_api_key=token_api_key,
    )


def _normalize_proxy_for_post(raw):
    """Cot 'proxy' cua mail_accounts co the o 2 dinh dang: nguoi dung tu go tay theo quy uoc
    cu 'ip:port:user:pass' (xem shopee_video_post.parse_proxy()), HOAC dong bo tu GPM/GEM
    (raw_proxy that, vd 'socks5://127.0.0.1:5000' hay 'http://user:pass@host:port' - da la URL
    day du san). Nhan dien qua '://' de goi dung ham, tranh parse_proxy() hieu nham URL that
    la dinh dang colon-list roi ghep sai."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" in raw:
        return raw
    return shopee_video_post.parse_proxy(raw)


def _video_share_link(market, post_id):
    """URL xem/chia se video that CUA SHOPEE (dang 'https://{sv}/share-video/{post_id}') - lay
    NGUYEN VAN tu source code that da capture cua Chill 68 (bien 'videoLink' trong
    createPostOnShopee_Server1/2/3, xem CHILL68_VIDEO_UPLOAD_RE.md), KHONG url-encode post_id
    (post_id dang base64 co the chua '/'/'=' - code goc CUNG khong encode, giu dung y het)."""
    if not post_id:
        return None
    market_cfg = shopee_video_post.MARKET_CONFIG.get(market)
    if not market_cfg:
        return None
    return f"https://{market_cfg['sv']}/share-video/{post_id}"


_VIDEO_POST_NEXT_RE = re.compile(r"^/api/video_sources/\d+/post_next$")


@app.after_request
def _cors_video_post_next(resp):
    """CORS CHI danh rieng cho route post_next() (video) - xem index()/main(): dashboard
    (port CHINH, vd 8877) goi fetch() CHEO PORT sang video port (vd 8878) de traffic dang
    video (toi da ~50 request dong thoi tu postVideoPoolWorker) KHONG dung chung hang doi
    ~6 ket noi HTTP/1.1/domain cua Chrome voi cac tab khac (nguyen nhan bao cao "dashboard
    treo" khi dang chay nhieu luong). 2 port khac nhau tren CUNG host van la 2 origin khac
    nhau theo trinh duyet -> can header CORS thi request nay moi qua duoc. CHI bat cho DUNG
    route nay (regex match path) - KHONG bat toan cuc, cac API con lai giu nguyen hanh vi
    cung-origin nhu truoc (server van chi bind 127.0.0.1, khong tang bien mat bao mat)."""
    if _VIDEO_POST_NEXT_RE.match(request.path):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.route("/api/video_sources/<int:source_id>/post_next", methods=["POST"])
def video_sources_post_next(source_id):
    """Dang DUNG 1 video ke tiep (video PENDING dau tien theo thu tu trong xlsx CHUA co request
    nao khac dang xu ly - xem _claim_next_pending()) cua 1 nguon, dung tai khoan DAU TIEN trong
    'account_ids' (theo thu tu client gui - client tu xoay vong mang nay giua cac lan goi de
    phan tai deu qua nhieu tai khoan) con du dieu kien: co Cookie VA (chua dat rate_limit_video
    HOM NAY, hoac khong gioi han). An toan khi NHIEU PHIEN/PROCESS goi dong thoi (ke ca cung 1
    nguon - xem shopee_db.try_claim_video()), moi phien (tab/nhom tai khoan/thi truong khac nhau) chay doc lap,
    khong can dung phien nay de chay phien khac. Tra ve 1 trong 4 dang:
    - {done: true}: nguon nay het video pending, khong con gi de dang.
    - {done: false, retry: true}: video pending con lai DANG bi phien KHAC xu ly (hiem, chi
      xay ra khi nhieu phien dong thoi tren 1 nguon nho) - client cho ngan roi thu lai, KHONG
      phai dung han.
    - {done: false, blocked: true}: con video pending nhung KHONG tai khoan nao du dieu kien
      (het cookie/het rate limit) - client nen dung vong lap, bao nguoi dung.
    - {done: false, sp_id, success, ...}: da thu dang 1 video, xem ket qua."""
    row = shopee_db.get_video_source(DB_PATH, source_id)
    if not row:
        return _bad_request(f"khong tim thay nguon id={source_id}")
    folder, market = row["folder"], row["market"]

    body = request.get_json(force=True, silent=True) or {}
    account_ids = body.get("account_ids")
    if not isinstance(account_ids, list) or not account_ids:
        return _bad_request("thieu 'account_ids' (danh sach id tai khoan dung de xoay vong)")
    # is_ai_generated: nguoi dung tu bat/tat qua checkbox tren dashboard truoc khi dang (yeu
    # cau nguoi dung 2026-09-12 "gắn nhãn video tạo bởi AI") - xem docstring
    # shopee_video_post._create_post_body() de biet vi sao KHONG hard-code cung 1 gia tri.
    is_ai_generated = bool(body.get("is_ai_generated"))

    try:
        matched = _get_matched_pool_cached(folder)
    except (FileNotFoundError, ValueError) as e:
        return _bad_request(str(e))

    target_row = _claim_next_pending(matched, market, folder)
    if target_row is None:
        still_pending = any(not shopee_db.already_posted(DB_PATH, r.sp_id, market, folder) for r in matched)
        if not still_pending:
            return jsonify({"ok": True, "done": True, "message": "Nguồn này đã hết video pending."})
        return jsonify({
            "ok": True, "done": False, "retry": True,
            "message": "Video pending còn lại đang được phiên khác xử lý - thử lại ngay.",
        })

    # TU DAY tro di GIU claim tren target_row.sp_id (da danh dau boi _claim_next_pending(), luu
    # trong bang video_claims) - BAT BUOC release trong finally o MOI nhanh return, khong thi
    # sp_id nay "ket" toi khi qua stale_after_seconds (mac dinh 5 phut, xem
    # shopee_db.try_claim_video()) moi tu don rac duoc.
    try:
        chosen_account = None
        for raw_id in account_ids:
            try:
                aid = int(str(raw_id).strip())
            except (TypeError, ValueError):
                continue
            acc = shopee_db.get_mail_account(DB_PATH, aid)
            # cookie co the la 'FAIL' (Get Cookie lay that bai, ghi de - xem
            # mail_accounts_get_cookie()) - KHONG tinh la co cookie dung duoc.
            acc_cookie = (acc.get("cookie") or "").strip() if acc else ""
            if not acc or not acc_cookie or acc_cookie == "FAIL":
                continue
            limit = acc.get("rate_limit_video")
            if limit is not None and shopee_db.count_account_success_today(DB_PATH, aid) >= limit:
                continue
            chosen_account = acc
            break
        if chosen_account is None:
            return jsonify({
                "ok": True, "done": False, "blocked": True,
                "message": "Không có tài khoản nào đủ điều kiện (thiếu Cookie hoặc đã đạt Rate limit video/ngày).",
            })

        try:
            signing = _load_video_signing_config()
        except RuntimeError as e:
            return _bad_request(str(e))

        device_override = {
            "device_model": chosen_account.get("device_model") or "",
            "os_version": chosen_account.get("device_os_version") or "",
            "rn_version": chosen_account.get("device_rn_version") or "",
        }
        proxy = _normalize_proxy_for_post(chosen_account.get("proxy"))
        video_path = str(Path(folder) / f"{target_row.sp_id}.mp4")

        result = shopee_video_post.post_video_to_shopee(
            video_path=video_path, cookie_str=chosen_account["cookie"], caption=target_row.product_name,
            merge_links=target_row.merge_links, signing=signing, market=market, proxy=proxy,
            device_override=device_override, is_ai_generated=is_ai_generated,
        )
        # log_video_post() + increment_video_source_stats() la 1 DON VI CONG VIEC LOGIC ("video
        # nay xong, ghi ket qua + cap nhat thong ke tong") - gop chung 1 connection/1 giao dich
        # (BEGIN IMMEDIATE) thay vi de moi ham tu mo/khoa-ghi rieng (yeu cau nguoi dung
        # 2026-09-11 "phân tích và đề xuất hướng giải quyết những điểm nghẽn" - giam mot nua so
        # lan khoa-ghi file DB cho hot path nay khi hang chuc worker client goi post_next() dong
        # thoi, giup cac route khac (vd GET /api/mail_accounts/list) cho ngan hon). conn dong
        # trong finally rieng - loi o 1 trong 2 buoc ghi se rollback ca giao dich (khong con
        # truong hop 1 buoc ghi thanh cong con buoc kia bi bo lo ngam).
        #
        # PHAI dung increment_video_source_stats() (delta, ATOMIC qua SQL) o day, KHONG PHAI
        # update_video_source_stats() (ghi de gia tri tuyet doi tinh tu 'row' - bien nay la
        # snapshot lay o DAU request, TRUOC khi goi Shopee that (30-90s) - 2 luong dong thoi
        # tren cung nguon se GHI DE mat ket qua cua nhau, gay bug Pending giam sai so, xac nhan
        # qua bao loi nguoi dung 2026-09-11: 2 video dang thanh cong nhung Pending chi giam 1).
        db_conn = shopee_db.open_connection(DB_PATH)
        try:
            db_conn.execute("BEGIN IMMEDIATE")
            shopee_db.log_video_post(
                DB_PATH, sp_id=target_row.sp_id, market=market, folder=folder,
                product_name=target_row.product_name, merge_links=target_row.merge_links,
                success=result.success, post_id=result.post_id, vid=result.vid, error=result.error,
                account_id=chosen_account["id"], conn=db_conn,
            )
            # 1 video LUON roi khoi "pending" sau lan dang nay (thanh cong -> success, that bai
            # -> error) - day la so DUNG chinh xac (khong phai uoc luong).
            updated_source = shopee_db.increment_video_source_stats(
                DB_PATH, source_id,
                success_delta=(1 if result.success else 0),
                error_delta=(0 if result.success else 1),
                pending_delta=-1,
                conn=db_conn,
            )
            db_conn.commit()
        finally:
            db_conn.close()
        pending_remaining = updated_source["pending_count"]
        return jsonify({
            "ok": True, "done": False, "sp_id": target_row.sp_id, "success": result.success,
            "post_id": result.post_id, "error": result.error,
            # unrecoverable: True neu loi nay chac chan LAP LAI Y HET o lan sau (vd cookie hong
            # cau truc, tai khoan het quota that - code 400002) - client dung de NGUNG thu lai
            # tai khoan nay ngay thay vi lap du perAccountTarget lan (yeu cau nguoi dung
            # 2026-09-12, xem is_unrecoverable_account_error()).
            "unrecoverable": (not result.success) and shopee_video_post.is_unrecoverable_account_error(result.error),
            "video_link": _video_share_link(market, result.post_id) if result.success else None,
            "account_id": chosen_account["id"], "account_label": chosen_account.get("profile") or chosen_account.get("email") or chosen_account.get("shopee_id") or f"#{chosen_account['id']}",
            "pending_remaining": pending_remaining,
        })
    finally:
        shopee_db.release_video_claim(DB_PATH, folder, target_row.sp_id)


# ---- Chay pool dang video qua Node.js thay vi vong lap fetch() trong trinh duyet - yeu cau
# nguoi dung 2026-09-12 "chạy qua node js" sau khi do thuc te (PowerShell, khong qua Chrome)
# xac nhan 50 request post_next() dong thoi hoan toan on (0 timeout), trong khi CUNG luong do
# qua tab Chrome that bi ket o buoc thiet lap ket noi TCP ("Initial connection" 2 phut tren
# DevTools Timing) - nghi van do Chrome chia se lop mang voi ~43 process Chrome GPM automation
# khac dang chay cung may. Node dung client HTTP rieng, tach biet hoan toan khoi Chrome.
#
# Kien truc: dashboard (browser) POST /node_pool/start voi accountIds + cau hinh -> route nay
# ghi 1 file config JSON roi spawn `node post_video_node_worker.js <config>` (subprocess.Popen,
# KHONG cho) - script Node do TU GOI post_next() lien tuc (port nguyen logic
# postVideoClaimReadyAccount()/postVideoPoolWorker() tu templates/index.html, xem file .js) va
# GHI trang thai ra 1 file JSON rieng (statusFilePath). Dashboard POLL /node_pool/status moi
# vai giay de hien thi gan real-time - khac voi mo hinh cu (JS trinh duyet tu goi truc tiep +
# cap nhat DOM callback), o day KHONG CO ket noi truc tiep nao giua dashboard va process Node,
# chi qua 2 file JSON (status doc, stop ghi) - don gian, khong can WebSocket/SSE.
@app.route("/api/video_sources/<int:source_id>/node_pool/start", methods=["POST"])
def video_sources_node_pool_start(source_id):
    row = shopee_db.get_video_source(DB_PATH, source_id)
    if not row:
        return _bad_request(f"khong tim thay nguon id={source_id}")

    body = request.get_json(force=True, silent=True) or {}
    account_ids = body.get("account_ids")
    if not isinstance(account_ids, list) or not account_ids:
        return _bad_request("thieu 'account_ids' (danh sach id tai khoan dung de xoay vong)")
    try:
        account_ids = [int(a) for a in account_ids]
        threads = max(1, min(int(body.get("threads") or 1), len(account_ids)))
        per_account_target = max(1, int(body.get("per_account_target") or 1))
        min_delay = max(0.0, float(body.get("min_delay") or 0))
        max_delay = max(min_delay, float(body.get("max_delay") or min_delay))
    except (TypeError, ValueError):
        return _bad_request("account_ids/threads/per_account_target/min_delay/max_delay khong hop le")
    is_ai_generated = bool(body.get("is_ai_generated"))  # xem video_sources_post_next()

    run_id = uuid.uuid4().hex[:12]
    run_dir = Path(tempfile.gettempdir()) / "shopee_node_pool"
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / f"{run_id}.config.json"
    status_path = run_dir / f"{run_id}.status.json"
    stop_path = run_dir / f"{run_id}.stop"
    retry_path = run_dir / f"{run_id}.retry.json"
    log_path = run_dir / f"{run_id}.log"

    config = {
        "mainOrigin": f"http://127.0.0.1:{MAIN_PORT}",
        "videoPorts": VIDEO_PORTS,
        "sourceId": source_id,
        "accountIds": account_ids,
        "threads": threads,
        "perAccountTarget": per_account_target,
        "minDelay": min_delay,
        "maxDelay": max_delay,
        "isAiGenerated": is_ai_generated,
        "statusFilePath": str(status_path),
        "stopFilePath": str(stop_path),
        "retryFilePath": str(retry_path),
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    node_script = os.path.join(SCRIPTS_DIR, "post_video_node_worker.js")
    log_file = open(log_path, "w", encoding="utf-8")
    try:
        proc = subprocess.Popen(["node", node_script, str(config_path)], stdout=log_file, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        log_file.close()
        return _bad_request("Khong tim thay lenh 'node' - can cai Node.js (https://nodejs.org/) tren may nay de dung che do chay qua Node.")
    _assign_process_to_job(_KILL_ON_CLOSE_JOB, proc.pid)
    _NODE_POOL_RUNS[run_id] = {
        "proc": proc, "status_path": status_path, "stop_path": stop_path, "retry_path": retry_path,
        "log_file": log_file, "source_id": source_id,
    }
    return jsonify({"ok": True, "run_id": run_id})


@app.route("/api/video_sources/<int:source_id>/node_pool/status", methods=["GET"])
def video_sources_node_pool_status(source_id):
    run_id = request.args.get("run_id", "")
    run = _NODE_POOL_RUNS.get(run_id)
    if not run:
        return _bad_request(f"khong tim thay phien Node id={run_id!r}")
    try:
        data = json.loads(run["status_path"].read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        # File chua kip ghi lan dau (Node vua spawn, chua toi dong writeStatusNow() dau tien) -
        # KHONG phai loi, tra ve trang thai rong hop le de dashboard tiep tuc poll binh thuong.
        data = {"running": True, "stats": {"posted": 0, "ok": 0, "fail": 0}, "accounts": {}, "log": []}
    proc_alive = run["proc"].poll() is None
    data["proc_alive"] = proc_alive
    if not proc_alive and run["log_file"] and not run["log_file"].closed:
        # Dong file handle NGAY khi phat hien process da thoat (lazy, kiem tra moi lan status
        # duoc poll) - tranh giu handle mo vo thoi han cho 1 process da chet tu lau.
        run["log_file"].close()
    return jsonify(data)


@app.route("/api/video_sources/<int:source_id>/node_pool/stop", methods=["POST"])
def video_sources_node_pool_stop(source_id):
    body = request.get_json(force=True, silent=True) or {}
    run_id = body.get("run_id", "")
    run = _NODE_POOL_RUNS.get(run_id)
    if not run:
        return _bad_request(f"khong tim thay phien Node id={run_id!r}")
    try:
        run["stop_path"].touch()
    except OSError:
        pass
    return jsonify({"ok": True})


# Yeu cau nguoi dung 2026-09-12: tai khoan bi "⛔ Đã dừng - Cookie thiếu SPC_U/csrftoken" (loi
# unrecoverable) sau khi nguoi dung lay lai Cookie moi, can dua tai khoan do QUAY LAI pool NGAY
# trong PHIEN Node dang chay (khong can dung ca phien chi vi 1 tai khoan) - vi tien trinh Node
# chay doc lap, khong the goi thang ham JS trong do, nen dung THEM 1 file tin hieu (retryFilePath,
# cung co che voi stopFilePath) de Node tu doc lai o vong poll ke tiep (xem post_video_node_worker.js).
@app.route("/api/video_sources/<int:source_id>/node_pool/retry", methods=["POST"])
def video_sources_node_pool_retry(source_id):
    body = request.get_json(force=True, silent=True) or {}
    run_id = body.get("run_id", "")
    run = _NODE_POOL_RUNS.get(run_id)
    if not run:
        return _bad_request(f"khong tim thay phien Node id={run_id!r}")
    try:
        account_id = int(body.get("account_id"))
    except (TypeError, ValueError):
        return _bad_request("thieu 'account_id' hop le")
    if run["proc"].poll() is not None:
        return _bad_request("Tiến trình Node của phiên này đã dừng hẳn - hãy nạp tài khoản và bắt đầu phiên mới.")
    retry_path = run["retry_path"]
    # Doc-sua-ghi: nhieu lan bam "Thử lại" lien tiep (cac tai khoan khac nhau) truoc khi Node kip
    # doc file cu se GOP lai thanh 1 danh sach, tranh lenh truoc bi ghi de mat neu bam don don.
    try:
        pending = json.loads(retry_path.read_text(encoding="utf-8")) if retry_path.exists() else []
    except (OSError, ValueError):
        pending = []
    if account_id not in pending:
        pending.append(account_id)
    retry_path.write_text(json.dumps(pending), encoding="utf-8")
    return jsonify({"ok": True})


def _enrich_video_post_log(logs):
    """Kem 'account_label' (uu tien ten Profile GPM, xem yeu cau nguoi dung 2026-09-10 - de
    biet dung PROFILE nao dang, khong phai chi email) + 'video_link' vao tung dong log doc
    tu video_post_log - dung chung cho video_sources_log() (1 nguon) va video_post_log_all()
    (nhieu nguon, tab 'Log-upload'). Dong log cu/tao qua CLI khong co account_id se hien
    'account_label: null' (khong loi)."""
    account_ids = {r["account_id"] for r in logs if r.get("account_id")}
    accounts = {a["id"]: a for a in shopee_db.list_mail_accounts_by_ids(DB_PATH, list(account_ids))} if account_ids else {}
    for r in logs:
        acc = accounts.get(r.get("account_id"))
        r["account_label"] = (acc.get("profile") or acc.get("email") or acc.get("shopee_id")) if acc else None
        r["video_link"] = _video_share_link(r.get("market"), r["post_id"]) if r.get("success") else None
    return logs


@app.route("/api/video_sources/<int:source_id>/log", methods=["GET"])
def video_sources_log(source_id):
    """Lich su dang cua 1 nguon (video_post_log loc theo dung market+folder cua nguon) - dung
    cho bang 'Lịch sử đăng' truoc day o tab 'Đăng video' (nay chi con dung noi bo, UI da
    chuyen sang goi /api/video_post_log qua tab 'Log-upload' - xem yeu cau nguoi dung
    2026-09-11)."""
    row = shopee_db.get_video_source(DB_PATH, source_id)
    if not row:
        return _bad_request(f"khong tim thay nguon id={source_id}")
    limit = request.args.get("limit", 100, type=int)
    logs = shopee_db.list_video_post_log(DB_PATH, market=row["market"], folder=row["folder"], limit=limit)
    return jsonify({"logs": _enrich_video_post_log(logs)})


@app.route("/api/video_post_log", methods=["GET"])
def video_post_log_all():
    """Lich su dang video CROSS-SOURCE (khong bat buoc chon 1 nguon cu the) - dung cho tab
    'Log-upload' (yeu cau nguoi dung 2026-09-11: 'Lịch sử đăng nên phát triển thành 1 tab
    riêng "Log-upload"', tach ra khoi tung phien cua tab 'Đăng video' de KHONG chiem nhieu
    khong gian cua khu vuc chinh - bang tai khoan). Filter deu OPTIONAL qua query string:
    market, folder (ten nguon), success ('1'/'0')."""
    market = request.args.get("market") or None
    folder = request.args.get("folder") or None
    success_raw = request.args.get("success")
    success = None if success_raw in (None, "") else success_raw == "1"
    limit = request.args.get("limit", 200, type=int)
    logs = shopee_db.list_video_post_log(DB_PATH, market=market, folder=folder, success=success, limit=limit)
    return jsonify({"logs": _enrich_video_post_log(logs)})


@app.route("/api/mail_accounts/export.xlsx", methods=["GET"])
def export_mail_accounts_xlsx():
    """Xuat .xlsx: neu truyen ?ids=1,2,3 (danh sach dong dang TICH CHON tren UI) thi xuat
    DUNG cac dong do (khong can phai co shopee_id); khong truyen ids thi xuat tat ca mail da
    tao tai khoan (co shopee_id) nhu hanh vi cu. Cot 'Group GPM'/'ID GPM' kem theo de import
    lai (nut Import Excel) khong mat du lieu GPM."""
    ids_raw = (request.args.get("ids") or "").strip()
    if ids_raw:
        ids = [int(x) for x in ids_raw.split(",") if x.strip().lstrip("-").isdigit()]
        accounts = shopee_db.list_mail_accounts_by_ids(DB_PATH, ids)
    else:
        accounts = shopee_db.list_created_mail_accounts(DB_PATH)
    wb = Workbook()
    ws = wb.active
    ws.title = "Tai khoan Shopee"
    ws.append([
        "Full info", "Email", "PassEmail", "Shopee_id", "Shopee Password", "Device", "Profile", "Group GPM", "ID GPM",
        "Slot", "Market", "Shopee_code", "Proxy", "Device Model", "Device OS Version",
        "Device RN Version", "Rate Limit Video", "Thoi gian tao",
    ])
    for a in accounts:
        ws.append([
            a["full_info"], a["email"], a["password"], a["shopee_id"], a.get("shopee_password") or "",
            a["device"], a["profile"], a.get("group_gpm") or "", a.get("id_gpm") or "",
            a["slot"], a["market"], a["shopee_code"],
            a.get("proxy") or "", a.get("device_model") or "", a.get("device_os_version") or "",
            a.get("device_rn_version") or "", a.get("rate_limit_video"),
            a["created_at"],
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"shopee_accounts_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_IMPORT_MAIL_ACCOUNTS_COLUMN_MAP = {
    "full info": "full_info",
    "shopee_id": "shopee_id",
    "shopee password": "shopee_password",
    "device": "device",
    "profile": "profile",
    "group gpm": "group_gpm",
    "id gpm": "id_gpm",
    "slot": "slot",
    "market": "market",
    "shopee_code": "shopee_code",
    "proxy": "proxy",
    "device model": "device_model",
    "device os version": "device_os_version",
    "device rn version": "device_rn_version",
    "rate limit video": "rate_limit_video",
}


@app.route("/api/mail_accounts/import_xlsx", methods=["POST"])
def import_mail_accounts_xlsx():
    """Nhap lai file .xlsx dung dinh dang cot cua export_mail_accounts_xlsx() (nut 'Import
    Excel' canh 'Xuat Excel' tren tab 'Tao tai khoan Shopee') - xem
    shopee_db.import_mail_accounts_from_rows(). Doc header theo TEN cot (khong phu thuoc thu
    tu) de khop voi _IMPORT_MAIL_ACCOUNTS_COLUMN_MAP, bo qua cot 'Email'/'PassEmail'/'Thoi
    gian tao' (khong can, xem docstring ham do)."""
    file = request.files.get("file")
    if file is None or not file.filename:
        return _bad_request("chua chon file .xlsx")
    try:
        wb = load_workbook(file, read_only=True, data_only=True)
    except Exception as e:
        return _bad_request(f"khong doc duoc file .xlsx: {e}")
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        return _bad_request("file rong, khong co dong tieu de")
    col_index = {str(h).strip().lower(): i for i, h in enumerate(header) if h}
    if "full info" not in col_index:
        return _bad_request("thieu cot 'Full info' - file khong dung dinh dang xuat")
    rows = []
    for row in rows_iter:
        if row is None:
            continue
        row_dict = {}
        for header_name, field in _IMPORT_MAIL_ACCOUNTS_COLUMN_MAP.items():
            idx = col_index.get(header_name)
            if idx is not None and idx < len(row):
                row_dict[field] = row[idx]
        rows.append(row_dict)
    result = shopee_db.import_mail_accounts_from_rows(DB_PATH, rows)
    return jsonify({"ok": True, **result})


@app.route("/api/mail_accounts/check_shopee_id", methods=["GET"])
def mail_accounts_check_shopee_id():
    """Tra cac mail KHAC dang dung cung shopee_id nay - dung cho canh bao chong nhap trung
    (popup xac nhan) o tab 'Tạo tài khoản Shopee' truoc khi luu that su."""
    shopee_id = (request.args.get("shopee_id") or "").strip()
    exclude_id = request.args.get("exclude_id", type=int)
    accounts = shopee_db.find_mail_accounts_by_shopee_id(DB_PATH, shopee_id, exclude_id=exclude_id)
    return jsonify({"accounts": accounts})


@app.route("/api/mail_accounts/<int:account_id>", methods=["POST"])
def mail_accounts_update(account_id):
    body = request.get_json(force=True, silent=True) or {}
    row = shopee_db.update_mail_account_fields(
        DB_PATH, account_id,
        shopee_id=body.get("shopee_id"), device=body.get("device"),
        profile=body.get("profile"), group_gpm=body.get("group_gpm"),
        id_gpm=body.get("id_gpm"),
        slot=body.get("slot"), market=body.get("market"),
        engine=body.get("engine"), cookie=body.get("cookie"),
        proxy=body.get("proxy"), device_model=body.get("device_model"),
        device_os_version=body.get("device_os_version"),
        device_rn_version=body.get("device_rn_version"),
        rate_limit_video=body.get("rate_limit_video"),
        shopee_password=body.get("shopee_password"),
    )
    if row is None:
        return _bad_request(f"khong tim thay mail id={account_id}")
    return jsonify({"account": row})


@app.route("/api/mail_accounts/shopee_password/set_bulk", methods=["POST"])
def mail_accounts_shopee_password_set_bulk():
    """Nut 'Tạo pass hàng loạt': ghi DUNG 1 password nguoi dung nhap cho MOI dong dang TICH
    CHON tren UI - xem shopee_db.bulk_set_shopee_password()."""
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    password = body.get("password")
    if not isinstance(ids, list) or not ids:
        return _bad_request("thieu 'ids'")
    if not password:
        return _bad_request("thieu 'password'")
    updated = shopee_db.bulk_set_shopee_password(DB_PATH, ids, password)
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/mail_accounts/group_gpm/set_bulk", methods=["POST"])
def mail_accounts_group_gpm_set_bulk():
    """Nut 'Gom nhóm': GOI LEN GPM/GEM de CHUYEN NHOM TRUC TIEP tren PROFILE THAT truoc (qua
    _ad.update_profile_group() - antidetect.py), CUNG 1 group_id (id nhom THAT, lay tu dropdown
    frontend da doi sang gui id thay vi ten - xem yeu cau nguoi dung 2026-09-11 'khi xac nhan
    thi hay goi len thay doi nhom tren gpm roi moi dong bo xuong') cho MOI dong dang tich chon.
    KHONG ghi thang vao DB local o day - client TU goi /gpm/sync NGAY SAU KHI route nay tra ve
    thanh cong de dong bo lai dung gia tri THAT tu GPM/GEM xuong DB, cung mo hinh voi
    proxy/import_bulk ('Set-proxy'). Dong chua co GPM/GEM ID (chua 'Tạo profile') se bi bo qua -
    khong the doi nhom tren 1 profile chua ton tai. CAC DONG PHAI CUNG 1 ENGINE (frontend da tu
    kiem tra truoc khi mo popup, vi group_id chi co y nghia trong PHAM VI 1 engine)."""
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    group_id = str(body.get("group_id") or "").strip()
    if not isinstance(ids, list) or not ids:
        return _bad_request("thieu 'ids'")
    if not group_id:
        return _bad_request("thieu 'group_id'")
    updated = 0
    results = []
    for raw_id in ids:
        try:
            aid = int(str(raw_id).strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "id khong hop le."})
            continue
        row = shopee_db.get_mail_account(DB_PATH, aid)
        if not row:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        profile_id = (row.get("id_gpm") or "").strip()
        if not profile_id:
            results.append({"id": aid, "ok": False, "error": "Chua co GPM/GEM ID - hay 'Tạo profile' truoc khi gom nhóm."})
            continue
        engine = _row_engine(row)
        try:
            _ad.update_profile_group(engine, profile_id=profile_id, group_id=group_id)
        except _ad.AntidetectError as e:
            results.append({"id": aid, "ok": False, "error": str(e)})
            continue
        updated += 1
        results.append({"id": aid, "ok": True})
    return jsonify({"ok": True, "updated": updated, "results": results})


# Danh sach thi truong hop le - khop CHINH XAC MAIL_MARKET_OPTIONS o frontend (dropdown cot
# 'Thị trường' tung dong) - dung de validate nut 'Add-market' hang loat (yeu cau nguoi dung
# 2026-09-11), tranh ghi rac vao cot market neu co request thu cong sai dinh dang.
_VALID_MAIL_MARKETS = {"PH", "TH", "MY", "ID", "VN", "SG"}


@app.route("/api/mail_accounts/market/set_bulk", methods=["POST"])
def mail_accounts_market_set_bulk():
    """Nut 'Add-market': ghi DUNG 1 thi truong (chon tu dropdown) cho MOI dong dang TICH CHON -
    xem shopee_db.bulk_set_market()."""
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    market = str(body.get("market") or "").strip().upper()
    if not isinstance(ids, list) or not ids:
        return _bad_request("thieu 'ids'")
    if market not in _VALID_MAIL_MARKETS:
        return _bad_request(f"thi truong khong hop le: '{market}'")
    updated = shopee_db.bulk_set_market(DB_PATH, ids, market)
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/mail_accounts/rate_limit_video/set_bulk", methods=["POST"])
def mail_accounts_rate_limit_video_set_bulk():
    """Nut 'Add rate-limit': ghi DUNG 1 gia tri Rate limit video/ngày (nhap trong popup - de
    trong = xoa gioi han) cho MOI dong dang TICH CHON - xem shopee_db.bulk_set_rate_limit_video().
    KHAC cac route bulk khac: 'rate_limit_video' rong/thieu trong body la LUA CHON HOP LE
    (nghia la 'khong gioi han'), khong bi tu choi nhu 'thieu tham so'."""
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return _bad_request("thieu 'ids'")
    raw = body.get("rate_limit_video")
    if raw is None or raw == "":
        rate_limit_video = None
    else:
        try:
            rate_limit_video = int(raw)
        except (TypeError, ValueError):
            return _bad_request("rate_limit_video phai la so nguyen hoac de trong")
        if rate_limit_video < 0:
            return _bad_request("rate_limit_video khong duoc am")
    updated = shopee_db.bulk_set_rate_limit_video(DB_PATH, ids, rate_limit_video)
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/mail_accounts/id_gpm/import_bulk", methods=["POST"])
def mail_accounts_id_gpm_import_bulk():
    """Nut 'Import GPM ID' hang loat: gan MOI dong 1 GPM ID KHAC NHAU (theo dung cap {id,
    id_gpm} client gui, da ghep san theo dung thu tu cac dong dang tich chon <-> dung thu tu
    tung dong nguoi dung nhap trong popup) - KHAC bulk_set_shopee_password() (dat CUNG 1 gia
    tri cho tat ca). Khong tu dong bo GPM o day - client tu goi /gpm/sync ngay sau khi route
    nay tra ve thanh cong (xem yeu cau nguoi dung 2026-09-10: "Đồng thời kích hoạt đồng bộ
    gpm"), de lay lai dung Profile/Group/Proxy that tu ID vua gan."""
    body = request.get_json(force=True, silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return _bad_request("thieu 'items' (danh sach {id, id_gpm})")
    updated = 0
    results = []
    for it in items:
        if not isinstance(it, dict):
            results.append({"id": None, "ok": False, "error": "dong khong hop le."})
            continue
        try:
            aid = int(str(it.get("id") or "").strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "id khong hop le."})
            continue
        id_gpm = str(it.get("id_gpm") or "").strip()
        row = shopee_db.update_mail_account_fields(DB_PATH, aid, id_gpm=id_gpm)
        if row is None:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        updated += 1
        results.append({"id": aid, "ok": True, "id_gpm": id_gpm})
    return jsonify({"ok": True, "updated": updated, "results": results})


def _validate_proxy_raw_line(raw_line):
    """1 dong proxy nguoi dung dan trong popup "Set-proxy" - CHI KIEM TRA dinh dang, KHONG bien
    doi/them scheme gi ca (xem yeu cau nguoi dung 2026-09-11: da tung tu dong ghep thanh URL
    'scheme://user:pass@host:port' truoc day nhung SAI - dung phai gui THANG dung nguyen van
    dinh dang nguoi dung go 'host:port:user:pass' len GPM/GEM, KHONG qua buoc doi sang URL).
    Chap nhan 'host:port:user:pass', 'host:port' (khong auth), hoac da la URL day du (co
    '://', dan thang). Tra ve chuoi da trim, hoac None neu sai dinh dang."""
    raw_line = (raw_line or "").strip()
    if not raw_line:
        return None
    if "://" in raw_line:
        return raw_line
    parts = raw_line.split(":")
    if len(parts) in (2, 4):
        return raw_line
    return None


@app.route("/api/mail_accounts/proxy/import_bulk", methods=["POST"])
def mail_accounts_proxy_import_bulk():
    """Nut 'Set-proxy' hang loat: GOI LEN GPM/GEM de CAP NHAT raw_proxy TRUC TIEP tren PROFILE
    THAT truoc (qua _ad.update_profile_proxy() - antidetect.py), MOI dong 1 proxy KHAC NHAU
    (theo dung cap {id, proxy_raw} client gui, da ghep san theo dung thu tu cac dong dang tich
    chon <-> dung thu tu tung dong nguoi dung dan trong popup). GUI THANG dung nguyen van chuoi
    nguoi dung nhap (vd 'host:port:user:pass') len GPM/GEM - KHONG con tu dong ghep thanh URL
    co scheme nhu truoc (xem _validate_proxy_raw_line() va yeu cau nguoi dung 2026-09-11: bug
    da bao "http://user:pass@host:port sai dinh dang, dung phai la host:port:user:pass").
    KHONG ghi thang vao DB local o day - client TU goi /gpm/sync NGAY SAU KHI route nay tra ve
    thanh cong de dong bo lai dung gia tri THAT tu GPM/GEM xuong DB, cung mo hinh voi
    id_gpm/import_bulk. Dong chua co GPM/GEM ID (chua 'Tạo profile') se bi bo qua - khong the
    cap nhat proxy tren 1 profile chua ton tai."""
    body = request.get_json(force=True, silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return _bad_request("thieu 'items' (danh sach {id, proxy_raw})")
    updated = 0
    results = []
    for it in items:
        if not isinstance(it, dict):
            results.append({"id": None, "ok": False, "error": "dong khong hop le."})
            continue
        try:
            aid = int(str(it.get("id") or "").strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "id khong hop le."})
            continue
        proxy_raw = str(it.get("proxy_raw") or "").strip()
        proxy_val = _validate_proxy_raw_line(proxy_raw)
        if not proxy_val:
            results.append({"id": aid, "ok": False,
                            "error": f"Dinh dang proxy khong hop le: '{proxy_raw}' (can 'host:port:user:pass' hoac 'host:port')"})
            continue
        row = shopee_db.get_mail_account(DB_PATH, aid)
        if not row:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        profile_id = (row.get("id_gpm") or "").strip()
        if not profile_id:
            results.append({"id": aid, "ok": False, "error": "Chua co GPM/GEM ID - hay 'Tạo profile' truoc khi dat proxy."})
            continue
        engine = _row_engine(row)
        try:
            _ad.update_profile_proxy(engine, profile_id=profile_id, raw_proxy=proxy_val)
        except _ad.AntidetectError as e:
            results.append({"id": aid, "ok": False, "error": str(e)})
            continue
        updated += 1
        results.append({"id": aid, "ok": True, "proxy": proxy_val})
    return jsonify({"ok": True, "updated": updated, "results": results})


@app.route("/api/mail_accounts/proxy/clear_bulk", methods=["POST"])
def mail_accounts_proxy_clear_bulk():
    """Nut 'Xoá proxy' trong popup 'Set-proxy': GOI LEN GPM/GEM XOA raw_proxy (chuoi rong -
    ca 2 engine hieu la 'khong co proxy', xem update_profile_proxy()) cua TUNG dong dang tich
    chon - KHONG ghi thang vao DB, client tu goi /gpm/sync sau khi thanh cong (cung mo hinh voi
    proxy/import_bulk, xem yeu cau nguoi dung 2026-09-11)."""
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return _bad_request("thieu 'ids'")
    updated = 0
    results = []
    for raw_id in ids:
        try:
            aid = int(str(raw_id).strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "id khong hop le."})
            continue
        row = shopee_db.get_mail_account(DB_PATH, aid)
        if not row:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        profile_id = (row.get("id_gpm") or "").strip()
        if not profile_id:
            results.append({"id": aid, "ok": False, "error": "Chua co GPM/GEM ID (bo qua)."})
            continue
        engine = _row_engine(row)
        try:
            _ad.update_profile_proxy(engine, profile_id=profile_id, raw_proxy="")
        except _ad.AntidetectError as e:
            results.append({"id": aid, "ok": False, "error": str(e)})
            continue
        updated += 1
        results.append({"id": aid, "ok": True})
    return jsonify({"ok": True, "updated": updated, "results": results})


@app.route("/api/mail_accounts/profile/set_bulk", methods=["POST"])
def mail_accounts_profile_set_bulk():
    """Nut 'Tạo tên profile' hang loat: gan MOI dong 1 ten profile KHAC NHAU (tien to co dinh +
    hau to tang dan, da tinh san o client - xem bulkGenerateProfileNames() trong index.html)
    - CUNG dang {id, profile} nhu id_gpm/import_bulk, chi khac field dich."""
    body = request.get_json(force=True, silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return _bad_request("thieu 'items' (danh sach {id, profile})")
    updated = 0
    results = []
    for it in items:
        if not isinstance(it, dict):
            results.append({"id": None, "ok": False, "error": "dong khong hop le."})
            continue
        try:
            aid = int(str(it.get("id") or "").strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "id khong hop le."})
            continue
        profile = str(it.get("profile") or "").strip()
        row = shopee_db.update_mail_account_fields(DB_PATH, aid, profile=profile)
        if row is None:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        updated += 1
        results.append({"id": aid, "ok": True, "profile": profile})
    return jsonify({"ok": True, "updated": updated, "results": results})


@app.route("/api/mail_accounts/<int:account_id>", methods=["DELETE"])
def mail_accounts_delete(account_id):
    ok = shopee_db.delete_mail_account(DB_PATH, account_id)
    if not ok:
        return _bad_request(f"khong tim thay mail id={account_id}")
    return jsonify({"ok": True})


@app.route("/api/mail_accounts/clear", methods=["POST"])
def mail_accounts_clear():
    deleted = shopee_db.clear_mail_accounts(DB_PATH)
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/mail_accounts/<int:account_id>/get_code", methods=["POST"])
def mail_accounts_get_code(account_id):
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    # Doc TRUC TIEP qua Graph (microsoft_mail_client) la duong CHINH - khong ton phi/khong
    # phu thuoc dich vu ngoai. Fallback ve dongvanfb CHI khi duong truc tiep loi (vd token
    # nay co van de rieng voi cach goi cua ta) - dongvanfb co 2 nhanh Graph/IMAP rieng, da
    # tung ghi nhan thuc te co tai khoan 1 nhanh loi nhung nhanh kia van doc duoc (xem
    # dongvanfb_client.py). Van luu lai refresh_token moi (Microsoft cap kem moi lan goi) nhu
    # thoi quen an toan - KHONG bat buoc, da kiem chung token cu van dung duoc binh thuong
    # sau khi "bi thay" (xem ghi chu dau file microsoft_mail_client.py).
    try:
        code, note, new_refresh_token = microsoft_mail_client.fetch_shopee_code(row["refresh_token"], row["client_id"])
        if new_refresh_token and new_refresh_token != row["refresh_token"]:
            shopee_db.update_mail_account_refresh_token(DB_PATH, account_id, new_refresh_token)
    except microsoft_mail_client.MicrosoftMailError as e:
        code, note = dongvanfb_client.fetch_shopee_code(row["email"], row["refresh_token"], row["client_id"])
        note = f"[Graph trực tiếp lỗi: {e}] Fallback dongvanfb -> {note}"
    shopee_db.set_mail_account_code(DB_PATH, account_id, code)
    return jsonify({"code": code, "note": note})


@app.route("/api/mail_accounts/<int:account_id>/activate_login", methods=["POST"])
def mail_accounts_activate_login(account_id):
    """Doc mail 'co lan dang nhap moi' cua Shopee (tu info@security.shopee.<tld>), trich
    link kich hoat dang "https://<tld>.shp.ee/dlink/<code>", roi mo link do bang Chrome.
    Uu tien mo DUNG profile Shopee da dang ky (cot 'Thiết bị' o tab Mail Accounts, khop ten
    voi 1 dong trong 'devices' - hop ly hon vi link kich hoat thuong can dang nhap dung
    tai khoan Shopee lien quan) - NEU khong dien 'Thiết bị' hoac khong khop dong nao, fallback
    ve 1 cua so Chrome RIENG co dinh (chrome_launcher.launch_activation_link()) de KHONG
    dieu huong tab/cua so nguoi dung dang lam viec (xem ghi chu ham do). CHUA co fallback
    dongvanfb cho tinh nang nay (dongvanfb khong co endpoint rieng cho link kich hoat, chi
    co endpoint doc ma OTP)."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    try:
        link, note, new_refresh_token = microsoft_mail_client.fetch_login_link(row["refresh_token"], row["client_id"])
        if new_refresh_token and new_refresh_token != row["refresh_token"]:
            shopee_db.update_mail_account_refresh_token(DB_PATH, account_id, new_refresh_token)
    except microsoft_mail_client.MicrosoftMailError as e:
        return jsonify({"error": f"Lỗi đọc mail: {e}"}), 500
    if not link:
        return jsonify({"link": None, "note": note})
    device_name = (row.get("device") or "").strip()
    matched_device = None
    if device_name:
        devices = shopee_db.list_devices(DB_PATH)
        matched_device = next((d for d in devices if d["name"] == device_name), None)
    try:
        if matched_device:
            proc = chrome_launcher.launch_profile(matched_device["serial"], link)
        else:
            proc = chrome_launcher.launch_activation_link(link)
    except (RuntimeError, ValueError) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"link": link, "note": note, "pid": proc.pid, "profile": matched_device["name"] if matched_device else "ActivationLinks"})


# ============================================================================
# Nut "Tạo profile" / "Mở profile" tren tab Mail Accounts (dong mail -> profile GPM).
# ============================================================================

# Market nguoi dung nhap o cot 'Thị trường' cua tab Mail (chi co PH/TH/MS trong dropdown)
# -> code GPM nho thuong dung trong _GPM_HOME_URL (ph/th/my/...).
_GPM_MARKET_CODE = {
    "PH": "ph", "TH": "th", "MS": "my", "MY": "my",
    "ID": "id", "VN": "vn", "SG": "sg",
}


def _gpm_market_home(market):
    """Market tren dong mail (PH/TH/MS/...) -> (url trang chu Shopee, code GPM). Mac dinh PH."""
    code = _GPM_MARKET_CODE.get(str(market or "").strip().upper(), "ph")
    return _GPM_HOME_URL.get(code, _GPM_HOME_URL["ph"]), code


def _check_shopee_cookie_alive(cookie_str, market):
    """Kiem tra cookie da luu (cot 'Cookie') con dang nhap duoc voi Shopee hay khong - KHONG
    can mo browser/GPM, chi 1 request HTTP TRUC TIEP toi API that cua Shopee, dung cho nut
    'Mở profile' (bo qua khong can mo browser neu cookie con song - xem yeu cau nguoi dung
    2026-09-11 "check cookie còn sống hay không trước khi mở profile").
    Da xac nhan THAT (2026-09-11, cookie that cua profile TH-00002):
      GET /api/v4/account/get_profile voi header Cookie ->
        con song : {"error": 0, "data": {"user_profile": {"userid": ..., ...}}}
        chet/het han: {"error": 19, "error_msg": "Failed to authenticate", "data": null}
    (dung endpoint nay thay vi mo trang SPA /user/account/profile qua requests tran - trang do
    la React SPA, auth check chay o client-side JS NEN GET HTML tho se LUON tra 200 bat ke da
    dang nhap hay chua, khong dung de kiem tra duoc qua 1 request HTTP don gian).
    Tra ve (alive: bool, detail: str)."""
    if not cookie_str or not cookie_str.strip():
        return False, "Chua co cookie."
    home_url, _code = _gpm_market_home(market)
    host = home_url.split("//", 1)[-1].rstrip("/")
    try:
        r = _requests.get(
            f"https://{host}/api/v4/account/get_profile",
            headers={
                "Cookie": cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": f"https://{host}/",
            },
            timeout=15,
        )
    except Exception as e:
        return False, f"Loi ket noi khi kiem tra cookie: {e}"
    try:
        j = r.json()
    except Exception:
        return False, f"Phan hoi khong phai JSON khi kiem tra cookie (HTTP {r.status_code})."
    err = j.get("error")
    userid = ((j.get("data") or {}).get("user_profile") or {}).get("userid")
    if err == 0 and userid:
        return True, f"Cookie con dang nhap (userid={userid})."
    return False, j.get("error_msg") or f"Cookie khong con hop le (error={err})."


def _gpm_list_page_items(payload):
    """GPM Local API tra list dang {"data": {...phan trang, "data": [items...]}} hoac truc tiep
    [items] - trich ra dung danh sach item (cung kieu xu ly nhu gpm_groups()/gpm_profiles())."""
    raw = payload.get("data")
    if isinstance(raw, dict):
        return raw.get("data") or []
    return raw or []


def _gpm_api_all_pages(path, page_size=5000, timeout=10):
    """GET /api/v1/profiles va /api/v1/groups cua GPM CO PHAN TRANG, mac dinh page_size=30
    (xem "gpm Login/https___gpmlogin_com_doc_.html.txt") - goi _gpm_api() 1 lan nhu truoc day
    CHI lay toi da 30 item dau, khien cac dong co id_gpm/nhom NAM NGOAI 30 item do bi
    mail_accounts_gpm_sync() hieu nham la "khong con ton tai" roi TU XOA id_gpm dang dung (bug
    nguoi dung bao 2026-09-09: dan tay ID hoac tao profile moi deu bi mat sau khi dong bo).
    Lap qua het cac trang (dung 'last_page' server tra ve) roi gop lai thanh 1 list day du."""
    all_items = []
    page = 1
    while True:
        payload = _gpm_api("GET", path, params={"page": page, "page_size": page_size}, timeout=timeout)
        if not payload.get("success"):
            raise RuntimeError(str(payload.get("message") or "GPM API loi"))
        raw = payload.get("data")
        items = raw.get("data") if isinstance(raw, dict) else (raw or [])
        all_items.extend(items or [])
        last_page = raw.get("last_page") if isinstance(raw, dict) else None
        if not items or not last_page or page >= last_page:
            break
        page += 1
    return all_items


def _gpm_create_profile_row(account_id, profile_name=None, group_name=None,
                            group_items=None, profile_items=None, batch_seen=None,
                            create_group=False):
    """Tao 1 profile GPM cho 1 dong mail - DUNG CHUNG cho nut 'Tạo profile' (1 dong) va
    'Tạo profile' hang loat (cac dong duoc tich chon). profile_name/group_name truyen vao khi
    nguoi dung dang sua tren UI (chua kip luu); None = lay gia tri dang luu trong DB cua dong.
    group_items/profile_items la danh sach nhom/profile GPM da load SAN (None = ham tu load
    khi can - dung cho goi 1 dong). batch_seen la set (name_lower, group_id) cac profile da tao
    trong CUNG dot nay (chan trung ten noi bo). create_group=True: neu ten nhom CHUA ton tai
    trong GPM thi TU DONG tao nhom moi truoc roi tao profile trong nhom do (dung sau khi
    nguoi dung da xac nhan o UI). Tra ve dict:
    {"ok": True, "account", "gpm": {id, name}, "message"} hoac
    {"ok": False, "error", "conflict"? (da co ID/trung ten), "gpm_down"? (GPM khong goi duoc)}."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return {"ok": False, "error": f"khong tim thay mail id={account_id}"}
    if (row.get("id_gpm") or "").strip():
        return {
            "ok": False, "conflict": True,
            "error": f"Dong nay da co ID GPM '{row['id_gpm']}' - xoa ID o cot ID GPM truoc khi tao profile moi.",
        }
    profile_name = (profile_name if profile_name is not None else row.get("profile") or "").strip()
    group_name = (group_name if group_name is not None else row.get("group_gpm") or "").strip()
    if group_items is None:
        try:
            group_items = _gpm_api_all_pages("/api/v1/groups")
        except Exception as e:
            return {"ok": False, "gpm_down": True, "error": f"Khong goi duoc GPM Local API ({GPM_BASE}): {e}"}
    group_id = None
    new_group_created = False
    if group_name:
        wanted = group_name.strip().lower()
        matches = [g for g in group_items if str(g.get("name") or "").strip().lower() == wanted]
        if not matches:
            if not create_group:
                known = ", ".join(str(g.get("name")) for g in group_items if g.get("name"))
                return {"ok": False,
                        "error": f"Khong tim thay nhom GPM '{group_name}'. Cac nhom dang co: {known or '(chua co nhom nao)'}."}
            # Nhom chua ton tai + nguoi dung da xac nhan tao moi -> tao nhom truoc (POST
            # /api/v1/groups/create, body {"name": ...} - xem tai lieu GPM da luu trong
            # "gpm Login/"), roi tao profile trong nhom vua tao. Them vao group_items de cac
            # dong SAU trong cung dot bulk dung lai (khong tao nhom trung).
            try:
                new_group = _gpm_api("POST", "/api/v1/groups/create", body={"name": group_name})
            except Exception as e:
                return {"ok": False, "gpm_down": True, "error": f"GPM tao nhom '{group_name}' loi: {e}"}
            if not new_group.get("success"):
                msg = str(new_group.get("message") or new_group.get("error") or "GPM tu choi tao nhom")
                return {"ok": False, "error": f"GPM tao nhom '{group_name}' that bai: {msg}"}
            gdata = new_group.get("data") or {}
            gid = str(gdata.get("id") or "").strip()
            if not gid:
                return {"ok": False, "gpm_down": True,
                        "error": f"GPM bao tao nhom thanh cong nhung khong co id nhom: {new_group}"}
            if group_items is not None:
                group_items.append({"id": gid, "name": group_name})
            group_id = gid
            new_group_created = True
        else:
            # Nhom da ton tai -> dung id nhom that (BUG cu: quen gan group_id nen profile tao ra
            # KHONG thuoc nhom nao va bo qua kiem tra trung ten - da sua 2026-09-08).
            group_id = matches[0].get("id")
    # Chong tao trung: da co profile cung ten trong CUNG nhom (hoac trung trong dot tao nay)
    # -> bao loi thay vi tao them (tranh tao trung khi bam nham/lap).
    if profile_name and group_id:
        if profile_items is None:
            try:
                profile_items = _gpm_api_all_pages("/api/v1/profiles")
            except Exception as e:
                return {"ok": False, "gpm_down": True, "error": f"Khong goi duoc GPM Local API ({GPM_BASE}): {e}"}
        same = [p for p in profile_items
                if str(p.get("group_id") or "") == str(group_id)
                and str(p.get("name") or "").strip() == profile_name]
        in_batch = batch_seen is not None and (profile_name.lower(), str(group_id)) in batch_seen
        if same or in_batch:
            existing_id = same[0].get("id") if same else None
            msg = f"GPM da co profile '{profile_name}' trong nhom '{group_name}'"
            if existing_id:
                msg += f" (id {existing_id})"
            else:
                msg += " (trung voi profile khac tao trong dot nay)"
            msg += ". Dan id vao cot ID GPM hoac doi ten o cot Profile truoc khi tao."
            return {"ok": False, "conflict": True, "error": msg}
    try:
        created = _gpm_api("POST", "/api/v1/profiles/create", body={"name": profile_name, "group_id": group_id})
    except Exception as e:
        return {"ok": False, "gpm_down": True, "error": f"GPM tao profile loi: {e}"}
    if not created.get("success"):
        msg = str(created.get("message") or created.get("error") or "GPM tu choi tao profile")
        return {"ok": False, "error": f"GPM tao profile that bai: {msg}"}
    data = created.get("data") or {}
    new_id = str(data.get("id") or "").strip()
    if not new_id:
        return {"ok": False, "gpm_down": True,
                "error": f"GPM bao tao thanh cong nhung khong co id profile: {created}"}
    account = shopee_db.update_mail_account_fields(DB_PATH, account_id, id_gpm=new_id)
    if batch_seen is not None:
        batch_seen.add((profile_name.lower(), str(group_id)))
    created_name = data.get("name") or profile_name or "(ten mac dinh)"
    if new_group_created:
        message = (f"Da tao nhom GPM '{group_name}' (id {group_id}) va profile GPM "
                   f"'{created_name}' trong nhom do (profile id {new_id}).")
    else:
        message = ("Da tao profile GPM '" + created_name + "'" +
                   (f" trong nhom '{group_name}'" if group_name else " theo mac dinh cua GPM") +
                   f" (id {new_id}).")
    return {
        "ok": True, "account": account,
        "gpm": {"id": new_id, "name": data.get("name")},
        "group_created": new_group_created,
        "group_name": group_name,
        "group_id": group_id,
        "message": message,
    }


# ============================================================================
# Engine GemLogin (GEM) - dong mail co cot 'engine' = 'gem'. Khac GPM:
#  - id profile la SO (vd 1,2), nhom la so (group_id), "khong thuoc nhom" = null/"noGroup".
#  - Tao profile chi can profile_name; gắn nhom qua group_name (nhom phai tao san trong app -
#    GEM KHONG co API tao group).
#  - Start profile tra remote_debugging_address "127.0.0.1:PORT" (khong tu chon port duoc).
#  - BAN FREE khong xoa profile qua API.
# ============================================================================


def _row_engine(row):
    """Engine cua 1 dong mail_accounts - mac dinh 'gpm' neu chua dat."""
    e = str((row or {}).get("engine") or "").strip().lower()
    return e if e in (_ad.ENGINE_GPM, _ad.ENGINE_GEM) else _ad.ENGINE_GPM


def _cdp_navigate_first_tab(port, url, timeout=8):
    """Dieu huong TAB DAU TIEN (tab 0 - tab GPM/GEM da tu mo san khi start profile, thuong
    dang trong) sang 'url' qua CDP WebSocket (Page.navigate), KHONG tao them tab moi - tranh
    tinh trang tab 0 bo trong con Shopee lai bi mo o tab 1 (yeu cau nguoi dung 2026-09-09).
    Tra ve True neu da gui navigate thanh cong, False neu khong tim duoc tab / loi WS (goi
    noi de fallback sang cach cu /json/new tao tab moi).

    CDP port "len" (TCP connect duoc) KHONG co nghia la tab dau tien da xuat hien trong
    /json/list ngay - co khoang tre nho luc browser vua khoi dong. Truoc day chi thu 1 lan
    nen hay bi race, roi vao dung fallback tao tab moi (nguoi dung bao lai 2026-09-09: tab 0
    van trong, Shopee mo o tab 1) - gio thu lai vai lan trong vai giay truoc khi bo cuoc."""
    import json as _json
    import websocket as _ws
    target = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = _requests.get(f"http://127.0.0.1:{port}/json/list", timeout=3)
            targets = r.json() if r.ok else []
        except Exception:
            targets = []
        target = next(
            (t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")), None
        )
        if target:
            break
        time.sleep(0.4)
    if not target:
        return False
    try:
        # suppress_origin=True: BAT BUOC - websocket-client tu gan header "Origin: http://<host:port>"
        # (trung CHINH host:port dang connect) neu khong noi ro, va Chrome tu ban 111+ CHAN thang
        # WS connect nao co Origin khong nam trong allowlist --remote-allow-origins (mac dinh
        # trong, tuc LA CHAN CA origin tu suy) -> 403 Forbidden. Day chinh la ly do fix truoc
        # (chi thu lai /json/list) khong het bug: tim dung tab nhung ket noi WS luon that bai,
        # roi lai fallback /json/new tao tab moi (nguoi dung bao lai 2026-09-09 lan 2). Cac cong
        # cu CDP thuc su (Puppeteer, chrome-remote-interface...) deu KHONG gui Origin de tranh
        # dung check nay - suppress_origin lam dung y do.
        ws = _ws.create_connection(target["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True)
    except Exception:
        return False
    try:
        ws.send(_json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
        ws.settimeout(timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = ws.recv()
            except Exception:
                break
            try:
                data = _json.loads(msg)
            except Exception:
                continue
            if data.get("id") == 1:
                break
        return True
    except Exception:
        return False
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _gem_open_url(profile_id, url):
    """Mo browser GEM (neu chua chay) roi mo 1 tab toi url. Raise RuntimeError khi loi;
    tra ve port CDP. Start cua GEM tu cap port nen doc tu remote_debugging_address."""
    try:
        st = _ad.start_profile(_ad.ENGINE_GEM, profile_id=profile_id)
    except _ad.AntidetectError as e:
        raise RuntimeError(str(e)) from e
    port = st.get("port")
    if not port:
        raise RuntimeError("GemLogin start khong tra duoc CDP port.")
    up = False
    for _ in range(90):  # cho toi 45s CDP len (nhu GPM)
        if _gpm_tcp_up(port):
            up = True
            break
        time.sleep(0.5)
    if not up:
        raise RuntimeError(f"Browser GemLogin start nhung CDP port {port} khong len.")
    # Uu tien dieu huong tab 0 co san (khong tao them tab) - xem _cdp_navigate_first_tab().
    if _cdp_navigate_first_tab(port, url):
        return port
    created = False
    new_tab = f"http://127.0.0.1:{port}/json/new?{url}"
    try:
        r = _requests.put(new_tab, timeout=6)
        if r.status_code in (200, 201):
            created = True
    except Exception:
        pass
    if not created:
        try:
            r = _requests.get(new_tab, timeout=6)
            if r.status_code in (200, 201):
                created = True
        except Exception:
            pass
    if not created:
        raise RuntimeError(f"Mo tab that bai tren port {port}.")
    return port


def _mail_gem_create_profile(account_id, profile_name=None, group_name=None):
    """Tao 1 profile GEM cho dong mail (engine='gem'). Tra ve dict giong _gpm_create_profile_row:
    {"ok": True, account, gpm:{id,name}, message} hoac {"ok": False, error, conflict?/gpm_down?}.
    Nhóm GEM phai ton tai san (khong tu tao); nhom rong = de GEM mac dinh."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return {"ok": False, "error": f"khong tim thay mail id={account_id}"}
    if (row.get("id_gpm") or "").strip():
        return {
            "ok": False, "conflict": True,
            "error": f"Dong nay da co ID GEM '{row['id_gpm']}' - xoa ID o cot ID GEM truoc khi tao profile moi.",
        }
    profile_name = (profile_name if profile_name is not None else row.get("profile") or "").strip()
    group_name = (group_name if group_name is not None else row.get("group_gpm") or "").strip()
    group_name_for_create = ""
    if group_name:
        try:
            groups = _ad.list_groups(_ad.ENGINE_GEM)
        except _ad.AntidetectError as e:
            return {"ok": False, "gpm_down": True, "error": str(e)}
        wanted = group_name.lower()
        match = next((g for g in groups if g["name"].lower() == wanted), None)
        if not match:
            known = ", ".join(g["name"] for g in groups) or "(chua co nhom nao - tao nhom trong app GemLogin)"
            return {
                "ok": False,
                "error": f"Khong tim thay nhom GemLogin '{group_name}'. Cac nhom dang co: {known}.",
            }
        group_name_for_create = match["name"]
    try:
        pid, data = _ad.create_profile(_ad.ENGINE_GEM, profile_name=profile_name,
                                       group_name=group_name_for_create)
    except _ad.AntidetectError as e:
        gpm_down = "Khong goi duoc" in str(e) or "GemLogin" not in str(e)
        return {"ok": False, "gpm_down": bool(gpm_down), "error": str(e)}
    account = shopee_db.update_mail_account_fields(DB_PATH, account_id, id_gpm=pid)
    created_name = data.get("name") or profile_name or "(ten mac dinh)"
    message = ("Da tao profile GEM '" + str(created_name) + "'" +
               (f" trong nhom '{group_name_for_create}'" if group_name_for_create else " (khong thuoc nhom)") +
               f" (id {pid}).")
    return {
        "ok": True, "account": account,
        "gpm": {"id": pid, "name": data.get("name")},
        "group_created": False,
        "group_name": group_name_for_create,
        "message": message,
    }


def _mail_create_one(row, profile_override=None, group_override=None, gpm_create_group=False):
    """Tao profile theo engine CUA DONG. Tra ve dict ket qua chuan (ok/error/...)."""
    if _row_engine(row) == _ad.ENGINE_GEM:
        return _mail_gem_create_profile(
            row["id"], profile_name=profile_override, group_name=group_override,
        )
    return _gpm_create_profile_row(
        row["id"], profile_name=profile_override, group_name=group_override,
        create_group=bool(gpm_create_group),
    )


def _mail_result_to_http(res):
    """Convert dict ket qua (single) -> (jsonify, status)."""
    if res.get("ok"):
        return jsonify({"ok": True, "account": res["account"], "gpm": res["gpm"], "message": res["message"]})
    if res.get("gpm_down"):
        return jsonify({"ok": False, "error": res["error"]}), 502
    return jsonify({"ok": False, "error": res["error"]}), (409 if res.get("conflict") else 400)


@app.route("/api/mail_accounts/<int:account_id>/gpm/create", methods=["POST"])
def mail_accounts_gpm_create(account_id):
    """Nut 'Tạo profile' tren 1 dong mail - chay theo engine cua DONG (cot 'engine' = gpm/gem).
    GPM: co the tu tao nhom moi khi thieu (body {"create_group": true}). GEM: nhom phai tao
    san trong app (khong tu tao)."""
    body = request.get_json(force=True, silent=True) or {}
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    res = _mail_create_one(row, gpm_create_group=bool(body.get("create_group")))
    return _mail_result_to_http(res)


@app.route("/api/mail_accounts/gpm/create_bulk", methods=["POST"])
def mail_accounts_gpm_create_bulk():
    """Nut 'Tạo profile' hang loat cho cac dong dang duoc TICH CHON: tao 1 profile theo engine
    CUA TUNG DONG (cot 'engine' - gpm/gem tron lan duoc). GPM: tao nhom moi khi thieu neu
    body co {"create_missing_groups": true}. GEM: nhom phai ton tai san (khong tu tao)."""
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("rows")
    if not isinstance(rows, list) or not rows:
        return _bad_request("thieu 'rows' (danh sach {id, profile?, group_gpm?})")
    create_missing_groups = bool(body.get("create_missing_groups"))
    results = []
    for it in rows:
        if not isinstance(it, dict):
            results.append({"id": None, "ok": False, "error": "Dong khong hop le."})
            continue
        try:
            aid = int(str(it.get("id") or "").strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "Thieu hoac sai 'id' trong dong."})
            continue
        row = shopee_db.get_mail_account(DB_PATH, aid)
        if not row:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        res = _mail_create_one(row, it.get("profile"), it.get("group_gpm"),
                               gpm_create_group=create_missing_groups)
        results.append({
            "id": aid, "ok": res.get("ok"),
            "error": res.get("error"), "gpm": res.get("gpm"), "message": res.get("message"),
            "group_created": res.get("group_created"), "group_name": res.get("group_name"),
        })
    created = sum(1 for r in results if r["ok"])
    return jsonify({"ok": True, "created": created, "skipped": len(results) - created, "results": results})


@app.route("/api/mail_accounts/<int:account_id>/gpm/open", methods=["POST"])
def mail_accounts_gpm_open(account_id):
    """Nut 'Mở profile' tren 1 dong mail: mo browser cua profile (cot ID GPM/GEM) roi mo 1 tab
    toi trang CHU Shopee cua 'Thị trường' dang chon tren chinh dong mail do - chay theo engine
    cua dong (gpm: start qua GPM; gem: start qua GemLogin roi dung CDP port GemLogin cap)."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    profile_id = (row.get("id_gpm") or "").strip()
    if not profile_id:
        return jsonify({
            "ok": False,
            "error": "Dong nay chua co ID GPM/GEM - bam 'Tạo profile' (hoac dan ID vao cot ID GPM) truoc.",
        }), 400
    url, code = _gpm_market_home(row.get("market"))
    try:
        if _row_engine(row) == _ad.ENGINE_GEM:
            port = _gem_open_url(profile_id, url)
        else:
            port = _gpm_open_url(profile_id, url)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify({
        "ok": True, "url": url, "market_code": code, "port": port,
        "message": f"Da mo {url}",
    })


@app.route("/api/mail_accounts/<int:account_id>/check_cookie", methods=["POST"])
def mail_accounts_check_cookie(account_id):
    """Kiem tra cookie da luu cua dong nay CON SONG hay khong - KHONG mo browser (xem
    _check_shopee_cookie_alive()). Dung boi nut 'Get Cookie' de QUYET DINH co can lay lai hay
    khong (cookie con song -> bo qua, khong lay lai - xem yeu cau nguoi dung 2026-09-11), hoac
    goi rieng de kiem tra nhanh. LUON GHI ket qua vao cot 'cookie_status' (ke ca khi goi ngam
    tu nut "Get Cookie") de UI hien dung trang thai LIVE/DIE tren cot Cookie (xem yeu cau
    nguoi dung 2026-09-11 "cột COOKIE chỉ hiện trạng thái"). status: 'no_cookie' (chua co
    cookie) / 'alive' / 'dead'."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    cookie_str = (row.get("cookie") or "").strip()
    if not cookie_str:
        return jsonify({"ok": True, "status": "no_cookie", "detail": "Chua co cookie."})
    alive, detail = _check_shopee_cookie_alive(cookie_str, row.get("market"))
    shopee_db.set_mail_account_cookie_status(DB_PATH, account_id, "alive" if alive else "dead")
    return jsonify({"ok": True, "status": "alive" if alive else "dead", "detail": detail})


@app.route("/api/mail_accounts/gpm/open_bulk", methods=["POST"])
def mail_accounts_gpm_open_bulk():
    """Nut 'Mở profile' hang loat cho cac dong dang duoc TICH CHON: mo browser theo engine cua
    TUNG dong (dong CO ID GPM/GEM, dong chua co ID se bo qua va dem vao skipped)."""
    body = request.get_json(force=True, silent=True) or {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return _bad_request("thieu 'ids'")
    results = []
    opened = 0
    for raw in ids:
        try:
            aid = int(str(raw).strip())
        except (TypeError, ValueError):
            results.append({"id": None, "ok": False, "error": "id khong hop le."})
            continue
        row = shopee_db.get_mail_account(DB_PATH, aid)
        if not row:
            results.append({"id": aid, "ok": False, "error": f"khong tim thay mail id={aid}"})
            continue
        profile_id = (row.get("id_gpm") or "").strip()
        if not profile_id:
            results.append({"id": aid, "ok": False, "error": "Chua co ID GPM/GEM (bo qua)."})
            continue
        url, code = _gpm_market_home(row.get("market"))
        try:
            if _row_engine(row) == _ad.ENGINE_GEM:
                port = _gem_open_url(profile_id, url)
            else:
                port = _gpm_open_url(profile_id, url)
        except RuntimeError as e:
            results.append({"id": aid, "ok": False, "error": str(e)})
            continue
        opened += 1
        results.append({"id": aid, "ok": True, "url": url, "market_code": code, "port": port})
    return jsonify({"ok": True, "opened": opened, "skipped": len(results) - opened, "results": results})


def _shopee_profile_base(market):
    """URL trang ho so buyer https://shopee.<tld>/user/account/profile theo market cua dong.
    KHONG vao thang seller.shopee (nguoi dung chua dang ky seller) - chi mo trang buyer, con
    response seller.shopee (mini/login) do trang nay kich hoat se duoc hook bat - xem cdp_get_shopee_id.mjs."""
    code = _GPM_MARKET_CODE.get(str(market or "").strip().upper(), "ph")
    return _GPM_HOME_URL.get(code, _GPM_HOME_URL["ph"]).rstrip("/") + "/user/account/profile"


@app.route("/api/mail_accounts/<int:account_id>/get_shopee_id", methods=["POST"])
def mail_accounts_get_shopee_id(account_id):
    """Nut 'Get Shopee ID' (hang loat): mo browser cua dong (engine GPM/GEM), mo trang
    buyer https://shopee.<tld>/user/account/profile va BAT RESPONSE cua request den
    seller.shopee (webchat .../mini/login) de lay user.name lam Shopee ID (thay XPath da loi).
    Tra ve theo trang thai:
      status ok        -> da co shopee_id (chua ghi neu trung -> status duplicate + duplicates)
      no_login/captcha -> de cua so do lai cho nguoi dung xu ly (khong ghi)
      timeout/error    -> loi/qua han (khong ghi)
    Ghi vao cot shopee_id CHI khi khong trung dong khac (exclude_id=chinh dong)."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    profile_id = (row.get("id_gpm") or "").strip()
    if not profile_id:
        return jsonify({"ok": True, "status": "no_id", "detail": "Chua co ID GPM/GEM (bo qua)."})
    engine = _row_engine(row)
    profile_url = _shopee_profile_base(row.get("market"))
    node_exe = _find_node()
    cmd = [node_exe, os.path.join(SCRIPTS_DIR, "cdp_get_shopee_id.mjs"),
           "--engine", engine, "--profile", profile_id, "--url", profile_url,
           "--gpm-base", GPM_BASE, "--gem-base", GEM_BASE]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=110)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": True, "status": "error", "detail": "Chay qua 110s (timeout).",
                        "engine": engine, "url": profile_url})
    out_text = (proc.stdout or "").strip()
    last_line = out_text.splitlines()[-1] if out_text else ""
    import json as _json
    try:
        res = _json.loads(last_line)
    except Exception:
        stderr_tail = (proc.stderr or "").strip().splitlines()
        tail = (stderr_tail[-1] if stderr_tail else "") or (proc.stdout or "")[:200]
        return jsonify({"ok": True, "status": "error", "detail": f"Helper loi: {tail[:240]}",
                        "engine": engine, "url": profile_url})
    status = str(res.get("status") or "error")
    sid = str(res.get("shopee_id") or "").strip()
    if status == "ok" and sid:
        dups = shopee_db.find_mail_accounts_by_shopee_id(DB_PATH, sid, exclude_id=account_id)
        if dups:
            return jsonify({"ok": True, "status": "duplicate", "shopee_id": sid,
                            "duplicates": [d["email"] for d in dups],
                            "detail": f"Shopee ID '{sid}' da duoc dung o dong khac.", "url": profile_url})
        shopee_db.update_mail_account_fields(DB_PATH, account_id, shopee_id=sid)
        return jsonify({"ok": True, "status": "ok", "written": True, "shopee_id": sid,
                        "detail": f"Da ghi Shopee ID '{sid}'.", "url": profile_url})
    return jsonify({"ok": True, "status": status,
                    "shopee_id": sid if sid else None,
                    "detail": res.get("detail") or "", "url": profile_url,
                    "user_handle": status in ("no_login", "captcha")})


@app.route("/api/mail_accounts/<int:account_id>/get_cookie", methods=["POST"])
def mail_accounts_get_cookie(account_id):
    """Nut 'Get Cookie' (hang loat): mo browser cua dong (engine GPM/GEM), mo trang chu
    Shopee theo market va lay TOAN BO cookie dang nhap qua CDP Network.getCookies (ke ca
    HttpOnly/Secure - xem cdp_get_cookie.mjs). Tra ve theo trang thai:
      status ok                        -> da co cookie, ghi vao cot cookie
      no_login/captcha/timeout/error   -> LAY THAT BAI: ghi de cot cookie = 'FAIL' (KE CA
                                           khi dong da co cookie CU tu truoc - xem yeu cau
                                           nguoi dung 2026-09-11 "dòng cookie hiện lên đảm
                                           bảo luôn sống": tranh de lai cookie cu CO THE da
                                           chet ma nhin qua tuong nhu van con dung duoc)."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    profile_id = (row.get("id_gpm") or "").strip()
    if not profile_id:
        return jsonify({"ok": True, "status": "no_id", "detail": "Chua co ID GPM/GEM (bo qua)."})
    engine = _row_engine(row)
    home_url, _code = _gpm_market_home(row.get("market"))
    node_exe = _find_node()
    cmd = [node_exe, os.path.join(SCRIPTS_DIR, "cdp_get_cookie.mjs"),
           "--engine", engine, "--profile", profile_id, "--url", home_url,
           "--gpm-base", GPM_BASE, "--gem-base", GEM_BASE]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=110)
    except subprocess.TimeoutExpired:
        shopee_db.update_mail_account_fields(DB_PATH, account_id, cookie="FAIL")
        shopee_db.set_mail_account_cookie_status(DB_PATH, account_id, None)
        return jsonify({"ok": True, "status": "error", "detail": "Chay qua 110s (timeout).",
                        "engine": engine, "url": home_url, "cookie": "FAIL"})
    out_text = (proc.stdout or "").strip()
    last_line = out_text.splitlines()[-1] if out_text else ""
    import json as _json
    try:
        res = _json.loads(last_line)
    except Exception:
        stderr_tail = (proc.stderr or "").strip().splitlines()
        tail = (stderr_tail[-1] if stderr_tail else "") or (proc.stdout or "")[:200]
        shopee_db.update_mail_account_fields(DB_PATH, account_id, cookie="FAIL")
        shopee_db.set_mail_account_cookie_status(DB_PATH, account_id, None)
        return jsonify({"ok": True, "status": "error", "detail": f"Helper loi: {tail[:240]}",
                        "engine": engine, "url": home_url, "cookie": "FAIL"})
    status = str(res.get("status") or "error")
    cookie_val = str(res.get("cookie") or "").strip()
    if status == "ok" and cookie_val:
        shopee_db.update_mail_account_fields(DB_PATH, account_id, cookie=cookie_val)
        # Vua lay duoc cookie MOI thanh cong = coi nhu vua xac nhan con song - ghi luon
        # cookie_status='alive' de cot Cookie hien LIVE ngay, khong can goi check_cookie rieng
        # (xem yeu cau nguoi dung 2026-09-11).
        shopee_db.set_mail_account_cookie_status(DB_PATH, account_id, "alive")
        return jsonify({"ok": True, "status": "ok", "written": True, "cookie": cookie_val,
                        "detail": res.get("detail") or "Da ghi cookie.", "url": home_url})
    # Lay that bai (no_login/captcha/timeout/error, hoac status 'ok' nhung thieu cookie_val) -
    # GHI DE cot cookie = 'FAIL', KE CA dong da co cookie CU tu truoc, de dam bao cot Cookie
    # hien tren UI luon la gia tri MOI KIEM CHUNG, khong bao gio la cookie cu co the da chet
    # (xem yeu cau nguoi dung 2026-09-11).
    shopee_db.update_mail_account_fields(DB_PATH, account_id, cookie="FAIL")
    shopee_db.set_mail_account_cookie_status(DB_PATH, account_id, None)
    return jsonify({"ok": True, "status": status, "cookie": "FAIL",
                    "detail": res.get("detail") or "", "url": home_url,
                    "user_handle": status in ("no_login", "captcha")})


# Trang thai "dang lam gi" MOI NHAT cua tung dong dang chay nut 'Login Shopee' - cho popup
# tien trinh o frontend POLL de hien chi tiet tung buoc (xem yeu cau nguoi dung 2026-09-11:
# "muon chi tiet hon tung tai khoan"), KHONG chi biet ket qua cuoi cung. Key = account_id,
# value = {"step", "detail", "ts"}. Ghi de MOI LAN co buoc moi (khong can lich su), doc qua
# GET /api/mail_accounts/<id>/login_shopee/progress.
_LOGIN_PROGRESS = {}
_LOGIN_PROGRESS_LOCK = threading.Lock()


def _set_login_progress(account_id, step, detail=""):
    with _LOGIN_PROGRESS_LOCK:
        _LOGIN_PROGRESS[account_id] = {"step": step, "detail": detail, "ts": time.time()}


def _persist_login_cookie(account_id, res):
    """Neu cdp_login_shopee.mjs da tra ve cookie (chi khi status 'ok' - xem
    extractCookieString() trong file do, lay NGAY trong cung phien CDP vua dang nhap thanh
    cong, khong can mo browser rieng qua nut 'Get Cookie' nua), luu lai NGAY vao cot 'cookie'
    + danh dau cookie_status='alive' (vua xac nhan song ngay luc nay) - xem yeu cau nguoi dung
    2026-09-11 "lấy luôn cookie nếu đã login thành công, tài khoản nào fail thì bỏ qua". Tai
    khoan fail (status khac 'ok') khong bao gio duoc goi ham nay (xem 2 noi goi trong
    mail_accounts_login_shopee) nen tu dong "bo qua" dung y nguoi dung, khong can check rieng
    o day. Neu vi ly do hiem gap ma dang nhap OK nhung khong lay duoc cookie (res['cookie'] la
    None/rong), KHONG ghi de gi ca - giu nguyen cookie cu (co the la 'FAIL' tu truoc) thay vi
    xoa mat du lieu cu vi 1 lan lay cookie tinh co that bai."""
    cookie_val = (res.get("cookie") or "").strip()
    if cookie_val:
        shopee_db.update_mail_account_fields(DB_PATH, account_id, cookie=cookie_val)
        shopee_db.set_mail_account_cookie_status(DB_PATH, account_id, "alive")


@app.route("/api/mail_accounts/<int:account_id>/login_shopee/progress", methods=["GET"])
def mail_accounts_login_shopee_progress(account_id):
    """Frontend POLL endpoint nay (moi 1-2s) trong luc cho ket qua POST .../login_shopee de
    hien buoc hien tai (vd 'Đang điền form...', 'Đang chờ email xác thực...') - xem
    _LOGIN_PROGRESS. step=None neu chua co gi (chua bat dau/da xong tu lau)."""
    with _LOGIN_PROGRESS_LOCK:
        p = _LOGIN_PROGRESS.get(account_id)
    if not p:
        return jsonify({"ok": True, "step": None, "detail": "", "ts": None})
    return jsonify({"ok": True, "step": p["step"], "detail": p["detail"], "ts": p["ts"]})


def _run_login_node(extra_args, timeout, account_id):
    """Chay cdp_login_shopee.mjs qua Popen (KHONG dung subprocess.run) de doc duoc stdout
    THEO THOI GIAN THUC tung dong mot - script in ra nhieu dong JSON tien trinh trong luc chay
    (dang {"progress": true, "step", "detail"} - xem progress() trong cdp_login_shopee.mjs) roi
    1 dong JSON KET QUA CUOI CUNG (khong co key 'progress'). Moi dong tien trinh doc duoc se
    ghi ngay vao _LOGIN_PROGRESS cho frontend poll thay ngay lap tuc, khong phai doi ca request
    xong moi biet dang lam gi (xem yeu cau nguoi dung 2026-09-11).
    Dung 1 thread doc rieng + queue.Queue de co the ap dung timeout tong the mot cach an toan
    tren Windows (subprocess pipe KHONG ho tro select() nhu socket tren Windows, nen khong the
    dat timeout truc tiep tren proc.stdout.readline())."""
    node_exe = _find_node()
    cmd = [node_exe, os.path.join(SCRIPTS_DIR, "cdp_login_shopee.mjs")] + extra_args
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return {"status": "error", "detail": f"Khong chay duoc node: {e}"}

    line_queue = queue.Queue()

    def _reader():
        try:
            for line in proc.stdout:
                line_queue.put(line)
        except Exception:
            pass
        line_queue.put(None)  # bao hieu stdout da dong (process da ket thuc hoac loi)

    threading.Thread(target=_reader, daemon=True).start()

    last_result = None
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            proc.kill()
            return {"status": "error", "detail": f"Chay qua {timeout}s (timeout)."}
        try:
            line = line_queue.get(timeout=remaining)
        except queue.Empty:
            proc.kill()
            return {"status": "error", "detail": f"Chay qua {timeout}s (timeout)."}
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("progress"):
            _set_login_progress(account_id, obj.get("step") or "", obj.get("detail") or "")
            continue
        last_result = obj

    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    if last_result is not None:
        return last_result
    stderr_tail = ""
    try:
        err_lines = (proc.stderr.read() or "").strip().splitlines()
        stderr_tail = err_lines[-1] if err_lines else ""
    except Exception:
        pass
    return {"status": "error", "detail": f"Helper loi: {stderr_tail[:240]}"}


@app.route("/api/mail_accounts/<int:account_id>/login_shopee", methods=["POST"])
def mail_accounts_login_shopee(account_id):
    """Nut 'Login Shopee' (hang loat): tu dong dang nhap Shopee cho 1 dong bang loginKey=Email
    (mail account) + password=Shopee Password, dung theo dung luong mo ta trong login_plan.txt.
    Chay qua cdp_login_shopee.mjs theo 2 buoc:
      1) --step login: mo trang profile (se bi dieu huong sang trang dang nhap neu chua login),
         dong popup chon ngon ngu (neu co), dien loginKey/password, bam "Log In", poll ket qua.
         Neu Shopee bat xac thuc qua link email ("Verify by Email Link") thi script da BAM nut
         do va tra ve port+tab_id de buoc 2 xu ly tiep (KHONG tu doc mail - Node khong co
         creds Microsoft Graph).
      2) O day (Python): doc mail lay link kich hoat qua microsoft_mail_client.fetch_login_link
         (cung ham dung boi nut '⟳ Kich hoat' hien co) - co retry vi email co the den tre vai
         giay sau khi bam nut. Sau do goi --step activate: mo link o TAB MOI (khong dieu huong
         tab dang cho), doi duoc duyet ("Sign-in attempt has been approved.") roi quay lai tab
         dang dang nhap (Page.bringToFront) va poll tiep ket qua dang nhap cuoi cung.
    Trong suot qua trinh, ghi tung buoc vao _LOGIN_PROGRESS (xem _run_login_node/
    _set_login_progress) de frontend poll hien chi tiet theo thoi gian thuc (yeu cau nguoi dung
    2026-09-11), khong chi bao ket qua cuoi.
    Tra ve theo trang thai (giong cac nut Get ID/Get Cookie khac):
      ok                  -> da dang nhap Shopee thanh cong
      invalid_credentials -> sai Email/Shopee Password
      captcha              -> bi chan captcha/traffic (de cua so do lai xu ly)
      no_email_link        -> Shopee yeu cau xac thuc qua mail nhung khong doc duoc link trong mail
      no_credentials        -> dong chua co Email va/hoac Shopee Password
      timeout/error         -> qua han/loi khac."""
    row = shopee_db.get_mail_account(DB_PATH, account_id)
    if not row:
        return _bad_request(f"khong tim thay mail id={account_id}")
    profile_id = (row.get("id_gpm") or "").strip()
    if not profile_id:
        return jsonify({"ok": True, "status": "no_id", "detail": "Chua co ID GPM/GEM (bo qua)."})
    login_key = (row.get("email") or "").strip()
    password = (row.get("shopee_password") or "").strip()
    if not login_key or not password:
        return jsonify({"ok": True, "status": "no_credentials",
                        "detail": "Chua co Email va/hoac Shopee Password de dang nhap (bo qua)."})
    engine = _row_engine(row)
    profile_url = _shopee_profile_base(row.get("market"))

    # Timeout tung buoc (giay) - nguoi dung tu dat trong popup "Login Shopee" (moi tai khoan
    # dung 1 proxy toc do mang khac nhau, xem yeu cau nguoi dung 2026-09-11). Clamp 20-600s de
    # tranh gia tri qua nho (fail oan) hoac qua lon (treo qua lau).
    body = request.get_json(force=True, silent=True) or {}
    try:
        timeout_sec = int(body.get("timeout_sec") or 90)
    except (TypeError, ValueError):
        timeout_sec = 90
    timeout_sec = max(20, min(600, timeout_sec))
    node_timeout_ms = str(timeout_sec * 1000)
    subprocess_timeout = timeout_sec + 50  # +50s de xu ly startProfile/CDP connect/overhead

    _set_login_progress(account_id, "Đang mở trình duyệt profile...")
    res = _run_login_node([
        "--step", "login", "--engine", engine, "--profile", profile_id, "--url", profile_url,
        "--login-key", login_key, "--password", password,
        "--gpm-base", GPM_BASE, "--gem-base", GEM_BASE, "--timeout", node_timeout_ms,
    ], timeout=subprocess_timeout, account_id=account_id)

    status = str(res.get("status") or "error")
    if status == "ok":
        _set_login_progress(account_id, "Đăng nhập thành công.")
        shopee_db.set_mail_account_login_status(DB_PATH, account_id, "ok")
        _persist_login_cookie(account_id, res)
        return jsonify({"ok": True, "status": "ok", "detail": res.get("detail") or "Da dang nhap.",
                        "url": profile_url, "cookie_saved": bool((res.get("cookie") or "").strip())})
    if status != "verify_email_link":
        _set_login_progress(account_id, "Đã xong.", res.get("detail") or "")
        shopee_db.set_mail_account_login_status(DB_PATH, account_id, status)
        return jsonify({"ok": True, "status": status, "detail": res.get("detail") or "",
                        "url": profile_url, "user_handle": status in ("captcha",)})

    # Shopee bat xac thuc qua link email - doc mail (co retry, email co the den tre vai giay).
    port = res.get("port")
    tab_id = res.get("tab_id")
    link = None
    fetch_note = ""
    for attempt in range(6):  # ~30s cho phep email den tre
        _set_login_progress(account_id, f"Đang đọc email lấy link kích hoạt (lần {attempt + 1}/6)...")
        time.sleep(5)
        try:
            link, fetch_note, new_refresh_token = microsoft_mail_client.fetch_login_link(
                row["refresh_token"], row["client_id"])
            if new_refresh_token and new_refresh_token != row["refresh_token"]:
                shopee_db.update_mail_account_refresh_token(DB_PATH, account_id, new_refresh_token)
        except microsoft_mail_client.MicrosoftMailError as e:
            _set_login_progress(account_id, "Đã xong.", f"Loi doc mail: {e}")
            shopee_db.set_mail_account_login_status(DB_PATH, account_id, "error")
            return jsonify({"ok": True, "status": "error", "detail": f"Loi doc mail: {e}",
                            "url": profile_url})
        if link:
            break
    if not link or not port or not tab_id:
        detail = f"Khong tim thay link kich hoat trong mail sau nhieu lan thu. {fetch_note}"
        _set_login_progress(account_id, "Đã xong.", detail)
        shopee_db.set_mail_account_login_status(DB_PATH, account_id, "no_email_link")
        return jsonify({"ok": True, "status": "no_email_link", "detail": detail, "url": profile_url})

    _set_login_progress(account_id, "Đã tìm thấy link kích hoạt, đang mở để xác thực...")
    res2 = _run_login_node([
        "--step", "activate", "--port", str(port), "--tab0-id", str(tab_id), "--link", link,
        "--timeout", node_timeout_ms,
    ], timeout=subprocess_timeout, account_id=account_id)
    status2 = str(res2.get("status") or "error")
    detail2 = res2.get("detail") or ""
    if res2.get("approved") is False:
        detail2 = (detail2 + " (Chua thay xac nhan duyet o tab kich hoat)").strip()
    _set_login_progress(account_id, "Đã xong.", detail2)
    shopee_db.set_mail_account_login_status(DB_PATH, account_id, status2)
    if status2 == "ok":
        _persist_login_cookie(account_id, res2)
    return jsonify({"ok": True, "status": status2, "detail": detail2, "url": profile_url,
                    "user_handle": status2 in ("captcha",),
                    "cookie_saved": status2 == "ok" and bool((res2.get("cookie") or "").strip())})


@app.route("/api/mail_accounts/gpm/sync", methods=["POST"])
def mail_accounts_gpm_sync():
    """Dong bo danh sach mail_accounts theo engine CUA TUNG DONG (GPM va GEM lam NGUON):
    - Dong nao co ID GPM/GEM: cap nhat lai cot Profile (ten profile that) + GROUP GPM + Proxy
      (field 'raw_proxy' cua profile) theo engine do, KE CA khi nguoi dung da doi ten/doi
      nhom/doi proxy ngay ben GPM/GemLogin. Profile khong thuoc nhom nao hien nhan
      khong-nhom theo engine (GPM: "Default group", GEM: "All").
    - ID khong con ton tai trong GPM/GEM (profile da bi xoa) -> TU XOA ID (dong ve trang thai
      chua tao, van GIU ten Profile/GROUP GPM/Proxy da nhap de tao lai neu can).
    Load nhom + profile cua TUNG engine 1 lan. Dong chua co ID khong dong cham toi."""
    accounts = shopee_db.list_mail_accounts(DB_PATH, limit=1000000)
    by_engine = {_ad.ENGINE_GPM: [], _ad.ENGINE_GEM: []}
    for a in accounts:
        if str(a.get("id_gpm") or "").strip():
            by_engine[_row_engine(a)].append(a)

    def _sync_engine_gpm(rows):
        """GPM: group_id uuid / name tu /api/v1/groups; ungrouped hien 'Default group'.
        Dung _gpm_api_all_pages (KHONG phai goi 1 lan la xong) - GPM phan trang mac dinh 30
        item/lan, goi thieu page_size se bo sot profile/nhom khien dong hop le bi hieu nham
        'khong con ton tai' roi bi TU XOA id_gpm (bug nguoi dung bao 2026-09-09)."""
        try:
            groups = _gpm_api_all_pages("/api/v1/groups")
            profiles = _gpm_api_all_pages("/api/v1/profiles")
        except Exception as e:
            raise RuntimeError(f"Khong goi duoc GPM Local API ({GPM_BASE}): {e}")
        group_name_by_id = {str(g.get("id")): str(g.get("name") or "") for g in groups}
        prof_by_id = {str(p.get("id")): p for p in profiles if str(p.get("id") or "").strip()}
        checked = updated = cleared = 0
        for a in rows:
            checked += 1
            pid = str(a["id_gpm"]).strip()
            prof = prof_by_id.get(pid)
            if prof is None:
                shopee_db.update_mail_account_fields(DB_PATH, a["id"], id_gpm="")
                cleared += 1
                continue
            gid = str(prof.get("group_id") or "").strip()
            group_name = group_name_by_id.get(gid, "") if gid else _ad.ungrouped_label(_ad.ENGINE_GPM)
            real_profile = str(prof.get("name") or "").strip()
            real_proxy = str(prof.get("raw_proxy") or "").strip()
            if ((a.get("profile") or "") != real_profile or (a.get("group_gpm") or "") != group_name
                    or (a.get("proxy") or "") != real_proxy):
                shopee_db.update_mail_account_fields(DB_PATH, a["id"], profile=real_profile, group_gpm=group_name, proxy=real_proxy)
                updated += 1
        return checked, updated, cleared

    def _sync_engine_gem(rows):
        """GEM: group_id so / ten tu /api/groups; 'noGroup'/null = chua xep nhom -> hien 'All'."""
        try:
            groups = _ad.list_groups(_ad.ENGINE_GEM)
            profiles = _ad.list_profiles(_ad.ENGINE_GEM)
        except _ad.AntidetectError as e:
            raise RuntimeError(str(e))
        group_name_by_id = {g["id"]: g["name"] for g in groups}
        prof_by_id = {p["id"]: p for p in profiles}
        checked = updated = cleared = 0
        for a in rows:
            checked += 1
            pid = str(a["id_gpm"]).strip()
            prof = prof_by_id.get(pid)
            if prof is None:
                shopee_db.update_mail_account_fields(DB_PATH, a["id"], id_gpm="")
                cleared += 1
                continue
            gid = prof.get("group_id") or ""
            group_name = group_name_by_id.get(gid, "") if gid else _ad.ungrouped_label(_ad.ENGINE_GEM)
            real_profile = prof.get("name") or ""
            real_proxy = prof.get("raw_proxy") or ""
            if ((a.get("profile") or "") != real_profile or (a.get("group_gpm") or "") != group_name
                    or (a.get("proxy") or "") != real_proxy):
                shopee_db.update_mail_account_fields(DB_PATH, a["id"], profile=real_profile, group_gpm=group_name, proxy=real_proxy)
                updated += 1
        return checked, updated, cleared

    checked = updated = cleared = 0
    errors = []
    for engine in (_ad.ENGINE_GPM, _ad.ENGINE_GEM):
        rows = by_engine[engine]
        if not rows:
            continue
        try:
            if engine == _ad.ENGINE_GPM:
                c, u, cl = _sync_engine_gpm(rows)
            else:
                c, u, cl = _sync_engine_gem(rows)
        except RuntimeError as e:
            errors.append(f"{engine.upper()}: {e}")
            continue
        checked += c
        updated += u
        cleared += cl
    if errors and checked == 0:
        return jsonify({"ok": False, "error": " | ".join(errors)}), 502
    note = (" | ".join(errors)) if errors else "Khong co loi."
    return jsonify({
        "ok": True, "checked": checked, "updated": updated, "cleared": cleared,
        "message": (f"Da kiem tra {checked} dong co ID GPM/GEM: cap nhat {updated} dong, "
                    f"xoa {cleared} ID khong con ton tai. {note}"),
    })


@app.route("/api/engines/groups", methods=["GET"])
def engines_groups():
    """Danh sach nhom cua 1 engine (?engine=gpm|gem) - de UI goi y nhom dung engine cua dong."""
    engine = request.args.get("engine") or _ad.ENGINE_GPM
    try:
        engine = _ad.normalize_engine(engine)
        groups = _ad.list_groups(engine)
    except _ad.AntidetectError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify({"ok": True, "engine": engine, "groups": groups})


@app.route("/api/engines/profiles", methods=["GET"])
def engines_profiles():
    """Danh sach profile cua 1 engine (?engine=&group_id=) - chuan hoa id/name/group + kem trang
    thai worker/CDP ma server dang theo doi (dung cho Tab Worker chon profile de chay)."""
    engine = request.args.get("engine") or _ad.ENGINE_GPM
    group_id = (request.args.get("group_id") or "").strip() or None
    try:
        engine = _ad.normalize_engine(engine)
        profiles = _ad.list_profiles(engine, group_id=group_id)
    except _ad.AntidetectError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    out = []
    for p in profiles:
        pid = p["id"]
        st = _gpm_worker_status(pid)
        # Chi nhan status worker cung engine (id so cua GEM co the trung format? Khong, uuid vs so)
        if st is not None and st.get("engine") not in (None, engine):
            st = None
        port = _gpm_ports.get(pid)
        out.append(dict(p, port=port, cdp_up=_gpm_tcp_up(port) if port else False, worker=st))
    return jsonify({"ok": True, "engine": engine, "profiles": out})


@app.route("/api/reset", methods=["POST"])
def reset_all():
    """Xoa du lieu san pham (khong dong tai khoan/profile) - dung cho nut "Xoa toan bo du
    lieu" tren UI. market optional trong body: '' hoac thieu = xoa TAT CA (hanh vi cu), 1
    ma market cu the = CHI xoa dong cua thi truong do."""
    body = request.get_json(force=True, silent=True) or {}
    market = body.get("market") or None
    deleted = shopee_db.clear_all_items(DB_PATH, market=market)
    return jsonify({"ok": True, "deleted": deleted, "market": market})


@app.route("/api/stats", methods=["GET"])
def stats():
    video_stats = shopee_db.count_video_push_stats(DB_PATH)
    return jsonify({
        "root": {
            "pending": shopee_db.count_status(DB_PATH, link_type="root", status_link="pending"),
            "done": shopee_db.count_status(DB_PATH, link_type="root", status_link="done"),
            "fail": shopee_db.count_status(DB_PATH, link_type="root", status_link="fail"),
        },
        "related": {
            "pending": shopee_db.count_status(DB_PATH, link_type="related", status_link="pending"),
            "member": shopee_db.count_status(DB_PATH, link_type="related", status_link="member"),
            "cached": shopee_db.count_status(DB_PATH, link_type="related", status_link="cached"),
        },
        # "Root đủ điều kiện tạo video": root co merged_link (nhom da du) - KHONG giam sau khi
        # da tao video (job_id/cache_uploaded tinh rieng ben tab Tạo Video), xem count_video_push_stats().
        "root_video_eligible": video_stats.get("eligible") or 0,
        "total_items": shopee_db.count_items(DB_PATH),
    })


# ============================================================================
# Tab "Cào root AFF" - cao san pham root theo TU KHOA qua trang
# affiliate.shopee.*/offer/product_offer (xem Cao_root_aff.txt). Worker la
# cdp_keyword_worker.mjs (spawn tu tab Vận hành GPM, mode 'keyword'): claim tu khoa
# pending, dieu khien CHINH TRANG affiliate search theo tu khoa do (de trang tu goi
# /api/v3/offer/product/list voi token chong bot hop le), hook Network chup tung trang
# roi day ve /api/keywords/page_done. Server loc tieu chi theo CAU HINH CAO worker gui
# kem moi trang: sold_min + comm_money_min = so TIEN hoa hong uoc tinh toi thieu
# (seller_commission_rate% * gia hien thi - "seller_com quy doi thanh tien") + filter_types
# (danh dau Xtra). CAC GIA TRI NAY CHI AP DUNG KHI CAO (nhap o tab Vận hành GPM luc start
# worker), KHONG LUU khi import tu khoa. Item dat tieu chi insert root pending vao bang
# 'products' chung (link_type 'root', groupid=itemid) - link da ton tai o bat ky dau trong
# DB thi bo qua (dup_skipped).
# ============================================================================
KEYWORD_MARKETS = ("ph", "th", "my", "id", "vn", "sg")


def _parse_keyword_text(text):
    """Doc noi dung dan len: ho tro ca dang "moi dong 1 tu khoa" lan dang file nhom
    (tieu de nhom tren 1 dong, sau do { cac tu khoa } - xem keyword_PH.txt). Dang file
    nhom: chi giu dong NAM TRONG { }; tieu de nhom/dau ngoac tu bo. Neu ca text khong co
    { } thi moi dong khong rong la 1 tu khoa."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    if any(ln in ("{", "}") for ln in lines):
        out, in_block = [], False
        for ln in lines:
            if ln == "{":
                in_block = True
            elif ln == "}":
                in_block = False
            elif in_block:
                out.append(ln)
        return out
    return [ln for ln in lines if ln not in ("{", "}")]


@app.route("/api/keywords/summary", methods=["GET"])
def keywords_summary():
    market = request.args.get("market") or None
    return jsonify(shopee_db.keyword_summary(DB_PATH, market=market))


@app.route("/api/keywords/list", methods=["GET"])
def keywords_list():
    market = request.args.get("market") or None
    status = request.args.get("status") or None
    search = request.args.get("search") or None
    try:
        cat_id = int(request.args["cat_id"]) if request.args.get("cat_id") not in (None, "") else None
    except (TypeError, ValueError):
        return _bad_request("'cat_id' phai la so nguyen")
    limit = int(request.args.get("limit") or 300)
    rows = shopee_db.list_keywords(DB_PATH, market=market, status=status,
                                   search=search, cat_id=cat_id, limit=limit)
    return jsonify({"keywords": rows})


@app.route("/api/keywords/import", methods=["POST"])
def keywords_import():
    """Nhap 1 LO tu khoa (cung market + cung cat_id/cat_name cho CA LO - dung quyet dinh
    "gắn cả lô khi người dùng ấn nút import"). CAC THONG SO SORT/FILTER/SOLD/HOA HONG KHONG
    NHAN O DAY - chung la cau hinh KHI CAO (worker gui kem moi /api/keywords/page_done).
    body: {market, keywords: [..] hoac text: "...", cat_id?, cat_name?}."""
    body = request.get_json(force=True, silent=True) or {}
    market = (body.get("market") or "").strip().lower()
    if market not in KEYWORD_MARKETS:
        return _bad_request(f"'market' phai la 1 trong: {', '.join(KEYWORD_MARKETS)}")
    keywords = body.get("keywords")
    if not isinstance(keywords, list):
        keywords = _parse_keyword_text(body.get("text"))
    if not isinstance(keywords, list) or not keywords:
        return _bad_request("thieu tu khoa: gui 'keywords' (list) hoac 'text' (nhieu dong)")
    try:
        cat_id = int(body["cat_id"]) if body.get("cat_id") not in (None, "") else None
    except (TypeError, ValueError):
        return _bad_request("'cat_id' phai la so nguyen")
    cat_name = (body.get("cat_name") or "").strip() or None
    if cat_id is not None and not cat_name:
        cat_name = shopee_categories.cat_name_for(market, cat_id)
    result = shopee_db.import_keywords(
        DB_PATH, market, keywords, cat_id=cat_id, cat_name=cat_name,
    )
    return jsonify({"ok": True, **result})


@app.route("/api/keywords/claim", methods=["POST"])
def keywords_claim():
    """Worker goi khi ranh: nhan 1 tu khoa pending (hoac con sot/in_progress lease het)
    DUNG market cua tab dang mo. Tra ve {'keyword': {...}} hoac keyword=null khi het viec
    (hoac auto-nhan keyword dang TAT / market bi gioi han - xem kw_auto_assign/kw_auto_market
    trong settings, bat/tat o tab 'Tu Khoa')."""
    body = request.get_json(force=True, silent=True) or {}
    device_key = (body.get("device_key") or "").strip()
    market = (body.get("market") or "").strip().lower()
    if not device_key:
        return _bad_request("thieu 'device_key'")
    if market not in KEYWORD_MARKETS:
        return _bad_request(f"'market' phai la 1 trong: {', '.join(KEYWORD_MARKETS)}")
    settings = shopee_db.get_settings(DB_PATH)
    if not settings.get("kw_auto_assign", 1):
        return jsonify({"keyword": None, "auto": False})
    kw_market = (settings.get("kw_auto_market") or "").strip().lower()
    if kw_market and kw_market != market:
        return jsonify({"keyword": None, "auto": False, "limit_market": kw_market})
    row = shopee_db.claim_keyword(DB_PATH, device_key, market)
    return jsonify({"keyword": row, "auto": True})


@app.route("/api/keywords/page_done", methods=["POST"])
def keywords_page_done():
    """Worker nop 1 trang (page_offset) item that da chup tu chinh trang affiliate. Server
    loc (theo CAU HINH CAO worker gui kem: sold_min/comm_money_min/filter_types - khong luu
    o import) + insert root pending moi (dedup toan DB) + cap nhat checkpoint/keyword."""
    body = request.get_json(force=True, silent=True) or {}
    keyword_id = body.get("keyword_id")
    device_key = (body.get("device_key") or "").strip()
    market = (body.get("market") or "").strip().lower()
    items = body.get("items")
    if keyword_id in (None, ""):
        return _bad_request("thieu 'keyword_id'")
    if not device_key:
        return _bad_request("thieu 'device_key'")
    if not market:
        return _bad_request("thieu 'market'")
    result = shopee_db.keyword_page_done(
        DB_PATH, keyword_id, device_key, market,
        body.get("page_offset"), body.get("page_limit"),
        body.get("total_count"), items if isinstance(items, list) else [],
        sold_min=body.get("sold_min"), comm_money_min=body.get("comm_money_min"),
        filter_types=body.get("filter_types"),
    )
    if not result.get("ok"):
        return jsonify(result), 409
    return jsonify(result)


@app.route("/api/keywords/<int:keyword_id>/fail", methods=["POST"])
def keywords_fail(keyword_id):
    body = request.get_json(force=True, silent=True) or {}
    reason = (body.get("reason") or "unknown_error")[:500]
    shopee_db.fail_keyword(DB_PATH, keyword_id, reason)
    return jsonify({"ok": True})


@app.route("/api/keywords/<int:keyword_id>/reset", methods=["POST"])
def keywords_reset_one(keyword_id):
    ok = shopee_db.reset_keyword(DB_PATH, keyword_id)
    if not ok:
        return _bad_request("khong tim thay keyword nay")
    return jsonify({"ok": True})


@app.route("/api/keywords/<int:keyword_id>", methods=["DELETE"])
def keywords_delete_one(keyword_id):
    ok = shopee_db.delete_keyword(DB_PATH, keyword_id)
    if not ok:
        return _bad_request("khong tim thay keyword nay")
    return jsonify({"ok": True})


@app.route("/api/keywords/bulk_reset", methods=["POST"])
def keywords_bulk_reset():
    """Dat lai hang loat tu khoa khop bo loc ve pending (bo loc rong = tat ca)."""
    body = request.get_json(force=True, silent=True) or {}
    n = shopee_db.reset_keywords(DB_PATH, market=body.get("market") or None,
                                 status=body.get("status") or None)
    return jsonify({"ok": True, "reset": n})


@app.route("/api/keywords/bulk_delete", methods=["POST"])
def keywords_bulk_delete():
    """Xoa hang loat tu khoa khop bo loc (KHONG dong cham toi root da bom)."""
    body = request.get_json(force=True, silent=True) or {}
    n = shopee_db.delete_keywords(DB_PATH, market=body.get("market") or None,
                                  status=body.get("status") or None)
    return jsonify({"ok": True, "deleted": n})


# ============================================================================
# Tab "Vận hành GPM" - dieu phoi worker cào qua GPM Login (Local API 9495).
# Server lam proxy (GPM khong CORS) + quan ly tien trinh node cdp_worker.mjs.
# ============================================================================
import requests as _requests  # noqa: E402

GPM_BASE = os.environ.get("GPM_BASE", "http://127.0.0.1:9495")
GEM_BASE = os.environ.get("GEM_BASE", "http://127.0.0.1:1010")
GPM_PORT_START = int(os.environ.get("GPM_PORT_START", "9601"))
_gpm_ports = {}     # profile_id -> cdp port da cap (ghi nhan de UI)
_gpm_workers = {}   # profile_id -> {proc, name, market, mode, port, engine, base, log}
_gpm_lock = threading.RLock()  # RLock: _gpm_alloc_port() giu lock khi duoc goi tu trong handler cung lock


def _gpm_api(method, path, params=None, body=None, timeout=10):
    r = _requests.request(method, GPM_BASE + path, params=params, json=body, timeout=timeout)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        return {"success": False, "message": r.text[:200]}


def _gpm_tcp_up(port):
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
            return True
    except OSError:
        return False


def _gpm_alloc_port(profile_id):
    with _gpm_lock:
        if profile_id in _gpm_ports:
            return _gpm_ports[profile_id]
        used = set(_gpm_ports.values())
        p = GPM_PORT_START
        tries = 0
        while (p in used or _gpm_tcp_up(p)) and tries < 200:
            p += 1
            tries += 1
        _gpm_ports[profile_id] = p
        return p


def _gpm_log_path(name):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name))
    log_dir = os.path.join(REPO_ROOT, "artifacts")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"gpm_worker_{safe}.log")


def _find_node():
    """Tim node.exe de spawn worker - CHI nhan cac ban Node >= 22 (worker can WebSocket
    global, chi co tu Node 22 tro len; Node 18/20 se loi 'WebSocket is not defined'). Uu tien
    duong dan cai dat chuan (Program Files/LocalAppData) roi moi den PATH - tranh PATH tro
    toi 1 ban Node cu khac."""
    cands = []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(os.path.join(la, "Programs", "nodejs", "node.exe"))
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        v = os.environ.get(key)
        if v:
            cands.append(os.path.join(v, "nodejs", "node.exe"))
    try:
        w = shutil.which("node")
        if w:
            cands.append(w)
    except Exception:
        pass
    seen = []
    for p in cands:
        if p and os.path.isfile(p) and p not in seen:
            seen.append(p)
    first_existing = seen[0] if seen else "node"
    for p in seen:
        try:
            out = subprocess.run([p, "-v"], capture_output=True, text=True, timeout=5)
            ver = (out.stdout or out.stderr or "").strip()
            m = re.match(r"v?(\d+)", ver)
            if m and int(m.group(1)) >= 22:
                return p
        except Exception:
            continue
    return first_existing


def _gpm_worker_status(profile_id):
    info = _gpm_workers.get(profile_id)
    if not info:
        return None
    proc = info.get("proc")
    running = proc is not None and proc.poll() is None
    return {
        "profile_id": profile_id,
        "name": info.get("name"),
        "market": info.get("market"),
        "mode": info.get("mode") or "root",
        "engine": info.get("engine") or "gpm",
        "port": info.get("port"),
        "running": running,
        "exit_code": None if (proc is None or running) else proc.poll(),
        "started_at": info.get("started_at"),
    }


def _gpm_read_log_tail(name, n=40):
    path = _gpm_log_path(name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError:
        return "(chua co log)"


def _gpm_kill_proc(proc):
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


@app.route("/api/gpm/groups", methods=["GET"])
def gpm_groups():
    """Danh sach nhom (group) cua GPM - de UI chon nhom roi moi load profile cua nhom do.
    Dung _gpm_api_all_pages (GPM phan trang mac dinh 30 nhom/lan, goi 1 lan se bo sot nhom
    neu co >30 nhom)."""
    try:
        items = _gpm_api_all_pages("/api/v1/groups")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Khong goi duoc GPM Local API ({GPM_BASE}): {e}"}), 502
    groups = [{"id": g.get("id"), "name": g.get("name") or g.get("id")} for g in items if g.get("id")]
    return jsonify({"ok": True, "groups": groups})


@app.route("/api/gpm/profiles", methods=["GET"])
def gpm_profiles():
    """Danh sach profile GPM + trang thai (port, worker, CDP). Loc theo ?group_id=<id> neu co.
    Dung _gpm_api_all_pages (GPM phan trang mac dinh 30 profile/lan, goi 1 lan se bo sot
    profile neu co >30 profile)."""
    group_id = (request.args.get("group_id") or "").strip()
    try:
        profiles = _gpm_api_all_pages("/api/v1/profiles")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Khong goi duoc GPM Local API ({GPM_BASE}): {e}"}), 502
    if group_id:
        profiles = [p for p in profiles if str(p.get("group_id") or "") == str(group_id)]
    out = []
    for p in profiles:
        pid = p["id"]
        port = _gpm_ports.get(pid)
        st = _gpm_worker_status(pid)
        out.append({
            "id": pid,
            "name": p.get("name"),
            "group_id": p.get("group_id"),
            "market": "ph",  # mac dinh; sua trong UI khi chay
            "port": port,
            "cdp_up": _gpm_tcp_up(port) if port else False,
            "worker": st,
            "browser": (p.get("browser") or {}).get("name", "chrome"),
        })
    return jsonify({"ok": True, "gpm_base": GPM_BASE, "group_id": group_id or None, "profiles": out})


@app.route("/api/gpm/worker/start", methods=["POST"])
def gpm_worker_start():
    """Spawn 1 worker cho 1 profile GPM (tu start browser qua GPM khi chay).
    mode='root' (mac dinh): cdp_worker.mjs - cao tung root (offer/product/<item_id>).
    mode='keyword': cdp_keyword_worker.mjs - cao root AFF theo TU KHOA (worker claim tu
    khoa pending cua market, dieu khien chinh trang affiliate search + chup product/list)."""
    body = request.get_json(force=True, silent=True) or {}
    profile_id = (body.get("profile_id") or "").strip()
    name = (body.get("name") or profile_id).strip()
    market = (body.get("market") or "ph").strip()
    engine = str(body.get("engine") or "gpm").strip().lower()
    if engine not in (_ad.ENGINE_GPM, _ad.ENGINE_GEM):
        engine = _ad.ENGINE_GPM
    mode = (body.get("mode") or "root").strip()
    max_roots = int(body.get("max_roots") or 0)
    hidden = bool(body.get("hidden"))
    if not profile_id:
        return _bad_request("thieu 'profile_id'")
    if mode not in ("root", "keyword"):
        return _bad_request("'mode' chi nhan 'root' hoac 'keyword'")
    # Cau hinh "khi cào" (chi dung cho mode keyword): sort_type/filter_types (API param khi
    # search). Lượt bán & Hoa hồng KHONG gui o day - lay tu "Điều kiện lọc chung" (settings,
    # tab Worker GPM Login) phia server khi loc page_done (xem keyword_page_done).
    try:
        crawl_sort = int(body.get("sort_type") or 2)
        if crawl_sort not in (1, 2):
            crawl_sort = 2
        crawl_filter = int(body.get("filter_types") or 0)
    except (TypeError, ValueError):
        return _bad_request("'sort_type'/'filter_types' phai la so")
    worker_script = "cdp_worker.mjs" if mode == "root" else "cdp_keyword_worker.mjs"
    print(f"[gpm] start worker {name} ({profile_id}) engine={engine} mode={mode} crawl(sort={crawl_sort}, filter={crawl_filter})...", flush=True)
    with _gpm_lock:
        st = _gpm_worker_status(profile_id)
        if st and st["running"]:
            return jsonify({"ok": False, "error": f"Worker '{st['name']}' dang chay roi (pid da co)."}), 409
        port = _gpm_alloc_port(profile_id)
        log_path = _gpm_log_path(name + ("_keyword" if mode == "keyword" else ""))
        node_exe = _find_node()
        engine_flags = (
            ["--gpm-profile", profile_id]
            if engine == _ad.ENGINE_GPM
            else ["--engine", _ad.ENGINE_GEM, "--gem-profile", profile_id, "--gem-base", GEM_BASE]
        )
        cmd = [
            node_exe,
            os.path.join(SCRIPTS_DIR, worker_script),
        ] + engine_flags + [
            "--port", str(port),
            "--device-key", name,
            "--market", market,
            "--log", log_path,
        ]
        if mode == "keyword":
            cmd += ["--sort-type", str(crawl_sort), "--filter-types", str(crawl_filter)]
        if max_roots > 0:
            limit_arg = "--max-keywords" if mode == "keyword" else "--max-roots"
            cmd += [limit_arg, str(max_roots)]
        if hidden:
            cmd += ["--hidden", "1"]
    # spawn NGOAI lock de khong chan cac request khac
    print(f"[gpm] spawn node: {' '.join(cmd)}", flush=True)
    logf = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=logf, stderr=subprocess.STDOUT, text=True)
    except Exception as e:
        logf.close()
        return jsonify({"ok": False, "error": f"Khong spawn duoc worker: {e}"}), 500
    _gpm_workers[profile_id] = {
        "proc": proc, "name": name, "market": market, "mode": mode,
        "port": port, "engine": engine,
        "base": GPM_BASE if engine == _ad.ENGINE_GPM else GEM_BASE,
        "log": log_path,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(f"[gpm] da spawn worker {name} (profile {profile_id}) engine={engine} mode={mode} pid={proc.pid} port={port}")
    return jsonify({"ok": True, "worker": _gpm_worker_status(profile_id)})


@app.route("/api/gpm/worker/stop", methods=["POST"])
def gpm_worker_stop():
    body = request.get_json(force=True, silent=True) or {}
    profile_id = (body.get("profile_id") or "").strip()
    stop_browser = bool(body.get("stop_browser", True))
    if not profile_id:
        return _bad_request("thieu 'profile_id'")
    info = _gpm_workers.get(profile_id)
    if info:
        _gpm_kill_proc(info["proc"])
        info["proc"] = None
    engine = str(body.get("engine") or (info or {}).get("engine") or "gpm").strip().lower()
    if engine not in (_ad.ENGINE_GPM, _ad.ENGINE_GEM):
        engine = _ad.ENGINE_GPM
    base = (info or {}).get("base")
    msg = "Da dung worker."
    if stop_browser:
        try:
            if engine == _ad.ENGINE_GEM:
                _ad.close_profile(_ad.ENGINE_GEM, base=base, profile_id=profile_id)
                msg += " + da dong browser GemLogin."
            else:
                _gpm_api("GET", f"/api/v1/profiles/stop/{profile_id}")
                msg += " + da dong browser GPM."
        except Exception as e:
            msg += f" (dong browser loi: {e})"
    return jsonify({"ok": True, "message": msg})


@app.route("/api/gpm/worker/log", methods=["GET"])
def gpm_worker_log():
    name = (request.args.get("name") or "").strip()
    if not name:
        return _bad_request("thieu 'name'")
    # Log cua worker keyword duoc ghi vao file khac (co duoi _keyword) nen tim THEO worker
    # dang chay truoc; fallback lai duong dan mac dinh theo ten (mode root / worker cu).
    info = next((v for v in _gpm_workers.values() if v.get("name") == name), None)
    log_path = info.get("log") if info and info.get("log") else _gpm_log_path(name)
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        text = "".join(lines[-60:])
    except OSError:
        text = "(chua co log)"
    return jsonify({"ok": True, "log": text})


_GPM_HOME_URL = {
    "ph": "https://shopee.ph/",
    "th": "https://shopee.co.th/",
    "my": "https://shopee.com.my/",
    "vn": "https://shopee.vn/",
    "sg": "https://shopee.sg/",
    "id": "https://shopee.co.id/",
}


def _gpm_ensure_browser(profile_id):
    """Bao dam browser GPM cua profile dang chay + tra ve CDP port that su (tu GPM tra ve khi
    start - khong tu chon port de tranh lech voi instance dang chay san). Neu GPM bao
    ProfileInUse (dang chay tu noi khac nhung khong ro port) thi stop roi start lai 1 lan."""
    with _gpm_lock:
        known = _gpm_ports.get(profile_id)
        if known and _gpm_tcp_up(known):
            return known
    for attempt in range(2):
        try:
            r = _gpm_api("GET", f"/api/v1/profiles/start/{profile_id}")
        except Exception:
            return None
        if r.get("success"):
            data = r.get("data") or {}
            p = data.get("remote_debugging_port")
            if p:
                with _gpm_lock:
                    _gpm_ports[profile_id] = p
                return p
            return None
        if "InUse" in (r.get("message") or "") and attempt == 0:
            try:
                _gpm_api("GET", f"/api/v1/profiles/stop/{profile_id}")
            except Exception:
                pass
            time.sleep(2.5)
            continue
        return None
    return None


def _gpm_open_url(profile_id, url):
    """Mo browser GPM cua profile (neu chua chay) va mo 1 tab toi 'url' - dung chung cho nut
    'Home-Shopee' o tab Vận hành GPM va nut 'Mở profile' o tab Mail Accounts. Raise
    RuntimeError kem thong diep ro rang khi that bai; tra ve CDP port neu mo tab thanh cong."""
    port = _gpm_ensure_browser(profile_id)
    if not port:
        raise RuntimeError("GPM khong start duoc browser (kiem tra GPM app / profile dang mo).")
    up = False
    for _ in range(90):  # cho toi 45s browser bind CDP
        if _gpm_tcp_up(port):
            up = True
            break
        time.sleep(0.5)
    if not up:
        raise RuntimeError(f"Browser GPM start nhung CDP port {port} khong len.")
    # Uu tien dieu huong tab 0 co san (khong tao them tab) - xem _cdp_navigate_first_tab().
    if _cdp_navigate_first_tab(port, url):
        return port
    # Fallback: mo tab moi bang CDP HTTP endpoint /json/new?<url> - CHU Y: url phai nam THANG
    # trong query (khong phai param ten 'url' - Chrome bo qua neu dung dang '?url=...' va chi
    # mo about:blank)
    created = False
    new_tab = f"http://127.0.0.1:{port}/json/new?{url}"
    try:
        r = _requests.put(new_tab, timeout=6)
        if r.status_code in (200, 201):
            created = True
    except Exception:
        pass
    if not created:
        try:
            r = _requests.get(new_tab, timeout=6)
            if r.status_code in (200, 201):
                created = True
        except Exception:
            pass
    if not created:
        raise RuntimeError(f"Mo tab that bai tren port {port}.")
    return port


@app.route("/api/gpm/browser/open", methods=["POST"])
def gpm_browser_open():
    """Mo browser GPM cua profile (neu chua chay) va mo 1 tab toi 'url' - dung cho nut
    'Home-Shopee' (mo trang chu shopee.<market> theo market dang chon cua profile)."""
    body = request.get_json(force=True, silent=True) or {}
    profile_id = (body.get("profile_id") or "").strip()
    url = (body.get("url") or "").strip()
    market = (body.get("market") or "").strip()
    engine = str(body.get("engine") or "gpm").strip().lower()
    if engine not in (_ad.ENGINE_GPM, _ad.ENGINE_GEM):
        engine = _ad.ENGINE_GPM
    if not profile_id:
        return _bad_request("thieu 'profile_id'")
    if not url and market in _GPM_HOME_URL:
        url = _GPM_HOME_URL[market]
    if not url or not (url.startswith("https://") or url.startswith("http://")):
        return _bad_request("thieu 'url' hop le")
    try:
        if engine == _ad.ENGINE_GEM:
            port = _gem_open_url(profile_id, url)
        else:
            port = _gpm_open_url(profile_id, url)
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify({"ok": True, "url": url, "engine": engine, "port": port, "message": f"Da mo {url}"})


def _create_kill_on_close_job():
    """Tao 1 Windows Job Object voi co JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE - MOI process duoc
    gan vao job nay (xem _assign_process_to_job()) se TU DONG bi CHINH HE DIEU HANH ket thuc
    ngay khi process GIU HANDLE cua job (chinh tien trinh cha nay) thoat, BAT KE thoat kieu gi:
    Ctrl+C binh thuong, `taskkill /F` (force kill), Task Manager "End Task", hay crash bat
    ngo - khac han atexit/try-finally (CHI chay duoc khi process thoat "binh thuong", hoan toan
    KHONG chay khi bi kill cung, da xac nhan qua kiem thu thuc te 2026-09-11: taskkill /F
    process cha de lai 3 video-worker process con van tiep tuc giu port, phai tu tay kill tung
    cai). Day la co che CHUAN cua Windows (thuc thi o tang KERNEL, khong phu thuoc code Python
    con chay duoc hay khong) de tranh "process con mo côi". Tra ve HANDLE (int, KHONG duoc dong
    som - phai giu song song voi vong doi process cha, Windows tu dong dong no + kich hoat kill-
    on-close khi process nay ket thuc) neu thanh cong, None neu that bai (khong phai Windows /
    loi API - caller tu fallback, van chay duoc binh thuong, chi mat luoi an toan khi bi kill cung)."""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64), ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64), ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64), ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _assign_process_to_job(job, pid):
    """Gan 1 process (theo pid) vao job da tao boi _create_kill_on_close_job() - im lang bo
    qua neu that bai (vd job=None do khong phai Windows, hoac process da tu thoat truoc khi
    kip gan) de KHONG lam hong luong khoi dong chinh, chi mat luoi an toan cho DUNG process do."""
    if job is None:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        PROCESS_ALL_ACCESS = 0x1F0FFF
        hproc = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if hproc:
            kernel32.AssignProcessToJobObject(job, hproc)
            kernel32.CloseHandle(hproc)
    except Exception:
        pass


def _ensure_port_free(host, port):
    """Bind THAT (roi dong ngay) truoc khi giao cho Werkzeug - phat hien SOM va bao loi RO
    RANG neu port da co server khac dang chay, thay vi de Werkzeug tu bind. Ly do: da xac
    nhan THAT tren may nay Windows cho phep 2 tien trinh CUNG bind duoc 1 port TCP ma KHONG
    bao loi "address already in use" (hanh vi bind mac dinh cua Werkzeug tren Windows) - khi
    do request tu trinh duyet bi he dieu hanh dinh tuyen NGAU NHIEN vao 1 trong 2 tien trinh.
    Neu tien trinh con lai "chet"/ket (vd dang cho 1 giao dich SQLite khong bao gio commit),
    request roi vao no se treo VINH VIEN - dung trieu chung nguoi dung bao cao: dashboard im
    lang khong phan hoi sau 1 luc, phai F5 lai. SO_EXCLUSIVEADDRUSE la co RIENG Windows ep he
    dieu hanh tu choi thang lan bind thu 2, bien loi ngam thanh loi ro rang ngay luc khoi
    dong thay vi lam hong ngau nhien luc dang dung (start_affiliate_scraper.bat da tung co
    kiem tra tuong tu qua netstat, nhung khong bao ve duoc neu server duoc khoi dong theo
    cach khac ngoai file .bat do)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError:
        print(
            f"[affiliate_scrape_server] LOI: port {port} DA CO server khac dang chay san "
            f"(vd mo .bat 2 lan, hoac 1 cua so terminal khac chua dong). Dung 2 tien trinh "
            f"cung chiem 1 port se gay loi 'dashboard khong phan hoi ngau nhien'. Dong server "
            f"cu (hoac dung cua so dang chay san) truoc khi mo cai moi."
        )
        sys.exit(1)
    finally:
        probe.close()


def main():
    global DB_PATH, VIDEO_PORT, VIDEO_PORTS, MAIN_PORT, _KILL_ON_CLOSE_JOB
    # Ep stdout/stderr sang UTF-8 (2026-09-12, bug thuc te: chay .bat qua SSH/plink khong ke
    # thua duoc `chcp 65001` cua .bat nhu khi mo truc tiep tren may - console fallback ve
    # codepage mac dinh (vd cp1252), khien BAT KY print() nao co ky tu tieng Viet (rat nhieu
    # trong file nay, vd dong log "video (N process, ...)" o duoi) crash ngay voi
    # UnicodeEncodeError, server KHONG khoi dong duoc. reconfigure() co tu Python 3.7+.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8877)
    ap.add_argument(
        "--video-port", type=int, default=None,
        help=(
            "Port DAU TIEN cho traffic dang video (postVideoPoolWorker goi post_next() sang "
            "day thay vi cung origin voi dashboard) - mac dinh --port + 1 neu khong truyen. "
            "Cac video-worker process con lai dung cac port LIEN TIEP tu day (xem --video-workers)."
        ),
    )
    ap.add_argument(
        "--video-workers", type=int, default=None,
        help=(
            "So PROCESS RIENG (khong phai thread) phuc vu traffic dang video, moi process 1 "
            "port lien tiep tu --video-port - dung DE THAT SU tan dung nhieu loi CPU: GIL cua "
            "Python khien nhieu THREAD trong CUNG 1 process KHONG chay song song duoc phan "
            "CPU-bound (chi phan cho I/O that su moi thuc su chay song song trong 1 process) - "
            "chi PROCESS RIENG moi co GIL rieng, he dieu hanh moi xep duoc len loi CPU khac "
            "nhau THAT SU. Mac dinh TU TINH = 80% so loi CPU logic cua may nay (lam tron xuong, "
            "toi thieu 1) - luon chua 20% cho dashboard/DB/tac vu khac, khong hard-code 1 con "
            "so co dinh nua."
        ),
    )
    ap.add_argument("--db-path", default=shopee_db.DB_PATH_DEFAULT)
    ap.add_argument(
        "--serve-only-port", type=int, default=None, help=argparse.SUPPRESS,
        # NOI BO - KHONG phai co cho nguoi dung tu truyen tay: main() tu goi lai CHINH SCRIPT
        # NAY (sys.executable, __file__) voi co nay de tao 1 "video-worker" PROCESS CON dich
        # thuc (xem nhanh spawn ben duoi) - process con chi phuc vu DUY NHAT 1 port nay,
        # KHONG spawn them process/thread nao khac (tranh de quy vo han goi lai chinh no).
    )
    args = ap.parse_args()
    DB_PATH = args.db_path
    shopee_db.init_db(DB_PATH)  # dam bao bang/cot ton tai truoc khi nhan request dau tien - luon
    # chay o CA process cha lan con, nhung process cha luon chay TRUOC (spawn con SAU khi ham
    # nay tra ve) nen migration/tao bang chi thuc su xay ra 1 lan, process con goi lai chi la
    # cac PRAGMA/CREATE-IF-NOT-EXISTS khong lam gi them (an toan, khong rac giao dich).

    if args.serve_only_port is not None:
        # Nhanh "video-worker" CON - xem giai thich o --serve-only-port o tren.
        VIDEO_PORT = args.serve_only_port
        _ensure_port_free("127.0.0.1", args.serve_only_port)
        print(f"[affiliate_scrape_server] video-worker process (PID {os.getpid()}): http://127.0.0.1:{args.serve_only_port}")
        app.run(host="127.0.0.1", port=args.serve_only_port, debug=False, threaded=True)
        return

    VIDEO_PORT = args.video_port if args.video_port is not None else args.port + 1
    # Tran mac dinh: 4 (ban dau) -> 12 (2026-09-11) -> 24 co dinh (2026-09-12) -> TU TINH theo
    # 80% so loi CPU logic cua CHINH may dang chay (2026-09-12, yeu cau nguoi dung "tool tự
    # set-process thông minh theo 80% số process của máy server") - khong con hard-code 1 con
    # so co dinh, tu thich nghi voi tung may (vd may 56 loi -> 44 process, may 8 loi -> 6
    # process), luon chua 20% loi CPU cho dashboard/DB/cac tac vu khac (khong chiem het may).
    # Van uu tien args.video_workers neu nguoi dung TU truyen tay (--video-workers N) - chi tu
    # tinh khi KHONG truyen co nay.
    #
    # Boi canh: sau khi vá proxy theo tung tai khoan cho ca 6 buoc dang video (xem
    # sign_request()/vod_preupload()/upload_video_wscloud()/report_upload_wscloud() trong
    # shopee_video_post.py), nut that server ky ngoai (Chill68, rate-limit ~30 request dong
    # thoi/IP nguon - do thuc te 2026-09-12) coi nhu da go vi MOI tai khoan gio ky/dang bang
    # DUNG proxy rieng cua no thay vi dong het vao 1 IP may chu - nen tang so process video-
    # worker (quyet dinh tran ket noi trinh duyet that, xem POSTVIDEO_CONN_PER_ORIGIN o
    # templates/index.html) gio an toan va co ich thuc su, khong con la "tang cho co" nua.
    auto_video_workers = max(1, int((os.cpu_count() or 4) * 0.8))
    video_workers = args.video_workers if args.video_workers is not None else auto_video_workers
    video_workers = max(1, video_workers)
    video_ports = [VIDEO_PORT + i for i in range(video_workers)]
    VIDEO_PORTS = video_ports
    MAIN_PORT = args.port

    _ensure_port_free("127.0.0.1", args.port)
    for p in video_ports:
        _ensure_port_free("127.0.0.1", p)

    workers_source = "tự tính 80% CPU" if args.video_workers is None else "--video-workers"
    print(
        f"[affiliate_scrape_server] DB: {DB_PATH} | chinh: http://127.0.0.1:{args.port} "
        f"| video ({video_workers} process, {workers_source}): {', '.join('http://127.0.0.1:' + str(p) for p in video_ports)}"
    )
    # threaded=True QUAN TRONG: mac dinh Werkzeug dev server xu ly TUAN TU tung request 1
    # (single-threaded) - voi so luong tab Tampermonkey (worker) chay song song + dashboard
    # tu poll 4 API moi 5s, request nao cung phai xep hang cho request truoc xong. Nguoi
    # dung bao cao trieu chung "bam nut tren dashboard khong phan hoi, giong mat mang" - dung
    # la hien tuong request bi ket trong hang doi nay, KHONG phai loi mang that. An toan bat
    # thread vi tang du lieu (shopee_db.py) da thiet ke san cho ghi song song: moi ham tu mo
    # 1 connection SQLite RIENG (_connect(), khong dung chung giua cac request/thread) + WAL
    # mode + BEGIN IMMEDIATE cho cac giao dich ghi quan trong (xem init_db()/try_assign_verified()).
    #
    # NHIEU PROCESS (khong phai thread) cho video - yeu cau nguoi dung 2026-09-11 "triển khai
    # multi-process ... tối ưu nhất" sau khi xac nhan van con bi GIL gioi han 1 loi CPU du da
    # tach port. Moi child o day la 1 tien trinh Python HOAN TOAN doc lap (KHONG chia se bo nho
    # voi nhau/voi cha, ke ca _MATCHED_POOL_CACHE - moi process tu cache rieng, ton chut bo nho
    # du khong anh huong dung), CHI chia se DUY NHAT qua file DB chung. Vi vay claim video
    # TRUOC KHI dang (xem _claim_next_pending()) BAT BUOC phai chuyen tu bien Python trong bo
    # nho (cach cu, chi dung dan trong 1 process) sang bang DB video_claims (xem
    # shopee_db.try_claim_video()/release_video_claim()) - neu khong 2 process khac nhau co the
    # cung claim/dang trung 1 video.
    # Windows Job Object voi kill-on-close (xem _create_kill_on_close_job()) - luoi an toan de
    # video-worker process con KHONG bi mo côi (giu port mai) neu process cha nay bi ket thuc
    # BAT NGO/CUNG (taskkill /F, Task Manager, crash) thay vi thoat binh thuong qua Ctrl+C - da
    # xac nhan qua kiem thu thuc te la 1 van de THAT (atexit/try-finally KHONG du, chi chay
    # duoc khi thoat "binh thuong"). None neu khong phai Windows - _assign_process_to_job() se
    # im lang bo qua, chap nhan mat luoi an toan nay tren nen tang khac.
    _KILL_ON_CLOSE_JOB = _create_kill_on_close_job()

    child_procs = []
    for p in video_ports:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--serve-only-port", str(p), "--db-path", DB_PATH],
        )
        child_procs.append(proc)
        _assign_process_to_job(_KILL_ON_CLOSE_JOB, proc.pid)

    def _terminate_children():
        for proc in child_procs:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        for proc in child_procs:
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    atexit.register(_terminate_children)  # luoi an toan neu app.run() thoat qua nhanh khac Ctrl+C binh thuong
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    finally:
        _terminate_children()


if __name__ == "__main__":
    main()

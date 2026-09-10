"""Đăng video lên Shopee qua pipeline WSCloud - tự build lại dựa trên reverse engineering
tool "Chill 68" (GemLogin), xem tài liệu đầy đủ ở CHILL68_VIDEO_UPLOAD_RE.md (repo root).

Chuỗi API thật (đã xác nhận bằng Frida hook SSL_write/SSL_read + hook DLL tls-client-64.dll,
KHÔNG suy đoán):
    1. POST {api_mms}/uploadapi/api/v1/vod/preupload   -> vid + upload token (WSCloud)
    2. POST {wscloud}/file/upload                       -> upload file video thật (multipart)
    3. POST {api_mms}/uploadapi/api/v1/vod/reportupload -> báo hoàn tất, đợi 10s xử lý
    4. POST {server2_url}/api/sign  (X-API-Key)          -> ký header chống anti-bot cho (5)
    5. POST {sv}/api/v2/biz/post/precheck                -> lấy extra_context
    6. lặp lại (4) cho (7)
    7. POST {sv}/api/v2/biz/post/create                  -> đăng thật, trả post_id

QUAN TRỌNG - PHẢI đọc trước khi dùng:
    - Header chống anti-bot (`1013e40f`, `x-sap-ri`, ...) của bước 5/7 KHÔNG tự tính được -
      bắt buộc gọi `server2_url` (license Chill 68 cấp `server2_api_key` qua endpoint riêng,
      xem mục 4 trong CHILL68_VIDEO_UPLOAD_RE.md). Không có key hợp lệ -> luôn bị Shopee
      chặn anti-bot (code 90309999, HTTP 418).
    - Token upload (bước 2) lấy từ `token_url` (mặc định https://sigkey.videoshopee.com/
      generate_token, xem get_upload_token()) - hết hạn nhanh (field 'deadline' trong policy),
      PHẢI lấy token MỚI ngay trước mỗi lần upload, không cache lâu.
    - Lần gọi precheck/create ĐẦU TIÊN thường bị Shopee chặn anti-bot (HTTP 418, code
      90309999) dù header ký đúng - quan sát thực tế từ tool gốc là tự động retry ngay 1 lần
      và lần 2 thường qua. shopee_precheck()/shopee_create_post() đã tự làm việc này.
    - `skip_cover_check: true` được set cứng trong mọi payload đã bắt được -> KHÔNG cần tự
      upload cover ảnh riêng cho v1 này (Shopee bỏ qua bước kiểm tra cover). Nếu sau này thấy
      video bị từ chối vì thiếu cover, xem lại mục "Bước 3" trong tài liệu RE để bổ sung.
    - Chỉ xác nhận đầy đủ market Thái Lan (MARKET_CONFIG["th"]). Thêm market khác PHẢI tự
      capture lại (không đoán bừa - từng bị sai market='vn' mặc định trong shopee_db.py vì lý
      do tương tự, xem market_from_link() ở đó) - dùng lại kỹ thuật Frida hook trong
      CHILL68_VIDEO_UPLOAD_RE.md mục 6.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import subprocess
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests

import shopee_db
from gsheet_video_scanner import ProductRow, build_matched_pool

_REQUEST_TIMEOUT_SECONDS = 30
_UPLOAD_TIMEOUT_SECONDS = 300  # video có thể vài chục MB, cần timeout dài hơn request thường
# Số lần thử (KỂ CẢ lần đầu) khi bị anti-bot chặn (418/90309999) - đã xác nhận qua test
# thật (2026-09-10) block này có tính XÁC SUẤT, không phải cứ chặn lần 1 là chắc qua lần 2
# (1 lần thấy qua ngay ở lần 2, 1 lần khác vẫn bị chặn ở CẢ lần 2) - tăng lên 4 để giảm khả
# năng thất bại hẳn 1 video vì "xui" 2 lần liên tiếp.
_POST_ANTI_BOT_MAX_ATTEMPTS = 4
_POST_ANTI_BOT_RETRY_DELAY_SECONDS = 2  # nhân dần theo số lần đã thử (2s, 4s, 6s...)
_AFTER_REPORT_WAIT_SECONDS = 10  # Shopee cần thời gian xử lý/transcode sau reportupload

# Cấu hình theo market - 'th' VÀ 'my' đã xác nhận bằng post THÀNH CÔNG THẬT (không chỉ
# capture). 'ph' domain đoán ĐÚNG (resolve ra hạ tầng Shopee thật) nhưng CHƯA post được -
# xem docstring MARKET_CONFIG["ph"] bên dưới + CHILL68_VIDEO_UPLOAD_RE.md mục "Chưa xác
# nhận". Domain của 'ph'/'my' suy theo ĐÚNG pattern đặt tên domain của 'th' (sv.shopee.{tld},
# up-ws-{market}.vod.susercontent.com, api-quic.mms.shopee.{tld}). 'language' lấy ĐÚNG giá
# trị field 'language' trong cookie thật PH/MY người dùng cung cấp (cả 2 đều 'en', khác 'th'
# của TH) - không suy đoán.
MARKET_CONFIG = {
    "th": {
        "host": "shopee.co.th",
        "sv": "sv.shopee.co.th",
        "api_mms": "api-quic.mms.shopee.co.th",
        "wscloud": "up-ws-th.vod.susercontent.com",  # UPLOAD only - KHÔNG dùng để build video.url
        # Domain THẬT SỰ dùng cho field video.url/videourl (report+create) - lấy nguyên từ
        # request 'create' THẬT đã capture (không phải suy đoán): "down-ws-global.vod.
        # susercontent.com/th-....mp4". "global" (không phải "-th") - có vẻ CDN đọc lại dùng
        # chung 1 domain cho mọi market, khác hẳn domain upload (per-market: up-ws-{market}).
        # Trước đây module này SAI CHỖ NÀY: tái dùng domain UPLOAD (wscloud) cho video.url,
        # khiến Shopee không đọc lại được video thật khi verify -> trả lỗi chung chung
        # '400003 Post too many videos' (không phải rate-limit - xem CHILL68_VIDEO_UPLOAD_RE.md).
        "video_cdn": "down-ws-global.vod.susercontent.com",
        "timezone": "Asia/Bangkok",
        "language": "th",
    },
    "ph": {
        # DOMAIN ĐÚNG (đã verify DNS resolve ra hạ tầng Shopee thật) nhưng POST BỊ CHẶN
        # 418/code 90309999 (anti-bot) dù retry đủ 4 lần - test thật 2026-09-10. Nghi vấn
        # chính: PH cần gọi server ký RIÊNG `phServer2Url = http://157.66.24.236:3004` (xem
        # creditInfo mục 4 trong CHILL68_VIDEO_UPLOAD_RE.md) thay vì dùng chung server2_url
        # như TH/MY - server đó CÒN SỐNG nhưng chưa capture được path/định dạng request thật.
        # SigningConfig hiện KHÔNG có chỗ override signing server theo market - cần thêm nếu
        # xác nhận đúng là do server ký riêng.
        "host": "shopee.ph",
        "sv": "sv.shopee.ph",
        "api_mms": "api-quic.mms.shopee.ph",
        "wscloud": "up-ws-ph.vod.susercontent.com",
        "video_cdn": "down-ws-global.vod.susercontent.com",
        "timezone": "Asia/Manila",
        "language": "en",  # lấy ĐÚNG từ field 'language' trong cookie PH thật (upload/PH/ph.txt)
    },
    "my": {
        # ĐÃ XÁC NHẬN bằng post THÀNH CÔNG THẬT (2026-09-10, 3/3 video) - domain đoán theo
        # pattern là ĐÚNG, dùng CHUNG server2_url với TH (không cần server ký riêng như PH).
        "host": "shopee.com.my",
        "sv": "sv.shopee.com.my",
        "api_mms": "api-quic.mms.shopee.com.my",
        "wscloud": "up-ws-my.vod.susercontent.com",
        "video_cdn": "down-ws-global.vod.susercontent.com",
        "timezone": "Asia/Kuala_Lumpur",
        "language": "en",  # lấy ĐÚNG từ field 'language' trong cookie MY thật (upload/MY/MY.txt)
    },
}

# Device fingerprint dùng cho header 'client-info' - giá trị lấy NGUYÊN VĂN từ source code
# gốc đã đọc được (biến clientInfoHeader lặp lại y hệt ở MỌI flow S1/S2/S3), KHÔNG phải suy
# đoán. device_model/os_version/rn_version + đặc biệt DEFAULT_DEVICE_ID là HẰNG SỐ TĨNH -
# xác nhận qua test thật (2026-09-10): tự sinh device_id ngẫu nhiên (thay vì dùng đúng giá
# trị cố định này) là nguyên nhân thật gây '400003 Post too many videos' dai dẳng, không
# phải rate-limit (xem post_video_to_shopee()).
DEFAULT_DEVICE = {
    "device_model": "SM-G991B",
    "os_version": "34",
    "client_version": "35943",
    "rn_version": "6.97.5",
}
DEFAULT_DEVICE_ID = "Qs89IS%2BDlxAYPzSRpvyXF1fld5iOiKGEE47uEZ64IFI%3D"

# Giá trị 'shopee_app_version' DỰ PHÒNG - đọc được NGUYÊN VĂN (số literal, không phải suy
# đoán) từ source code gốc: biến FALLBACK_APP_VERSION dùng chung cho mọi flow S1/S2/S3, và
# tool gốc KHÔNG BAO GIỜ tin nguyên giá trị 'shopee_app_version' có sẵn trong cookie người
# dùng đưa vào - nó GHI ĐÈ field này trong CHÍNH Cookie header (regex
# `shopee_app_version=\d+` -> `shopee_app_version=${CREDIT_APP_VERSION}`) TRƯỚC khi gọi
# create(), và khi create() trả `{"code":400003,"msg":"Post too many videos, please have a
# rest"}` thì coi đó là tín hiệu "app_version bị server ký/Shopee từ chối - đổi version khác"
# rồi thử LẠI NGAY (không delay) với 1 cookie biến thể thứ 2 mang shopee_app_version khác
# (37229) - KHÔNG PHẢI rate-limit thật (xem shopee_create_post()). Giá trị PRIMARY
# (CREDIT_APP_VERSION) không đọc được số cụ thể từ capture, nên module này dùng chính giá trị
# 'shopee_app_version' đã có trong cookie làm PRIMARY (nhiều khả năng vẫn hợp lệ vì lấy từ
# phiên đăng nhập thật) và chỉ đổi sang FALLBACK khi create() thực sự trả 400003.
_FALLBACK_APP_VERSION = "37229"


def _cookie_with_app_version(cookie_str: str, app_version: str) -> str:
    """Trả về cookie_str với field 'shopee_app_version' được GHI ĐÈ thành app_version - đúng
    cơ chế `shopeeCookie.replace(/shopee_app_version=\\d+/, ...)` trong source gốc (xem
    _FALLBACK_APP_VERSION). Nếu cookie chưa có field này thì nối thêm vào cuối (cũng đúng
    nhánh source gốc: `shopeeCookie + '; shopee_app_version=' + ver`)."""
    if re.search(r"shopee_app_version=\d+", cookie_str):
        return re.sub(r"shopee_app_version=\d+", f"shopee_app_version={app_version}", cookie_str)
    return f"{cookie_str}; shopee_app_version={app_version}"

_SHOPEE_PRODUCT_URL_RE = re.compile(r"-i\.(\d+)\.(\d+)|/product/(\d+)/(\d+)")


@dataclass(frozen=True)
class SigningConfig:
    """Thông tin lấy 1 lần từ license Chill 68 (endpoint checkngaytest...workers.dev, xem
    mục 0+4 trong CHILL68_VIDEO_UPLOAD_RE.md) - truyền vào đây thay vì hard-code, vì key có
    thể xoay/hết hạn theo license."""
    server2_url: str
    server2_api_key: str
    token_url: str = "https://sigkey.videoshopee.com/generate_token"
    token_api_key: str = ""


@dataclass
class PostResult:
    success: bool
    post_id: str | None = None
    vid: str | None = None
    error: str | None = None
    raw_responses: dict = field(default_factory=dict)


def parse_cookie(cookie_str: str) -> dict[str, str]:
    """Chuỗi cookie dạng 'a=1; b=2; ...' (định dạng trong get_cookie/*.txt) -> dict.
    Không dùng http.cookies.SimpleCookie vì vài giá trị Shopee (SPC_ST, AC_CERT_D...) chứa
    ký tự '=' base64 lồng trong value, SimpleCookie parse sai các cookie này."""
    out: dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        out[key.strip()] = value.strip()
    return out


def extract_shop_item_id(url: str) -> tuple[str, str] | None:
    """Lấy (shop_id, item_id) từ 1 link sản phẩm Shopee. Hỗ trợ 2 dạng URL phổ biến:
    '...-i.{shop_id}.{item_id}' (dạng SEO mới) và '.../product/{shop_id}/{item_id}' (dạng
    cũ). Trả None nếu không khớp - người gọi tự quyết định bỏ qua link hay báo lỗi."""
    m = _SHOPEE_PRODUCT_URL_RE.search(url)
    if not m:
        return None
    if m.group(1) and m.group(2):
        return m.group(1), m.group(2)
    return m.group(3), m.group(4)


def build_products_field(merge_links: str) -> list[dict]:
    """Cột F ('Link Sản Phẩm Muốn Gắn Giỏ', xem gsheet_push_engine.to_sheet_row) nối nhiều
    link bằng '|' -> field 'products' trong body create_post(). Link không parse được
    (extract_shop_item_id trả None) bị bỏ qua lặng lẽ - không có shop_id/item_id thì không
    thể gắn giỏ cho link đó, nhưng không nên chặn cả bài đăng chỉ vì 1 link lỗi."""
    products = []
    for link in (merge_links or "").split("|"):
        link = link.strip()
        if not link:
            continue
        parsed = extract_shop_item_id(link)
        if parsed is None:
            continue
        shop_id, item_id = parsed
        products.append({
            "custom_name": "",
            "item_id": int(item_id),
            "shop_id": int(shop_id),
            "source_tab": 1,
            "mcn_campaign_token": "",
            "free_sample_info": {
                "free_sample_context": "",
                "need_free_sample_proof": False,
                "free_sample_proof_status": 0,
            },
        })
    return products


_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_FFMPEG_DIMENSIONS_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


def _ffmpeg_exe() -> str:
    """ffprobe không có sẵn trên máy dev (không phải dependency có sẵn của project) - dùng
    ffmpeg từ package imageio-ffmpeg (pip, tự tải sẵn binary tĩnh, không cần cài đặt hệ
    thống) và đọc thông tin qua stderr của `ffmpeg -i` thay vì ffprobe -show_entries."""
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def get_video_metadata(video_path: str) -> dict:
    """width/height/duration(ms) bằng cách parse stderr của `ffmpeg -i` (ffmpeg luôn thoát
    exit-code khác 0 khi không truyền -y/output - đây là hành vi BÌNH THƯỜNG, không phải
    lỗi, nên KHÔNG dùng check=True). duration đổi ra milli-giây cho khớp field 'duration'
    trong payload thật (quan sát được là số ms, ví dụ 3065/3000 cho video ~3 giây)."""
    proc = subprocess.run(
        [_ffmpeg_exe(), "-i", video_path],
        capture_output=True, text=True,
    )
    stderr = proc.stderr
    dur_match = _FFMPEG_DURATION_RE.search(stderr)
    dim_match = _FFMPEG_DIMENSIONS_RE.search(stderr)
    if not dur_match or not dim_match:
        raise RuntimeError(f"Không đọc được metadata video từ ffmpeg cho {video_path!r}:\n{stderr[-500:]}")
    hours, minutes, seconds = dur_match.groups()
    duration_ms = round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)
    return {
        "width": int(dim_match.group(1)),
        "height": int(dim_match.group(2)),
        "duration": duration_ms,
    }


def file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_request(signing: SigningConfig, target_url: str, body_str: str) -> dict[str, str]:
    """Gọi server ký chống anti-bot (server2Url/api/sign) - trả object header để merge
    trực tiếp vào request thật gửi Shopee. KHÔNG có logic tự tính ở local - xem docstring
    module. body_str PHẢI là chuỗi JSON đã stringify (không phải dict) - đúng như request
    thật đã bắt được ({"url":..., "body": "<json string>"})."""
    resp = requests.post(
        signing.server2_url,
        json={"url": target_url, "body": body_str},
        headers={"Content-Type": "application/json", "X-API-Key": signing.server2_api_key},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    headers = data.get("data") if isinstance(data, dict) else None
    if not headers:
        raise RuntimeError(f"Sign server không trả header hợp lệ: {data!r}")
    return headers


def get_upload_token(signing: SigningConfig) -> str:
    """Token dạng Qiniu ('key:sign:policy_base64') dùng ngay cho upload_video_wscloud().
    Body rỗng '{}' - chỉ cần đúng X-API-Key (đã xác nhận bằng capture thật)."""
    resp = requests.post(
        signing.token_url,
        json={},
        headers={"Content-Type": "application/json", "X-API-Key": signing.token_api_key},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    token = resp.text.strip()
    if token.count(":") != 2:
        raise RuntimeError(f"Upload token không đúng định dạng key:sign:policy: {token!r}")
    return token


def _client_info(device: dict, device_id: str) -> str:
    """Header 'client-info' - CÓ MẶT trong request thật (precheck/create) đã capture
    nhưng bị THIẾU trong bản đầu của module này (chỉ thêm cho preupload/reportupload)."""
    return (
        f"device_id={device_id};device_model={device['device_model']};os=0;"
        f"os_version={device['os_version']};client_version={device['client_version']};"
        f"network=1;platform=1;rn_version={device['rn_version']};api_source=na;"
        f"cpu_model=;live_device_model=samsung+o1s"
    )


def _url_query_model(device: dict) -> str:
    """Giá trị field 'model' trong query string URL create() - dạng 'samsung SM-G991B'
    (có prefix hãng + khoảng trắng), KHÁC với device['device_model'] ('SM-G991B' trơn dùng
    trong client-info). Lấy nguyên từ URL thật đã capture:
    '...&model=samsung%20SM-G991B&android_performance=802'."""
    return f"samsung {device['device_model']}"


def _app_info_device_model(device: dict) -> str:
    """Giá trị field 'app_info.device_model' trong body precheck/create - dạng mô tả đầy
    đủ 'Brand/samsung Model/sm-g991b OSVer/34 Manufacturer/samsung', KHÁC HẲN
    device['device_model'] trơn. Lấy nguyên từ body thật đã capture."""
    return (
        f"Brand/samsung Model/{device['device_model'].lower()} "
        f"OSVer/{device['os_version']} Manufacturer/samsung"
    )


def _base_headers(
    cookie_str: str, csrf_token: str, market_cfg: dict,
    device: dict = DEFAULT_DEVICE, device_id: str = "", client_request_id: str = "",
) -> dict[str, str]:
    headers = {
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache, no-store",
        "af-ac-enc-sz-token": "",
        "User-Agent": "okhttp/3.12.4 app_type=1",
        "Cookie": cookie_str,
        "X-CSRFToken": csrf_token,
        "X-SAP-Type": "1",
        "X-Shopee-Client-Timezone": market_cfg["timezone"],
        "SHOPEE_HTTP_DNS_MODE": "1",
        "language": market_cfg["language"],
        "referer": f"https://{market_cfg['host']}/",
        "sfid": "",
    }
    if device_id:
        headers["client-info"] = _client_info(device, device_id)
    if client_request_id:
        headers["Client-Request-Id"] = client_request_id
    return headers


def vod_preupload(
    cookie_str: str, csrf_token: str, user_id: str, market_cfg: dict,
    fsize: int, md5: str, device: dict = DEFAULT_DEVICE,
    device_id: str = "", client_request_id: str = "",
) -> dict:
    """Bước 1. Trả về dict từ field 'data' của response ({'vid': ..., 'services': [...]})."""
    headers = {
        **_base_headers(cookie_str, csrf_token, market_cfg, device, device_id, client_request_id),
        "Content-Type": "application/json",
        "x-api-source": "rn",
    }
    body = {
        "biz": 124, "ver": 3,
        "fingerprint_info": {"fsize": fsize, "md5": md5},
        "reportdata": {
            "sdkversion": "1.0", "appversion": device["client_version"],
            "ostype": "0", "osversion": device["os_version"],
            "token_type": 0, "userid": user_id, "reporttime": int(time.time() * 1000),
        },
        "mediatype": 1,
        # skip_cover_check=true ở precheck/create -> cover fingerprint không cần khớp ảnh
        # thật, nhưng field vẫn bắt buộc phải có mặt trong payload (đã xác nhận qua capture).
        "cover_fingerprint_info": {"fsize": 0, "md5": "00000000000000000000000000000000"},
    }
    resp = requests.post(
        f"https://{market_cfg['api_mms']}/uploadapi/api/v1/vod/preupload",
        json=body, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json().get("data")
    if not data or not data.get("vid"):
        raise RuntimeError(f"Preupload không trả vid: {resp.text[:300]!r}")
    return data


def upload_video_wscloud(market_cfg: dict, upload_token: str, vid: str, video_path: str) -> None:
    """Bước 2 - upload file thật lên WSCloud. Không dùng lại token cũ - luôn gọi
    get_upload_token() ngay trước bước này (xem docstring module)."""
    with open(video_path, "rb") as f:
        resp = requests.post(
            f"https://{market_cfg['wscloud']}/file/upload",
            files={"file": (f"{vid}.mp4", f, "video/mp4")},
            data={"token": upload_token, "key": f"{vid}.mp4"},
            headers={"User-Agent": "WCS-Android-SDK-1.6.8"},
            timeout=_UPLOAD_TIMEOUT_SECONDS,
        )
    resp.raise_for_status()


def report_upload_wscloud(
    cookie_str: str, csrf_token: str, market_cfg: dict, market_key: str,
    vid: str, fsize: int, video_meta: dict, device: dict = DEFAULT_DEVICE,
    device_id: str = "", client_request_id: str = "",
) -> dict:
    """Bước 3. cover_md5 dùng giá trị random (giống hành vi tool gốc ở nhánh WSCloud PH -
    không phải MD5 thật của cover, chỉ là placeholder Shopee chấp nhận nhờ
    skip_cover_check=true ở các bước sau)."""
    headers = {
        **_base_headers(cookie_str, csrf_token, market_cfg, device, device_id, client_request_id),
        "Content-Type": "application/json",
        "x-api-source": "rn",
    }
    body = {
        "ver": 0, "extendid": vid, "code": 0, "vid": vid, "biz": 124,
        "cover_md5": uuid.uuid4().hex,
        "fsize": fsize,
        "videourl": f"https://{market_cfg['video_cdn']}/{vid}.mp4",
        "reportdata": {"ostype": "0", "app_id": f"com.shopee.{market_key}"},
        "serviceid": "wscloud",
        "fileinfos": {
            "duration": video_meta["duration"], "vbitrate": 0, "abitrate": 0,
            "width": video_meta["width"], "height": video_meta["height"],
            "fps": 0, "mediatype": 1,
        },
    }
    resp = requests.post(
        f"https://{market_cfg['api_mms']}/uploadapi/api/v1/vod/reportupload",
        json=body, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()


def _get_header(headers, name: str, default: str = "") -> str:
    """tls_client trả headers dạng dict thường (PHÂN BIỆT HOA/THƯỜNG) - KHÁC
    requests.Response.headers (CaseInsensitiveDict, tự khớp mọi cách viết hoa/thường). Đã
    xác nhận qua test thật (2026-09-10): tra thẳng resp.headers.get("content-type") luôn
    miss vì Shopee trả đúng key 'Content-Type' -> code cũ đọc nhầm response 200 OK thành
    lỗi (tưởng không phải JSON). Hàm này quét không phân biệt hoa/thường để tránh lặp lại
    bug này ở chỗ khác."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return default


def parse_proxy(proxy_line: str) -> str:
    """'ip:port:user:pass' (định dạng trong upload/proxy.txt) -> URL proxy dạng
    'http://user:pass@ip:port' dùng được cho cả tls_client.Session.proxies và
    requests' proxies dict. Cũng chấp nhận proxy KHÔNG có auth (chỉ 'ip:port')."""
    parts = proxy_line.strip().split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    raise ValueError(f"Định dạng proxy không hợp lệ (cần 'ip:port:user:pass' hoặc 'ip:port'): {proxy_line!r}")


def _tls_session(proxy: str | None = None):
    """Session giả TLS fingerprint 'okhttp4_android_13' - GIỐNG CHÍNH XÁC config
    node-tls-client mà tool gốc dùng cho precheck/create (xem tlsClientIdentifier trong
    request thật đã capture, mục 5 CHILL68_VIDEO_UPLOAD_RE.md). BẮT BUỘC dùng cái này (KHÔNG
    phải `requests` thường) cho 2 API này - đã xác nhận qua test thật (2026-09-10): cùng
    header ký hợp lệ nhưng gọi bằng `requests` (TLS fingerprint mặc định của Python) bị
    Shopee chặn 418/90309999 ở MỌI request bất kể sign đúng hay không; nghi vấn Shopee đối
    chiếu TLS JA3 thực tế với User-Agent/header claim (okhttp Android) - lệch là chặn ngay,
    không liên quan gì tới việc header ký đúng hay sai.

    proxy: URL đã parse qua parse_proxy() - KHÔNG liên quan lỗi '400003 Post too many videos'
    (đã xác nhận nguyên nhân thật là 'shopee_app_version' bị từ chối, xem
    _FALLBACK_APP_VERSION + shopee_create_post()). Giữ tham số proxy vì vẫn hữu ích để phân
    tán traffic khi chạy nhiều video song song."""
    import tls_client
    session = tls_client.Session(
        client_identifier="okhttp4_android_13",
        random_tls_extension_order=True,
        force_http1=False,
    )
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def _post_signed(
    signing: SigningConfig, url: str, body: dict, cookie_str: str, csrf_token: str,
    market_cfg: dict, proxy: str | None = None, device: dict = DEFAULT_DEVICE,
    device_id: str = "", client_request_id: str = "",
):
    """POST tới sv.shopee.co.th với header đã ký - dùng chung cho precheck và create. Tự
    retry (xem _POST_ANTI_BOT_MAX_ATTEMPTS) nếu bị anti-bot chặn (HTTP 418 / code 90309999)
    - chặn này có tính XÁC SUẤT (đã xác nhận qua test thật: có lần qua ngay lần 2, có lần
    vẫn bị chặn ở lần 2), không phải "chặn lần 1, chắc qua lần 2" như quan sát ban đầu từ
    tool gốc. Dùng _tls_session() (KHÔNG phải requests) - xem docstring hàm đó. Ký LẠI TỪ
    ĐẦU mỗi lần thử (không dùng lại header cũ) - header ký có thể gắn theo thời điểm/nonce,
    dùng lại header stale nhiều khả năng vẫn bị chặn."""
    body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    session = _tls_session(proxy)
    for attempt in range(_POST_ANTI_BOT_MAX_ATTEMPTS):
        signed_headers = sign_request(signing, url, body_str)
        headers = {
            **_base_headers(cookie_str, csrf_token, market_cfg, device, device_id, client_request_id),
            **signed_headers,
            "Content-Type": "application/json; charset=UTF-8",
        }
        resp = session.post(url, data=body_str.encode("utf-8"), headers=headers)
        is_json = _get_header(resp.headers, "Content-Type", "").startswith("application/json")
        blocked = resp.status_code == 418 or (is_json and resp.json().get("error") == 90309999)
        if not blocked or attempt == _POST_ANTI_BOT_MAX_ATTEMPTS - 1:
            return resp
        time.sleep(_POST_ANTI_BOT_RETRY_DELAY_SECONDS * (attempt + 1))
    return resp  # unreachable, giữ để type-checker hài lòng


def shopee_precheck(
    signing: SigningConfig, cookie_str: str, csrf_token: str, creator_id: str,
    market_cfg: dict, video_meta: dict, proxy: str | None = None, device: dict = DEFAULT_DEVICE,
    device_id: str = "", client_request_id: str = "",
) -> str:
    """Bước 4. Trả về 'extra_context' cần cho create_post(). video.url/video_id/cover để
    rỗng - đúng payload thật đã capture (Shopee chưa cần biết file thật ở bước precheck).

    device['client_version'] PHẢI khớp với field 'shopee_app_version' TRONG CHÍNH cookie
    (xem post_video_to_shopee() - tự suy device từ cookie thay vì dùng DEFAULT_DEVICE cứng).
    Đã xác nhận qua test thật (2026-09-10): lệch 2 giá trị này (vd cookie mang
    shopee_app_version=29627 nhưng app_info.app_version gửi 35943) khiến create() LUÔN trả
    '{"code":400003,"msg":"Post too many videos, please have a rest"}' bất kể đổi tài khoản/
    video/IP proxy - KHÔNG phải rate-limit thật, code gốc (biến SERVER1_APP_VERSION/
    FALLBACK_APP_VERSION) coi 400003 là tín hiệu "thử lại với app_version khác" chứ không
    phải quota (quota thật là code 400002, tool gốc check riêng)."""
    url = f"https://{market_cfg['sv']}/api/v2/biz/post/precheck"
    body = {
        "content": {
            "from_source": f"creator_id={creator_id}&pre_source=shopee_video&scene_code=my_profile_create_button",
            "caption": "",
            "video": {
                "url": "", "video_id": "", "cover": "",
                "width": video_meta["width"], "height": video_meta["height"],
                "size": 0, "duration": video_meta["duration"],
                "watermark_cover_url": "", "skip_cover_check": True, "is_ugc_cover": False,
            },
            "music": {
                "music_id": "", "type": 3, "title": "", "url": "", "original": True,
                "start": 0, "cover": "", "duration": 3000, "author_name": "", "soundtracks": [],
            },
            "mentions": [], "hashtags": [],
            "post_attr": {"share_to_friends": False},
            "content_source": 0, "images": [], "content_type": 0,
        },
        "app_info": {
            "system_os": "Android", "system_version": device["os_version"],
            "app_version": device["client_version"], "device_model": _app_info_device_model(device),
        },
    }
    resp = _post_signed(signing, url, body, cookie_str, csrf_token, market_cfg, proxy, device, device_id, client_request_id)
    if resp.status_code != 200:
        raise RuntimeError(f"Precheck thất bại (status={resp.status_code}): {resp.text[:300]!r}")
    data = resp.json()
    extra_context = (data.get("data") or {}).get("extra_context")
    if not extra_context:
        raise RuntimeError(f"Precheck không trả extra_context: {data!r}")
    return extra_context


def _create_post_body(
    creator_id: str, market_cfg: dict, video_meta: dict, vid: str, caption: str,
    products: list[dict], extra_context: str, fsize: int, device: dict,
) -> dict:
    return {
        "content": {
            "from_source": f"creator_id={creator_id}&pre_source=content_merge_tab",
            "caption": caption,
            "video": {
                "url": f"https://{market_cfg['video_cdn']}/{vid}.mp4", "video_id": vid, "cover": "",
                "width": video_meta["width"], "height": video_meta["height"],
                "size": fsize, "duration": video_meta["duration"],
                "watermark_cover_url": "", "skip_cover_check": True, "is_ugc_cover": True,
            },
            "music": {
                "music_id": "", "type": 3, "title": "", "url": "", "original": True,
                "start": 0, "cover": "", "duration": 3000, "author_name": "", "soundtracks": [],
            },
            "mentions": [], "hashtags": [],
            "products": products,
            "post_attr": {"share_to_friends": False},
            "content_source": 0, "images": [], "content_type": 0,
            "is_creator_claim_aigc": False,
            "extra_context": extra_context,
            "allow_info": {"allow_stitch": False, "allow_duet": False},
        },
        "app_info": {
            "system_os": "Android", "system_version": device["os_version"],
            "app_version": device["client_version"], "device_model": _app_info_device_model(device),
        },
        "media_sdk_info": {
            "camera": {"magic": [], "media_type": 2, "text": 0, "magic_type": [], "filter_id": []},
            "edit": [{"media_type": 2, "text": 0, "magic_type": [], "filter_id": []}],
            "effect_ids": [], "game_info": "", "cover_text_info": "", "game_magic_type": 1,
            "ug_reward_context_value": "", "use_product_clip": False,
        },
    }


def shopee_create_post(
    signing: SigningConfig, cookie_str: str, csrf_token: str, creator_id: str,
    market_cfg: dict, video_meta: dict, vid: str, caption: str,
    products: list[dict], extra_context: str, fsize: int = 0, proxy: str | None = None,
    device: dict = DEFAULT_DEVICE, device_id: str = "", client_request_id: str = "",
) -> str:
    """Bước 6. Trả về post_id nếu thành công, raise RuntimeError nếu vẫn thất bại sau khi thử
    cả 2 biến thể 'shopee_app_version'.

    ĐÚNG cơ chế code gốc (đọc từ source thật, xem _FALLBACK_APP_VERSION): thử biến thể
    'default' (device/cookie_str y như truyền vào) trước; nếu response trả
    `{"code":400003,"msg":"Post too many videos, please have a rest"}` thì thử NGAY (không
    delay) biến thể 'fallback' - device['client_version'] VÀ cookie's shopee_app_version cùng
    đổi sang _FALLBACK_APP_VERSION. 400003 ở đây là tín hiệu "app_version bị từ chối", KHÔNG
    PHẢI rate-limit thật (xem module docstring)."""
    url = (
        f"https://{market_cfg['sv']}/api/v2/biz/post/create"
        f"?os_type=2&system_version={device['os_version']}&sdk_version=1.61.2"
        f"&model={urllib.parse.quote(_url_query_model(device))}&android_performance=802"
    )
    variants = [
        ("default", device, cookie_str),
        ("fallback", {**device, "client_version": _FALLBACK_APP_VERSION}, _cookie_with_app_version(cookie_str, _FALLBACK_APP_VERSION)),
    ]
    last_data: dict = {}
    last_status = 0
    for label, variant_device, variant_cookie in variants:
        body = _create_post_body(creator_id, market_cfg, video_meta, vid, caption, products, extra_context, fsize, variant_device)
        resp = _post_signed(signing, url, body, variant_cookie, csrf_token, market_cfg, proxy, variant_device, device_id, client_request_id)
        data = resp.json() if _get_header(resp.headers, "Content-Type", "").startswith("application/json") else {}
        if resp.status_code == 200 and data.get("code") == 0:
            post_id = (data.get("data") or {}).get("post_id")
            if post_id:
                return post_id
            raise RuntimeError(f"Create post [{label}] 200 OK nhưng thiếu post_id: {data!r}")
        last_data, last_status = data, resp.status_code
        if data.get("code") != 400003:
            break  # lỗi khác 400003 (vd anti-bot hết retry) -> không thử biến thể app_version khác
    raise RuntimeError(f"Create post thất bại sau khi thử các biến thể app_version (status={last_status}): {last_data!r}")


def post_video_to_shopee(
    video_path: str, cookie_str: str, caption: str, merge_links: str,
    signing: SigningConfig, market: str = "th", proxy: str | None = None,
) -> PostResult:
    """Hàm tổng - chạy đủ 6 bước (preupload -> upload -> report -> đợi 10s -> precheck ->
    create) cho 1 video. Nhận thẳng (video_path, caption, merge_links) - xem
    post_videos_from_folder() để nạp hàng loạt từ thư mục video + file .xlsx thay vì gọi
    hàm này 1 mình cho từng video.

    proxy: dùng cho precheck/create - hữu ích để phân tán traffic khi chạy nhiều video/tài
    khoản cùng lúc - KHÔNG liên quan lỗi '400003 Post too many videos' (xem
    shopee_create_post())."""
    market_cfg = MARKET_CONFIG.get(market)
    if market_cfg is None:
        return PostResult(success=False, error=f"Market '{market}' chưa xác nhận cấu hình (xem MARKET_CONFIG)")

    cookie = parse_cookie(cookie_str)
    creator_id = cookie.get("SPC_U")
    csrf_token = cookie.get("csrftoken")
    if not creator_id or not csrf_token:
        return PostResult(success=False, error="Cookie thiếu SPC_U hoặc csrftoken")

    # Suy PRIMARY app_version từ CHÍNH cookie (field 'shopee_app_version') - dùng làm biến
    # thể 'default' trong shopee_create_post() (nhiều khả năng vẫn hợp lệ vì lấy từ phiên
    # đăng nhập thật); nếu create() trả 400003, shopee_create_post() TỰ đổi sang biến thể
    # 'fallback' (_FALLBACK_APP_VERSION) - đúng cơ chế 2 biến thể của code gốc, xem
    # _cookie_with_app_version().
    device = {**DEFAULT_DEVICE, "client_version": cookie.get("shopee_app_version", DEFAULT_DEVICE["client_version"])}
    # device_id: PHẢI dùng ĐÚNG giá trị TĨNH này - xác nhận qua đọc source code gốc (biến
    # `clientInfoHeader` lặp lại y hệt giá trị này ở MỌI flow S1/S2/S3, hard-code cứng trong
    # tool, KHÔNG sinh ngẫu nhiên/theo thiết bị thật).
    device_id = DEFAULT_DEVICE_ID
    # client_request_id: định dạng '<uuid>.<số thứ tự>' - đúng dạng thật đã capture
    # ("3e77a8a6-115f-4a04-b2c7-ebb8540940cb.347"), cố định trong suốt 1 lần đăng.
    client_request_id = f"{uuid.uuid4()}.{uuid.uuid4().int % 1000}"

    raw: dict = {}
    try:
        fsize = 0
        with open(video_path, "rb") as f:
            f.seek(0, 2)
            fsize = f.tell()
        md5 = file_md5(video_path)
        video_meta = get_video_metadata(video_path)

        preupload_data = vod_preupload(cookie_str, csrf_token, creator_id, market_cfg, fsize, md5, device, device_id, client_request_id)
        vid = preupload_data["vid"]
        raw["preupload"] = preupload_data

        upload_token = get_upload_token(signing)
        upload_video_wscloud(market_cfg, upload_token, vid, video_path)

        report_data = report_upload_wscloud(cookie_str, csrf_token, market_cfg, market, vid, fsize, video_meta, device, device_id, client_request_id)
        raw["report"] = report_data
        time.sleep(_AFTER_REPORT_WAIT_SECONDS)

        products = build_products_field(merge_links)
        extra_context = shopee_precheck(signing, cookie_str, csrf_token, creator_id, market_cfg, video_meta, proxy, device, device_id, client_request_id)
        post_id = shopee_create_post(
            signing, cookie_str, csrf_token, creator_id, market_cfg, video_meta,
            vid, caption, products, extra_context, fsize=fsize, proxy=proxy,
            device=device, device_id=device_id, client_request_id=client_request_id,
        )
        return PostResult(success=True, post_id=post_id, vid=vid, raw_responses=raw)
    except Exception as exc:  # noqa: BLE001 - lỗi 1 video phải trả về trong PostResult, không được crash cả batch (post_videos_from_folder cần chạy tiếp các video còn lại)
        return PostResult(success=False, error=str(exc), raw_responses=raw)


def post_videos_from_folder(
    folder: str, cookie_str: str, signing: SigningConfig, market: str = "th",
    db_path: str = shopee_db.DB_PATH_DEFAULT, skip_already_posted: bool = True,
    limit: int | None = None, proxies: list[str] | None = None,
    min_delay_seconds: float = 8.0, max_delay_seconds: float = 20.0,
) -> list[tuple[ProductRow, PostResult]]:
    """Nạp dữ liệu THEO CÁCH RIÊNG của tool này (khác Chill 68 - không dùng Google Sheet làm
    hàng đợi): người dùng cung cấp 1 thư mục chứa file video `<sp_id>.mp4` + đúng 1 file
    `*_results.xlsx` trong CHÍNH thư mục đó. Cột cố định trong xlsx: A=tên video (sp_id,
    không kèm đuôi .mp4), B=tên sản phẩm (dùng làm caption), P=link tiếp thị (merge_links,
    nhiều link nối bằng '|' -> field 'products' của create_post). Xem
    gsheet_video_scanner.build_matched_pool() - chỉ những SP ID VỪA có file .mp4 trong thư
    mục VỪA có dòng tương ứng trong xlsx mới được đăng (giao của 2 tập hợp).

    Chạy TUẦN TỰ (không song song) - mỗi video tốn ít nhất _AFTER_REPORT_WAIT_SECONDS (10s)
    chờ Shopee xử lý, cộng thời gian upload thật; số lượng lớn nên tự chia nhỏ/chạy nhiều
    tiến trình ở tầng gọi nếu cần tăng tốc, hàm này không tự làm việc đó.

    skip_already_posted=True (mặc định): bỏ qua SP ID đã có log THÀNH CÔNG cho đúng
    (market, folder) này trước đó (xem shopee_db.already_posted()) - tránh tốn 1 lượt gọi
    server ký + upload thật nếu chạy lại cùng thư mục. Mỗi video (thành công lẫn thất bại)
    đều được ghi vào bảng video_post_log NGAY sau khi xử lý xong (không đợi hết batch), xem
    shopee_db.log_video_post().

    limit: chỉ XÉT tối đa `limit` video đầu (theo thứ tự trong build_matched_pool()) - áp
    dụng TRƯỚC skip_already_posted, nên "--limit 1" mà video đó đã đăng thành công trước đó
    sẽ trả về danh sách rỗng (không tự "bù" sang video thứ 2). None = xét hết.

    proxies: danh sách URL proxy (parse_proxy() từng dòng của proxy.txt) - xoay vòng
    round-robin, MỖI VIDEO 1 proxy khác (video thứ N dùng proxies[N % len(proxies)]) - chỉ để
    phân tán traffic, KHÔNG liên quan lỗi '400003 Post too many videos' (xem
    shopee_create_post()). None = không dùng proxy (IP thật của máy chạy script).

    min_delay_seconds/max_delay_seconds: nghỉ NGẪU NHIÊN (uniform, tránh nhịp đều đặn dễ nhận
    diện) trong khoảng này TRƯỚC MỖI video (trừ video đầu tiên) - CHỈ để tạo nhịp đăng giống
    người dùng thật (không dồn dập), KHÔNG liên quan lỗi '400003' (đã xác nhận nguyên nhân
    thật là 'shopee_app_version', xem shopee_create_post()). Đặt cả 2 = 0 để tắt hẳn (đăng
    liên tục không nghỉ). Video bị skip (đã đăng thành công trước đó) KHÔNG tính vào nhịp nghỉ
    này (không tốn thời gian chờ cho video không thực sự gọi Shopee).

    Trả về danh sách (ProductRow, PostResult) song song - CHỈ gồm các video thực sự được xử
    lý trong lần gọi này (video bị skip do đã đăng trước đó không có trong kết quả trả về;
    tra lịch sử đầy đủ qua shopee_db.list_video_post_log())."""
    shopee_db.init_db(db_path)
    folder_path = Path(folder)
    rows = build_matched_pool(folder_path)
    if limit is not None:
        rows = rows[:limit]
    results: list[tuple[ProductRow, PostResult]] = []
    processed = 0
    for row in rows:
        if skip_already_posted and shopee_db.already_posted(db_path, row.sp_id, market, folder):
            continue
        if processed > 0 and max_delay_seconds > 0:
            delay = random.uniform(min_delay_seconds, max_delay_seconds)
            time.sleep(delay)
        proxy = proxies[processed % len(proxies)] if proxies else None
        processed += 1
        video_path = folder_path / f"{row.sp_id}.mp4"
        result = post_video_to_shopee(
            video_path=str(video_path),
            cookie_str=cookie_str,
            caption=row.product_name,
            merge_links=row.merge_links,
            signing=signing,
            market=market,
            proxy=proxy,
        )
        shopee_db.log_video_post(
            db_path, sp_id=row.sp_id, market=market, folder=folder,
            product_name=row.product_name, merge_links=row.merge_links,
            success=result.success, post_id=result.post_id, vid=result.vid, error=result.error,
        )
        results.append((row, result))
    return results

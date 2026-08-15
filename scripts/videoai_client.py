"""Goi thang toi videoai-api.devappnow.com (KHONG qua Postgres cua 0-database/2-tao-video)
de day du lieu cache + tao video, dung cho tinh nang "Tao video" cua dashboard nay.

Endpoint da xac nhan qua doc code that (D:\\Shopee369\\1-cao, D:\\Shopee369\\2-tao-video) +
test that bang API key cua nguoi dung (2026-08-06):
    POST /api/products/shopee-cache/batch   {items: CacheItem[]}  -> {upserted, skipped, errors?}
    POST /api/v1/shopee-video/batch         {urls, language, tag, Pool}
                                             -> {items: [{url, jobId, status} | {url, error}]}

Ghi chu quan trong da xac nhan qua test that (dung lai se sai):
    - KHONG can field 'category'/'categories' - du lieu du an nay khong co category, bo qua
      hoan toan (nguoi dung xac nhan). Test that gui khong co field nay van thanh cong.
    - Field Pool viet HOA chu P (khac 'pool' thuong trong 2-tao-video/lib/videoai.ts) - test
      that xac nhan 'Pool' hoa moi dung.
    - BAT BUOC co header User-Agent giong trinh duyet that - Cloudflare chan thang request
      khong co UA nay (403 error code 1010), da gap that khi test bang urllib mac dinh.
    - price trong DB dang luu RAW (batch_item_for_item_card_full.price) - phai chia /100000
      moi ra dung don vi tien te thuc (VD 489900000 -> 4899.0).
    - images trong DB dang luu HASH tho (json array) - phai build lai thanh URL day du:
      https://down-{market}.img.susercontent.com/file/{hash} - prefix theo market (da xac
      nhan dung voi vn/sg/ph; CHUA xac nhan voi th/my - CDN prefix that su co the khac,
      can test that truoc khi day video hang loat cho 2 market nay).

CHU Y (2026-08-12): market_from_link()/cot 'market' trong DB TUNG khong dang tin (moi
insert path deu bo qua, DB mac dinh 'vn' cho MOI market) - da fix o shopee_db.py (moi
insert path gio deu tu suy dung market tu URL). Module nay gio tai dung
shopee_db.market_from_link() lam nguon chan ly DUY NHAT thay vi tu giu 1 ban regex rieng
(truoc day chi co vn/sg/ph, thieu th/my/tw/id/cl/br/mx/co).
"""
import json

import requests

import shopee_db

BASE_URL = "https://videoai-api.devappnow.com"
CACHE_ENDPOINT = f"{BASE_URL}/api/products/shopee-cache/batch"
TASK_ENDPOINT = f"{BASE_URL}/api/v1/shopee-video/batch"
BATCH_LIMIT = 200  # gioi han cua chinh VideoAI cho moi lan goi batch

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# vi/fil/en/th/id/ms da xac nhan la ma ngon ngu HOP LE cua VideoAI (nguoi dung cung cap
# 2026-08-14). vn->vi, ph->fil, th->th, id->id, my->ms map truc tiep theo market (luu y:
# ma ngon ngu Malay la 'ms' theo chuan ISO 639-1, KHAC voi ma market 'my' - 'my' la ma
# QUOC GIA Malaysia, khong phai ma ngon ngu). sg->en (Singapore dung tieng Anh).
# tw/cl/br/mx/co KHONG co ma rieng trong danh sach nay (Trung/Tay Ban Nha/Bo Dao Nha deu
# khong duoc VideoAI ho tro) - tam fallback 'en' (mac dinh cua .get()) cho toi khi co xac
# nhan khac.
_LANG_BY_MARKET = {"vn": "vi", "ph": "fil", "sg": "en", "th": "th", "id": "id", "my": "ms"}

market_from_link = shopee_db.market_from_link


def language_for_market(market):
    return _LANG_BY_MARKET.get(market, "en")


def build_cache_item(row, market=None):
    """Map 1 dong 'products' (dict) sang CacheItem cua VideoAI. Tra ve None neu thieu du
    lieu bat buoc (url/title) - nguoi goi tu quyet dinh bo qua/bao loi dong do."""
    url = row.get("product_link")
    title = row.get("name")
    if not url or not title:
        return None
    if market is None:
        market = market_from_link(url)

    price_raw = row.get("price")
    price = round(price_raw / 100000, 2) if price_raw not in (None, "") else 0

    rating_raw = row.get("rating_star")
    rating = round(float(rating_raw), 2) if rating_raw not in (None, "") else 0

    sold = row.get("historical_sold")
    if sold in (None, ""):
        sold = row.get("sold") or 0

    images = []
    images_raw = row.get("images")
    if images_raw:
        try:
            hashes = json.loads(images_raw)
        except (TypeError, ValueError):
            hashes = []
        prefix = f"down-{market}"
        images = [f"https://{prefix}.img.susercontent.com/file/{h}" for h in hashes if h]

    return {
        "url": url,
        "title": title,
        "price": price,
        "rating": rating,
        "soldCount": int(sold or 0),
        "stock": int(row.get("stock") or 0),
        "shopName": row.get("shop_name") or "",
        "images": images,
        "reviewCount": int(row.get("review_count") or 0),
        "ratingCount": int(row.get("rating_count") or 0),
    }


def _headers(api_key):
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": USER_AGENT,
    }


def push_cache_batch(items, api_key):
    """Day toi da BATCH_LIMIT item/lan. Tra ve {upserted, skipped, errors: [{url, reason}]}."""
    if not items:
        return {"upserted": 0, "skipped": 0, "errors": []}
    if len(items) > BATCH_LIMIT:
        raise ValueError(f"toi da {BATCH_LIMIT} item/lan goi (dang gui {len(items)})")
    resp = requests.post(
        CACHE_ENDPOINT, json={"items": items}, headers=_headers(api_key), timeout=30
    )
    if resp.status_code == 401:
        raise RuntimeError("VideoAI API key khong hop le (401)")
    resp.raise_for_status()
    return resp.json()


def _url_entry(url, merged_link):
    """1 phan tu cua mang 'urls' trong request tao task - LUON gui dang object {url,
    mergeLinks}, KE CA khi nhom chi co 1 link (khong co gi de gop) - de cot 'Merge Links'
    trong _results.xlsx cua VideoAI khong bao gio bi de trong (yeu cau nguoi dung,
    2026-08-16). mergeLinks gui dang STRING noi bang '|' (vd 'url1|url2|url3'), giong y
    dinh dang cot merged_link ben shopee_db.py (xem compute_merged_links(), toi da 6
    link/nhom KE CA link chinh) - da test batch that (20 san pham, tag 'TEST', 2026-08-16):
    Job Status Done 20/20, cot Merge Links tra ve dung format + khop 100% voi merged_link
    trong DB, video tao ra binh thuong (kich thuoc cung range voi batch cu dung dang
    object) - xem chi tiet trong lich su chat. Neu merged_link rong (chua tinh duoc, hiem)
    thi fallback dung chinh 'url'."""
    links = [l for l in (merged_link or "").split("|") if l] or [url]
    return {"url": url, "mergeLinks": "|".join(links)}


def create_video_batch(url_items, api_key, tag, pool, language):
    """Tao task video cho toi da BATCH_LIMIT url/lan. url_items: list [{url, merged_link}]
    (merged_link co the None/rong - dong do se fallback dung 'url' lam mergeLinks.link1, xem
    _url_entry() - luon gui dang object {url, mergeLinks}). Tra ve list [{url, jobId,
    status} hoac {url, error}] - 'url' trong response luon la link goc (API tu doc lai key
    'url' ben trong object)."""
    if not url_items:
        return []
    if len(url_items) > BATCH_LIMIT:
        raise ValueError(f"toi da {BATCH_LIMIT} url/lan goi (dang gui {len(url_items)})")
    urls = [_url_entry(it["url"], it.get("merged_link")) for it in url_items]
    body = {"urls": urls, "language": language, "tag": tag, "Pool": pool}
    resp = requests.post(TASK_ENDPOINT, json=body, headers=_headers(api_key), timeout=60)
    if resp.status_code == 401:
        raise RuntimeError("VideoAI API key khong hop le (401)")
    resp.raise_for_status()
    return resp.json().get("items") or []

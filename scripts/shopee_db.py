"""SQLite rieng cua du an nay (KHONG phai Postgres chung o D:\\Shopee369\\0-database).
Bang 'products' lay dung khung cot da xac nhan tu
D:\\Shopee369\\0-database\\migrate\\schema.sql, cong them cot moi
'affiliate_promoted_last_7days' (da chot voi nguoi dung). Vi la DB rieng nen khong bi
rang buoc boi script drop-table-cascade cua 0-database.

Dung de:
    - Kiem tra itemid da co trong DB chua (item_exists) truoc khi chay lai UI automation.
    - Luu ket qua sau khi merge_and_export_offer_result() thanh cong (upsert_item).

Chay truc tiep de test nhanh:
    python scripts/shopee_db.py
"""
import json
import os
import re
import sqlite3

DB_PATH_DEFAULT = "artifacts/db/shopee.db"

# Dung thu tu, dung ten cot voi D:\Shopee369\0-database\migrate\schema.sql (bang
# products) + 1 cot moi affiliate_promoted_last_7days o cuoi.
COLUMNS = [
    "itemid", "shopid", "name", "price", "sold", "product_link",
    "default_commission", "seller_commission", "shopee_commission", "groupid",
    "images", "stock", "rating_star", "rating_count", "ctime", "link_type",
    "merged_link", "historical_sold", "shop_name", "description", "status_link",
    "created_at", "market", "category_group", "original_price", "review_count",
    "assigned_key", "claimed_at", "job_id", "cache_uploaded",
    "affiliate_promoted_last_7days", "xtra", "fail_reason",
]

CREATE_DEVICES_TABLE_SQL = """
create table if not exists devices (
    id integer primary key autoincrement,
    name text,
    serial text unique,
    created_at timestamp default current_timestamp
);
"""

# 1 dong duy nhat (id=1) - nguong loc dieu chinh duoc qua UI, thay cho hang so tinh trong
# select_l1_l2_candidates.py. Xem get_settings()/update_settings().
CREATE_SETTINGS_TABLE_SQL = """
create table if not exists settings (
    id integer primary key,
    promoted_7d_max integer,
    sold_min integer,
    seller_commission_vnd_min integer,
    auto_assign integer default 0
);
"""

# Nhieu may/tai khoan VideoAI khac nhau (moi may 1 API key rieng, thu muc/pool rieng) -
# dung cho tinh nang "Tao video" (day thang qua videoai-api.devappnow.com, xem
# videoai_client.py). Nguoi dung chon 1 may khi chay day hang loat (POST /api/videos/push).
CREATE_VIDEO_MACHINES_TABLE_SQL = """
create table if not exists video_machines (
    id integer primary key autoincrement,
    name text not null,
    api_key text not null,
    tag text not null,
    pool text default 'selfhostPool',
    enabled integer default 1,
    created_at timestamp default current_timestamp
);
"""

# Mail mua tu dongvanfb dung de dang ky tai khoan Shopee (tab "Tao tai khoan Shopee") - xem
# dongvanfb_client.py. full_info luu NGUYEN dong "email|password|refresh_token|client_id"
# dongvanfb tra ve (yeu cau cua nguoi dung: giu du lieu goc), cac cot email/password tach
# san de hien thi/tim kiem nhanh khong can parse lai moi lan doc. shopee_id/device/market
# la du lieu NGUOI DUNG tu nhap/chon (khong tu dong dien) - market CHI la nhan de nguoi
# dung tu phan loai, khong anh huong logic lay code (filter theo domain "shopee" da tong
# quat cho moi thi truong, xac nhan qua test that voi mail Shopee Thai Lan).
CREATE_MAIL_ACCOUNTS_TABLE_SQL = """
create table if not exists mail_accounts (
    id integer primary key autoincrement,
    full_info text not null,
    email text not null,
    password text,
    refresh_token text,
    client_id text,
    account_type text,
    order_code text,
    shopee_id text default '',
    device text default '',
    slot text default '',
    market text default 'PH',
    shopee_code text,
    checked_at timestamp,
    created_at timestamp default current_timestamp
);
"""

# Trang thai "song" cua tung tab Tampermonkey (khong phai hang doi viec - hang doi viec
# van dung lai cot assigned_key/claimed_at co san tren 'products', xem
# assign_root_to_worker()). Bang nay CHI de dashboard hien thi tab nao dang ranh/lam
# viec/bi chan, khong anh huong logic gan viec.
CREATE_WORKERS_TABLE_SQL = """
create table if not exists workers (
    device_key text primary key,
    status text,
    current_root text,
    last_heartbeat timestamp
);
"""

CREATE_TABLE_SQL = """
create table if not exists products (
    id integer primary key autoincrement,
    itemid text unique,
    shopid text,
    name text,
    price integer,
    sold integer,
    product_link text,
    default_commission integer,
    seller_commission integer,
    shopee_commission integer,
    groupid text,
    images text,
    stock integer,
    rating_star real,
    rating_count integer,
    ctime text,
    link_type text,
    merged_link text,
    historical_sold integer,
    shop_name text,
    description text,
    status_link text default 'pending',
    created_at timestamp default current_timestamp,
    market text default 'vn',
    category_group text,
    original_price integer,
    review_count integer,
    assigned_key text,
    claimed_at timestamp,
    job_id text,
    cache_uploaded integer default 0,
    affiliate_promoted_last_7days integer,
    xtra integer,
    fail_reason text
);
"""


def init_db(db_path=DB_PATH_DEFAULT):
    dirname = os.path.dirname(db_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    conn = sqlite3.connect(db_path)
    # WAL: doc KHONG bi chan boi ghi (va nguoc lai) - quan trong vi nhieu worker BFS
    # (Tampermonkey) ghi song song trong luc dashboard doc lien tuc (stats/roots/workers
    # poll moi 5s). Journal mode mac dinh ("delete") khoa CA FILE moi khi co 1 giao dich
    # ghi, khien MOI thao tac dashboard (ke ca doc) phai cho toi 5s (default busy timeout
    # cua module sqlite3) neu dung luc worker dang ghi - day la nguyen nhan chinh gay cam
    # giac UI "cham, click lau moi thay" nguoi dung bao cao. WAL la thuoc tinh luu trong
    # chinh file .db, chi can set 1 lan (khong can lap lai moi lan ket noi sau).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(CREATE_TABLE_SQL)
    # Migration L1/L2 cu -> root/related: CHI chay 1 LAN (guard qua PRAGMA user_version).
    # TRUOC DAY chay lai KHONG DIEU KIEN moi lan goi init_db() - vi hau het ham trong file
    # nay dung init_db() lam ham "ket noi" thong thuong, nghia la MOI thao tac dashboard
    # (ke ca doc don gian nhu xem danh sach root) deu quet toan bang 'products'
    # (~90ms/lan tren 125k dong, do dac thuc te) VA tu bien minh thanh 1 giao dich GHI toan
    # file - tranh khoa khong can thiet voi worker BFS dang ghi that. Da xac nhan day la
    # nguyen nhan chinh gay cham UI. Cac ham khac trong file nay gio dung _connect() (ket
    # noi nhe, khong chay lai migration) thay vi init_db().
    if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        conn.execute("update products set link_type = 'root' where link_type = 'L1'")
        conn.execute("update products set link_type = 'related' where link_type = 'L2'")
        conn.execute("PRAGMA user_version = 1")
    # Cot 'xtra' them sau - DB cu (tao truoc khi co dong nay trong CREATE_TABLE_SQL) se
    # khong tu co cot, phai ALTER TABLE rieng. SQLite khong ho tro "ADD COLUMN IF NOT
    # EXISTS" nen phai tu kiem tra qua pragma table_info truoc.
    existing_cols = {row[1] for row in conn.execute("pragma table_info(products)").fetchall()}
    if "xtra" not in existing_cols:
        conn.execute("alter table products add column xtra integer")
    if "fail_reason" not in existing_cols:
        conn.execute("alter table products add column fail_reason text")
    conn.execute(
        "create index if not exists idx_products_claim "
        "on products(status_link, affiliate_promoted_last_7days, sold)"
    )
    # Thieu index tren groupid la ly do rieng khien /api/roots/list (list_roots_with_counts,
    # dem 'member' cho TUNG root) cham nang - moi truy van "where groupid=? and
    # status_link='member'" phai QUET toan bo cac dong 'member' (hang chuc nghin dong)
    # thay vi seek truc tiep. Da do dac thuc te: 18s cho 1 lan goi /api/roots/list (200
    # root x quet ~10k dong) truoc khi co index nay. Cung giup count_group_members(),
    # list_group_members(), compute_merged_links(), try_assign_verified()... deu dung
    # groupid lam dieu kien loc.
    conn.execute(
        "create index if not exists idx_products_group_status on products(groupid, status_link)"
    )
    # count_status() (dung cho /api/stats, dashboard poll moi 5s LIEN TUC) loc theo
    # link_type+status_link nhung khong co index nao khop ca 2 cot - phai quet ca chuc
    # nghin dong 'related'/'pending' de dem. Do dac thuc te: /api/stats ~1.16s/lan (x2 lien
    # tuc moi 5s la tai khong can thiet, cong don voi cac may khac dang doc/ghi).
    conn.execute(
        "create index if not exists idx_products_type_status on products(link_type, status_link)"
    )
    # count_video_push_stats()/list_video_push_candidates() (tab "Tao video") loc theo
    # merged_link is not null - khong co index rieng, phai quet toan bang.
    conn.execute(
        "create index if not exists idx_products_merged_link on products(merged_link) "
        "where merged_link is not null"
    )
    conn.execute(CREATE_DEVICES_TABLE_SQL)
    conn.execute(CREATE_SETTINGS_TABLE_SQL)
    conn.execute(CREATE_WORKERS_TABLE_SQL)
    # Cot 'auto_assign' them sau - DB cu (bang settings tao truoc khi co dong nay) can
    # ALTER TABLE rieng, cung ly do nhu xtra/fail_reason o tren.
    existing_settings_cols = {row[1] for row in conn.execute("pragma table_info(settings)").fetchall()}
    if "auto_assign" not in existing_settings_cols:
        conn.execute("alter table settings add column auto_assign integer default 0")
    if "dongvanfb_api_key" not in existing_settings_cols:
        conn.execute("alter table settings add column dongvanfb_api_key text default ''")
    conn.execute(CREATE_VIDEO_MACHINES_TABLE_SQL)
    conn.execute(CREATE_MAIL_ACCOUNTS_TABLE_SQL)
    # Cot 'slot' them sau - DB cu (tao truoc khi co dong nay trong
    # CREATE_MAIL_ACCOUNTS_TABLE_SQL) can ALTER TABLE rieng, cung ly do nhu xtra/fail_reason.
    existing_mail_cols = {row[1] for row in conn.execute("pragma table_info(mail_accounts)").fetchall()}
    if "slot" not in existing_mail_cols:
        conn.execute("alter table mail_accounts add column slot text default ''")
    conn.execute(
        "create index if not exists idx_mail_accounts_market on mail_accounts(market)"
    )
    conn.commit()
    return conn


def _connect(db_path=DB_PATH_DEFAULT):
    """Ket noi NHE cho cac ham truy van/ghi thong thuong - KHONG chay lai migration/tao
    bang (da chay 1 lan qua init_db() luc server khoi dong, xem main()). Dung thay
    init_db() trong moi ham noi bo cua module nay de tranh quet toan bang + giao dich ghi
    thua o MOI request (xem giai thich chi tiet trong init_db()). Script doc lap (khong
    qua server) van nen tu goi init_db() truoc neu DB co the chua ton tai."""
    return sqlite3.connect(db_path)


# --- Nguong loc dieu chinh duoc qua UI (thay hang so tinh select_l1_l2_candidates.py) ---

def get_settings(db_path=DB_PATH_DEFAULT):
    """Doc nguong loc hien tai. Lan dau chua co dong nao trong bang 'settings' thi tu tao
    voi gia tri mac dinh lay tu select_l1_l2_candidates.py."""
    import select_l1_l2_candidates as l1l2
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from settings where id=1").fetchone()
        if row is None:
            conn.execute(
                "insert into settings (id, promoted_7d_max, sold_min, seller_commission_vnd_min) "
                "values (1, ?, ?, ?)",
                (l1l2.PROMOTED_7D_MAX_DEFAULT, l1l2.SOLD_MIN_DEFAULT, l1l2.SELLER_COMMISSION_VND_MIN_DEFAULT),
            )
            conn.commit()
            row = conn.execute("select * from settings where id=1").fetchone()
        return dict(row)
    finally:
        conn.close()


def update_settings(db_path=DB_PATH_DEFAULT, promoted_7d_max=None, sold_min=None,
                     seller_commission_vnd_min=None, auto_assign=None, dongvanfb_api_key=None):
    """Cap nhat MOT PHAN nguong loc (tham so None = giu nguyen gia tri cu). Tra ve settings
    day du sau khi cap nhat - dung cho endpoint POST /api/settings. dongvanfb_api_key dung
    chung cho tab "Tao tai khoan Shopee" (mua mail + lay code) - luu chung 1 dong settings
    nay thay vi bang rieng vi chi co DUY NHAT 1 key toan cuc, khong nhieu nhu video_machines."""
    current = get_settings(db_path)
    new_vals = {
        "promoted_7d_max": promoted_7d_max if promoted_7d_max is not None else current["promoted_7d_max"],
        "sold_min": sold_min if sold_min is not None else current["sold_min"],
        "dongvanfb_api_key": dongvanfb_api_key if dongvanfb_api_key is not None else current["dongvanfb_api_key"],
        "seller_commission_vnd_min": (
            seller_commission_vnd_min if seller_commission_vnd_min is not None
            else current["seller_commission_vnd_min"]
        ),
        "auto_assign": int(bool(auto_assign)) if auto_assign is not None else current["auto_assign"],
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            "update settings set promoted_7d_max=?, sold_min=?, seller_commission_vnd_min=?, "
            "auto_assign=?, dongvanfb_api_key=? where id=1",
            (new_vals["promoted_7d_max"], new_vals["sold_min"], new_vals["seller_commission_vnd_min"],
             new_vals["auto_assign"], new_vals["dongvanfb_api_key"]),
        )
        conn.commit()
    finally:
        conn.close()
    return get_settings(db_path)


# --- Quan ly danh sach dien thoai (ten hien thi + serial ADB) - dung cho UI Streamlit
# chon 1/nhieu/tat ca dien thoai de chay cung luc. serial la khoa that su dung lam
# device_key cho claim_pending()/u2.connect() - name chi la nhan hien thi, khong anh
# huong logic. ---

def add_device(db_path, name, serial):
    """Them 1 dien thoai moi, hoac DOI TEN neu serial da ton tai (ON CONFLICT tren
    cot 'serial' - unique)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into devices (name, serial) values (?, ?) "
            "on conflict(serial) do update set name = excluded.name",
            (name, serial),
        )
        conn.commit()
    finally:
        conn.close()


def list_devices(db_path):
    """Danh sach dien thoai da dang ky, sap theo ten."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select * from devices order by name collate nocase asc").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def remove_device(db_path, serial):
    """Xoa 1 dien thoai khoi danh sach dang ky (KHONG anh huong toi cac dong 'products'
    da claim boi serial nay truoc do - assigned_key la du lieu lich su, van giu nguyen)."""
    conn = _connect(db_path)
    try:
        conn.execute("delete from devices where serial = ?", (serial,))
        conn.commit()
    finally:
        conn.close()


def item_exists(db_path, itemid):
    conn = _connect(db_path)
    try:
        row = conn.execute("select 1 from products where itemid = ?", (str(itemid),)).fetchone()
        return row is not None
    finally:
        conn.close()


def delete_item(db_path, itemid):
    """Xoa 1 dong bat ky (root hoac related) - dung cho tab quan ly san pham tren UI khi
    nguoi dung muon bo 1 item cu the (vd nham/khong con phu hop) ra khoi DB. Neu xoa 1
    ROOT thi cac 'related' cua no (groupid=itemid) VAN GIU NGUYEN (khong cascade) - tranh
    xoa nham hang loat, nguoi dung tu xoa them neu thuc su muon don sach ca nhom."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("delete from products where itemid=?", (str(itemid),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _parse_money_amount(text):
    """Doc so tien tu chuoi hien thi Shopee - KHAC nhau theo thi truong nen KHONG THE chi
    strip het ky tu khong phai so nhu truoc (bug thuc te da gap voi PH): VND dung dau '.'
    lam phan cach hang nghin, khong co phan thap phan (vd '₫3.135' -> 3135). PHP (va nhieu
    thi truong khac) dung dau '.' lam dau THAP PHAN THAT (vd '₱13.86' -> 13.86, KHONG
    PHAI 1386). Phan biet bang so chu so SAU dau '.' CUOI CUNG trong chuoi: dung 2 chu so
    -> coi la thap phan that, giu nguyen (tra ve float); khac 2 (thuong la boi so 3, kieu
    '.135', '.000') -> coi la phan cach hang nghin, bo dau cham (tra ve int)."""
    if not text:
        return None
    s = re.sub(r"[^\d.]", "", str(text))
    if not s:
        return None
    last_dot = s.rfind(".")
    if last_dot == -1:
        return int(s)
    after = s[last_dot + 1:]
    if len(after) == 2:
        try:
            return float(s)
        except ValueError:
            return None
    digits = s.replace(".", "")
    return int(digits) if digits else None


def map_v2_data_to_row(data, link_type=None, groupid=None, status_link="pending", market="vn"):
    """Map tu response_json['data'] cua API offer/product_v2 sang dict dung ten cot
    trong bang 'products'. link_type ('root'/'related') va groupid (itemid cua root lien
    quan) KHONG the suy ra tu chinh du lieu cua item - nguoi goi phai tu truyen vao.

    Vai cot chua ro ngu nghia/don vi thuc te ben du an 0-database (merged_link,
    category_group, don vi price) - de None/best-effort thay vi doan bua, ghi chu ro
    de dieu chinh sau khi doi chieu voi 1-cao."""
    batch = data.get("batch_item_for_item_card_full") or {}
    commission_rate = data.get("commission_rate") or {}
    item_rating = batch.get("item_rating") or {}
    rating_count_list = item_rating.get("rating_count") or []

    images = batch.get("images")

    return {
        "itemid": str(data.get("item_id") or batch.get("itemid") or ""),
        "shopid": batch.get("shopid"),
        "name": batch.get("name"),
        "price": batch.get("price"),  # TODO: xac nhan lai don vi/scale voi 1-cao
        "sold": batch.get("sold"),
        "product_link": data.get("product_link"),
        "default_commission": _parse_money_amount(commission_rate.get("default_commission")),
        "seller_commission": _parse_money_amount(commission_rate.get("seller_commission")),
        "shopee_commission": _parse_money_amount(commission_rate.get("shopee_commission")),
        "groupid": groupid,
        "images": json.dumps(images, ensure_ascii=False) if images else None,
        "stock": batch.get("stock"),
        "rating_star": item_rating.get("rating_star"),
        "rating_count": sum(rating_count_list) if rating_count_list else None,
        "ctime": str(batch.get("ctime")) if batch.get("ctime") is not None else None,
        "link_type": link_type,
        "merged_link": None,  # chua ro ngu nghia cot nay ben 0-database
        "historical_sold": batch.get("historical_sold"),
        "shop_name": batch.get("shop_name"),
        "description": None,  # khong co trong response da capture
        "status_link": status_link,
        "market": market,
        "category_group": None,  # batch.catid la ma so, khac ngu nghia "nhom danh muc" dang text
        "original_price": batch.get("price_before_discount"),
        "review_count": batch.get("cmt_count"),
        "assigned_key": None,
        "claimed_at": None,
        "job_id": None,
        "cache_uploaded": 0,
        "affiliate_promoted_last_7days": int(data.get("affiliate_promoted_last_7days") or 0),
        # Cao duoc toi day (goi product_v2 thanh cong) nghia la da di qua dung man hinh
        # "Hoa hong doi tac" - item nay chac chan CO duoc bat Hoa Hong Xtra.
        "xtra": 1,
    }


def upsert_item(db_path, row: dict):
    """INSERT OR REPLACE 1 dong vao bang products theo itemid (unique)."""
    conn = _connect(db_path)
    try:
        cols = [c for c in COLUMNS if c in row]
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        values = [row[c] for c in cols]
        conn.execute(
            f"insert into products ({col_list}) values ({placeholders}) "
            f"on conflict(itemid) do update set "
            + ", ".join(f"{c}=excluded.{c}" for c in cols if c != "itemid"),
            values,
        )
        conn.commit()
    finally:
        conn.close()


def clear_all_items(db_path=DB_PATH_DEFAULT):
    """Xoa TOAN BO du lieu san pham (root + related, moi trang thai) - dung khi nguoi
    dung bam "Xoa toan bo du lieu" tren UI de lam sach test/chay lai tu dau. KHONG dong
    bang 'devices' (tai khoan/profile la cau hinh, khong phai du lieu cao duoc). Tra ve so
    dong da xoa."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("delete from products")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def count_items(db_path=DB_PATH_DEFAULT):
    conn = _connect(db_path)
    try:
        return conn.execute("select count(*) from products").fetchone()[0]
    finally:
        conn.close()


def get_item(db_path=DB_PATH_DEFAULT, itemid=None):
    """Lay dung 1 dong theo itemid (khop chinh xac, khong phai LIKE nhu fetch_all_items) -
    dung de kiem tra trang thai 1 item NGAY sau khi cao/cham diem xong."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from products where itemid = ?", (str(itemid),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_all_items(db_path=DB_PATH_DEFAULT, link_type=None, status_link=None, search=None, limit=500):
    """Doc lai danh sach item da luu trong DB - dung cho tab 'Xem DB' cua Streamlit UI.
    link_type: loc theo 'root'/'related' (None/'' = tat ca). status_link: loc theo
    'pending'/'done'/'fail' (None/'' = tat ca). search: loc itemid/name/shop_name co chua
    chuoi nay (khong phan biet hoa thuong). Dung tham so hoa (?) cho ca gia tri loc lan
    LIMIT - khong noi chuoi nguoi dung truc tiep vao cau SQL (tranh SQL injection)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where = []
        params = []
        if link_type:
            where.append("link_type = ?")
            params.append(link_type)
        if status_link:
            where.append("status_link = ?")
            params.append(status_link)
        if search:
            where.append("(itemid LIKE ? OR name LIKE ? OR shop_name LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where_sql = f"where {' and '.join(where)}" if where else ""
        params.append(limit)
        rows = conn.execute(
            f"select * from products {where_sql} order by id desc limit ?", params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --- Hang doi (queue) phang pending/done/fail - xem C:\Users\Administrator\.claude\
# plans\happy-sleeping-firefly.md. Khong con phan biet L1/L2 khi XU LY: moi dong (root
# tu file flash-sale, hoac related phat hien qua "San pham tuong tu" cua 1 root) deu di
# qua CUNG 1 quy trinh cao + cham diem. link_type ('root'/'related') chi con y nghia: (1)
# root tu nhom voi chinh no (groupid=itemid) va LUON duoc lay them related (ke ca khi
# chinh no khong dat chuan), (2) related KHONG mo rong tiep (chi 1 cap). "Da cao du lieu
# chua" tach rieng khoi "co dat tieu chi khong": dung cot affiliate_promoted_last_7days
# IS NOT NULL lam tin hieu "da cao xong" (khong them cot moi), con status_link chi phan
# anh KET QUA CHAM DIEM theo nguong HIEN TAI (tinh lai duoc bat ky luc nao qua
# reclassify_status(), khong can cao lai thiet bi neu doi nguong sau nay). ---

def import_links_as_pending(db_path, links, link_type="root", extract_item_id=None):
    """Nap 1 danh sach link vao DB lam hang doi 'pending' - itemid da co san (bat ke
    trang thai gi) thi BO QUA (INSERT OR IGNORE), khong ghi de tien do cu. link_type='root'
    thi tu gan groupid=itemid (root tu nhom voi chinh no). extract_item_id: ham tach itemid
    tu link (mac dinh dung u2_affiliate_offer_flow.extract_item_id_from_link qua lazy
    import - tranh import vong vi module do lai import shopee_db)."""
    if extract_item_id is None:
        import u2_affiliate_offer_flow as flow
        extract_item_id = flow.extract_item_id_from_link

    conn = _connect(db_path)
    try:
        inserted = 0
        already_existed = 0
        invalid = 0
        for link in links:
            itemid = extract_item_id(link)
            if not itemid:
                invalid += 1
                continue
            groupid = str(itemid) if link_type == "root" else None
            cur = conn.execute(
                "insert or ignore into products (itemid, product_link, link_type, groupid, status_link) "
                "values (?, ?, ?, ?, 'pending')",
                (str(itemid), link, link_type, groupid),
            )
            if cur.rowcount:
                inserted += 1
            else:
                already_existed += 1
        conn.commit()
        return {"inserted": inserted, "already_existed": already_existed, "invalid": invalid}
    finally:
        conn.close()


def insert_related_as_pending(db_path, groupid, related_items):
    """Nap cac ung vien 'San pham tuong tu' (tu offer_product_list_combined.list, DA CO
    SAN trong response product/list - khong can cao gi them) lam hang doi 'related'
    'pending'. Luu san 'sold' (batch_item_for_item_card_full.sold - da xac nhan co that
    trong response qua du lieu that) de loc so bo mien phi truoc khi phai cao thiet bi lay
    affiliate_promoted_last_7days/seller_commission (VND) cho tung item."""
    conn = _connect(db_path)
    try:
        inserted = 0
        for item in related_items:
            itemid = item.get("item_id")
            link = item.get("product_link")
            if not itemid or not link:
                continue
            sold = (item.get("batch_item_for_item_card_full") or {}).get("sold")
            cur = conn.execute(
                "insert or ignore into products (itemid, product_link, sold, link_type, groupid, status_link) "
                "values (?, ?, ?, 'related', ?, 'pending')",
                (str(itemid), link, sold, str(groupid)),
            )
            if cur.rowcount:
                inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def mark_no_affiliate(db_path, itemid, link_type=None, groupid=None, product_link=None):
    """Item hien sheet chia se THUONG "Chia se voi ban be va nguoi than" (KHONG co dong
    "Hoa hong doi tac") khi bam icon chia se - nghia la item nay KHONG duoc Shopee bat
    tinh nang Hoa Hong Xtra, se KHONG BAO GIO co product_v2/hoa hong that de cao dan toi
    dat 3 tieu chi. Ghi thang status_link='fail' + xtra=0 NGAY (bo qua het buoc cao
    product_v2/reclassify_status vi khong co du lieu that) de claim_pending() KHONG BAO
    GIO thu lai item nay nua o cac lan chay sau - tranh lang phi vong lap vo ich."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into products (itemid, product_link, link_type, groupid, status_link, xtra, fail_reason) "
            "values (?, ?, ?, ?, 'fail', 0, 'no_affiliate') "
            "on conflict(itemid) do update set status_link = 'fail', xtra = 0, fail_reason = 'no_affiliate'",
            (str(itemid), product_link, link_type, str(groupid) if groupid else None),
        )
        conn.commit()
    finally:
        conn.close()


def mark_product_not_found(db_path, itemid, link_type=None, groupid=None, product_link=None):
    """Link mo ra thang dialog "Sản phẩm bạn đang tìm kiếm không tồn tại." NGAY khi vua
    mo deeplink (truoc ca buoc bam icon chia se) - san pham da bi go/het hang vinh vien,
    khong lien quan gi toi Hoa Hong Xtra (khac voi mark_no_affiliate() nen KHONG dong
    xtra). Ghi thang status_link='fail' + fail_reason='not_found' NGAY (bo qua het buoc
    cao product_v2/reclassify_status vi khong co du lieu that) de claim_pending() KHONG
    BAO GIO thu lai item nay nua."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into products (itemid, product_link, link_type, groupid, status_link, fail_reason) "
            "values (?, ?, ?, ?, 'fail', 'not_found') "
            "on conflict(itemid) do update set status_link = 'fail', fail_reason = 'not_found'",
            (str(itemid), product_link, link_type, str(groupid) if groupid else None),
        )
        conn.commit()
    finally:
        conn.close()


def claim_pending(db_path, device_key, limit=1, lease_seconds=1800, sold_min=None, groupid=None):
    """Claim NGUYEN TU (BEGIN IMMEDIATE - khoa ghi ca file DB trong luc chon+danh dau,
    tranh 2 tien trinh/thiet bi cung tro vao 1 file SQLite gianh trung dong) toi da
    `limit` dong 'pending' CHUA CAO DU LIEU (affiliate_promoted_last_7days is null) va
    KHONG dang bi thiet bi khac giu claim (assigned_key is null, hoac claimed_at da qua
    lease_seconds - phong truong hop thiet bi truoc crash giua chung). sold_min: loc so bo
    MIEN PHI bang cot 'sold' da biet san tu luc seed. QUAN TRONG: dieu kien PHAN BIET
    theo link_type, KHONG dung chung 1 dieu kien "sold is null or sold > sold_min" cho ca
    2 nhu truoc (da xac nhan qua thuc nghiem la BUG - vai related item bi Shopee tra ve
    thieu han field 'sold' luc seed (insert_related_as_pending ghi sold=NULL cho ca
    truong hop nay), NULL do bi hieu nham thanh "chua biet nen cho qua" giong het root,
    lot loi ca nhung item sold that < sold_min sau khi cao xong): root luon duoc claim
    (sold luon null toi khi cao xong - day la truong hop NULL hop le duy nhat), con
    related CHI duoc claim khi sold KHONG NULL va sold > sold_min - related nao seed
    thieu 'sold' (NULL) se o lai 'pending' vinh vien thay vi duoc coi la dat chuan mac
    dinh. groupid (tuy chon): chi claim trong DUNG 1 nhom - dung de "don
    sach" ngay cac 'related' cua 1 root vua cao xong, TRANH bi chim nghim phia sau hang
    tram root khac dang cho trong hang doi FIFO chung (order by id asc - related moi nap
    luon co id lon hon, tu nhien xep sau het backlog root cu neu khong loc theo groupid).
    Tra ve danh sach dong day du (dict) VUA duoc claim (assigned_key da la device_key
    nay)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        where = [
            "status_link = 'pending'",
            "affiliate_promoted_last_7days is null",
            "(assigned_key is null or claimed_at is null "
            "or claimed_at < datetime('now', ?))",
        ]
        params = [f"-{lease_seconds} seconds"]
        if sold_min is not None:
            where.append("(link_type = 'root' or (sold is not null and sold > ?))")
            params.append(sold_min)
        if groupid is not None:
            where.append("groupid = ?")
            params.append(str(groupid))
        rows = conn.execute(
            f"select itemid from products where {' and '.join(where)} order by id asc limit ?",
            params + [limit],
        ).fetchall()
        ids = [r["itemid"] for r in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"update products set assigned_key = ?, claimed_at = current_timestamp "
                f"where itemid in ({placeholders})",
                [device_key] + ids,
            )
            claimed = conn.execute(
                f"select * from products where itemid in ({placeholders})", ids
            ).fetchall()
        else:
            claimed = []
        conn.commit()
        return [dict(r) for r in claimed]
    finally:
        conn.close()


def find_stale_related_groups(db_path):
    """Danh sach groupid co it nhat 1 dong 'related' PENDING nhung root cua nhom (itemid
    = groupid, tu nhom voi chinh no) KHONG con 'pending' nua (da done/fail tu LAN CHAY
    TRUOC). Cac related nay se KHONG BAO GIO duoc "vong lap chinh" (claim_pending khong
    loc groupid, FIFO theo id) cham toi lai vi root cua chung khong con dieu kien de lot
    vao ket qua nao ca - phai chu dong "don sach" rieng cac nhom nay o dau moi lan chay
    (xem run_pipeline). Da xac nhan qua thuc nghiem: neu bo qua buoc nay, related bi bo
    quen vinh vien sau khi root cua no da xu ly xong o 1 lan chay truoc."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "select distinct p.groupid from products p "
            "join products root on root.itemid = p.groupid "
            "where p.link_type = 'related' and p.status_link = 'pending' "
            "and root.status_link != 'pending'"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def release_claim(db_path, itemid):
    """Xoa assigned_key/claimed_at cho 1 item (goi sau khi xu ly xong, du thanh cong hay
    loi ky thuat) - de item van con 'pending' kha dung NGAY cho thiet bi khac, khong can
    doi het lease_seconds."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update products set assigned_key = null, claimed_at = null where itemid = ?",
            (str(itemid),),
        )
        conn.commit()
    finally:
        conn.close()


def release_claims_for_device(db_path, device_key):
    """Nha thu cong TOAN BO claim cua 1 thiet bi (phong truong hop thiet bi crash giua
    chung, khong tu release_claim() duoc) - tra ve so dong da nha."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "update products set assigned_key = null, claimed_at = null where assigned_key = ?",
            (device_key,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# =====================================================================================
# LOGIC GROUP MOI (updatelogic.txt): 1 root -> group du 'target' san pham dat chi tieu.
# Chi ROOT co status 'pending' + claim da thiet bi. San pham tuong tu khi vao DB da mang
# status 'member' (da gan group, dat chi tieu) hoac 'cached' (da cao product_v2, chua gan /
# de tai dung metrics tranh goi lai). Unique TOAN CUC: 1 itemid chi thuoc 1 group.
# =====================================================================================

def import_roots_as_pending(db_path, links):
    """Nap link root -> insert link_type='root', status='pending', groupid=itemid. Bo qua
    itemid da ton tai (insert or ignore). Tra ve so root MOI them."""
    conn = _connect(db_path)
    try:
        added = 0
        for link in links:
            m = re.search(r"/product/\d+/(\d+)", link) or re.search(r"(\d+)\s*$", str(link).strip())
            if not m:
                continue
            itemid = m.group(1)
            cur = conn.execute(
                "insert or ignore into products (itemid, product_link, link_type, groupid, status_link) "
                "values (?, ?, 'root', ?, 'pending')",
                (itemid, link, itemid),
            )
            added += cur.rowcount
        conn.commit()
        return added
    finally:
        conn.close()


def claim_root(db_path, device_key, lease_seconds=1800):
    """Claim NGUYEN TU (BEGIN IMMEDIATE) 1 root 'pending' chua bi thiet bi khac giu (hoac
    lease het). Tra ve dict dong root, hoac None neu het root."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "select * from products where link_type='root' and status_link='pending' "
            "and (assigned_key is null or claimed_at is null or claimed_at < datetime('now', ?)) "
            "order by id asc limit 1",
            (f"-{lease_seconds} seconds",),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "update products set assigned_key=?, claimed_at=current_timestamp where itemid=?",
            (device_key, row["itemid"]),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def assign_root_to_worker(db_path, itemid, device_key):
    """Giao THU CONG 1 root cu the cho 1 device_key cu the (dashboard chon tay) - dung lai
    dung 2 cot assigned_key/claimed_at nhu claim_root() (root van la 1 hang doi, chi khac
    AI duoc quyen chon: nguoi dung tren dashboard, khong phai tab tu claim). NGUYEN TU:
    tu choi neu root khong ton tai/khong phai 'pending', hoac dang bi device KHAC giu (va
    lease chua het). Tra ve {"ok": True, "root": {...}} hoac {"ok": False, "error": "..."}."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "select * from products where itemid=? and link_type='root'", (str(itemid),)
        ).fetchone()
        if row is None:
            conn.commit()
            return {"ok": False, "error": "khong tim thay root nay"}
        if row["status_link"] != "pending":
            conn.commit()
            return {"ok": False, "error": f"root dang o trang thai '{row['status_link']}', khong the giao"}
        if row["assigned_key"] and row["assigned_key"] != device_key:
            conn.commit()
            return {"ok": False, "error": f"root dang duoc giao cho '{row['assigned_key']}' roi"}
        conn.execute(
            "update products set assigned_key=?, claimed_at=current_timestamp where itemid=?",
            (device_key, str(itemid)),
        )
        conn.commit()
        updated = conn.execute("select * from products where itemid=?", (str(itemid),)).fetchone()
        return {"ok": True, "root": dict(updated)}
    finally:
        conn.close()


def get_assigned_root_for_worker(db_path, device_key):
    """Root (neu co) dang duoc giao cho dung device_key nay va van con 'pending' - worker
    (Tampermonkey) goi lien tuc de biet co viec moi hay chua.

    Neu chua co gi duoc giao THU CONG va setting 'auto_assign' dang bat: tu dong lay 1
    root 'pending' con trong (chua ai giu) giao luon cho worker nay, tai dung nguyen
    claim_root() (van la cung 1 co che nguyen tu/lease, chi khac la duoc goi tu day thay
    vi worker tu goi truc tiep nhu truoc). Nho vay nguoi dung KHONG can tu bam "Giao viec"
    tung root mot khi bat che do nay - worker ranh se tu nhan viec o lan poll ke tiep."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select * from products where link_type='root' and status_link='pending' "
            "and assigned_key=? order by claimed_at asc limit 1",
            (device_key,),
        ).fetchone()
        if row:
            return dict(row)
    finally:
        conn.close()

    if get_settings(db_path)["auto_assign"]:
        return claim_root(db_path, device_key)
    return None


def worker_heartbeat(db_path, device_key, status, current_root=None):
    """Upsert trang thai 'song' cua 1 tab Tampermonkey - CHI de dashboard hien thi
    (idle/working/blocked + root dang lam), khong dung de gan viec (xem
    assign_root_to_worker())."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into workers (device_key, status, current_root, last_heartbeat) "
            "values (?, ?, ?, current_timestamp) "
            "on conflict(device_key) do update set status=excluded.status, "
            "current_root=excluded.current_root, last_heartbeat=excluded.last_heartbeat",
            (device_key, status, current_root),
        )
        conn.commit()
    finally:
        conn.close()


def list_workers(db_path):
    """Danh sach tat ca worker tung heartbeat - dashboard tu tinh 'offline' theo do cu cua
    last_heartbeat (khong luu status 'offline' rieng, tranh worker tat dot ngot ma khong
    bao duoc)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select * from workers order by device_key asc").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def assign_member(db_path, row, groupid):
    """Gan item (row day du metrics tu product_v2) vao group NGUYEN TU, dam bao unique toan
    cuc (BEGIN IMMEDIATE khoa ghi ca file - 2 thiet bi khong gianh trung 1 item cho 2 group):
      - item chua co trong DB       -> INSERT status='member'
      - item dang 'cached'/chua gan -> UPDATE thanh member cua group nay (+ cap nhat metrics)
      - item da la member/root khac  -> KHONG gan, tra False
    Tra ve True neu gan duoc."""
    itemid = str(row["itemid"])
    r = dict(row)
    r.update(link_type="related", groupid=str(groupid), status_link="member")
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ex = conn.execute(
            "select status_link, groupid, link_type from products where itemid=?", (itemid,)
        ).fetchone()
        if ex is None:
            cols = [c for c in COLUMNS if c in r]
            conn.execute(
                f"insert into products ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
                [r[c] for c in cols],
            )
            conn.commit()
            return True
        status, gid, lt = ex[0], ex[1], ex[2]
        if lt == "root" or (status == "member" and gid):
            conn.commit()
            return False  # root cua chinh no, hoac da thuoc group khac
        setcols = [c for c in COLUMNS if c in r and c != "itemid"]
        conn.execute(
            f"update products set {', '.join(f'{c}=?' for c in setcols)} where itemid=?",
            [r[c] for c in setcols] + [itemid],
        )
        conn.commit()
        return True
    finally:
        conn.close()


def try_assign_verified(db_path, row: dict, groupid, promoted_7d_max=None, sold_min=None,
                         seller_commission_vnd_min=None):
    """Dung cho pipeline web/BFS moi (nhieu Chrome profile chay SONG SONG, moi profile tu
    duyet 1 root khac nhau) - khac assign_member() o cho: assign_member() coi moi dong
    'pending' (chua verify) la con TRONG, cho phep group khac de len duoc (dung cho phone
    pipeline cu, cao/gan tuan tu tung root 1 nen hiem khi dung race that). Voi BFS song
    song, 2 nhom co the cung phat hien 1 candidate qua similar_product_offers cua 2 root
    khac nhau - dong 'pending' do insert_related_as_pending() seed truoc GIU CHO nhom da
    seed no, nhom khac phai bi tu choi (khong duoc "cuop" dua vao viec chua verify).

    row: dict tu map_v2_data_to_row() (du affiliate_promoted_last_7days/sold/seller_commission
    That, tuc DA goi offer/product that cho item nay). Tinh passes_criteria() luon, roi:
      - Khong dat  -> luu 'cached' (groupid=null, tai dung metrics lan sau, khong goi lai API)
      - Dat + o duoc  -> gan 'member' cua groupid nay
      - Dat nhung da bi nhom KHAC giu (root cua no, hoac 'member'/'pending' groupid khac)
        -> tu choi, KHONG ghi de

    Tra ve dict {"outcome": "assigned"|"already_member"|"claimed_by_other"|"failed_criteria",
    "group_member_count": int} - group_member_count chi co gia tri khi outcome="assigned"
    hoac "already_member" (dung de vong lap BFS phia goi biet khi nao dat 60 ma dung)."""
    import select_l1_l2_candidates as l1l2
    if promoted_7d_max is None or sold_min is None or seller_commission_vnd_min is None:
        _settings = get_settings(db_path)
        if promoted_7d_max is None:
            promoted_7d_max = _settings["promoted_7d_max"]
        if sold_min is None:
            sold_min = _settings["sold_min"]
        if seller_commission_vnd_min is None:
            seller_commission_vnd_min = _settings["seller_commission_vnd_min"]

    itemid = str(row["itemid"])
    groupid = str(groupid)
    metrics = l1l2._row_metrics(row)
    passes = l1l2.passes_criteria(metrics, promoted_7d_max, sold_min, seller_commission_vnd_min)

    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        ex = conn.execute(
            "select status_link, groupid, link_type from products where itemid=?", (itemid,)
        ).fetchone()

        if not passes:
            if ex is None:
                r = dict(row)
                r.update(link_type="related", groupid=None, status_link="cached")
                cols = [c for c in COLUMNS if c in r]
                conn.execute(
                    f"insert into products ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
                    [r[c] for c in cols],
                )
            # da ton tai (vd 'pending' do seed truoc) - cap nhat metrics that + chuyen 'cached'
            elif ex["status_link"] != "member":
                r = dict(row)
                r.update(link_type="related", groupid=None, status_link="cached")
                setcols = [c for c in COLUMNS if c in r and c != "itemid"]
                conn.execute(
                    f"update products set {', '.join(f'{c}=?' for c in setcols)} where itemid=?",
                    [r[c] for c in setcols] + [itemid],
                )
            conn.commit()
            return {"outcome": "failed_criteria", "group_member_count": None}

        if ex is None:
            r = dict(row)
            r.update(link_type="related", groupid=groupid, status_link="member")
            cols = [c for c in COLUMNS if c in r]
            conn.execute(
                f"insert into products ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
                [r[c] for c in cols],
            )
            conn.commit()
            count = count_group_members(db_path, groupid)
            return {"outcome": "assigned", "group_member_count": count}

        status, gid, lt = ex["status_link"], ex["groupid"], ex["link_type"]
        if lt == "root":
            conn.commit()
            return {"outcome": "claimed_by_other", "group_member_count": None}
        if status == "member":
            conn.commit()
            if gid == groupid:
                return {"outcome": "already_member", "group_member_count": count_group_members(db_path, groupid)}
            return {"outcome": "claimed_by_other", "group_member_count": None}
        if gid is not None and gid != groupid:
            # 'pending'/'cached' nhung da bi nhom khac seed/giu truoc
            conn.commit()
            return {"outcome": "claimed_by_other", "group_member_count": None}

        r = dict(row)
        r.update(link_type="related", groupid=groupid, status_link="member")
        setcols = [c for c in COLUMNS if c in r and c != "itemid"]
        conn.execute(
            f"update products set {', '.join(f'{c}=?' for c in setcols)} where itemid=?",
            [r[c] for c in setcols] + [itemid],
        )
        conn.commit()
        count = count_group_members(db_path, groupid)
        return {"outcome": "assigned", "group_member_count": count}
    finally:
        conn.close()


def seed_and_claim_candidates(db_path, groupid, related_items):
    """Nhu insert_related_as_pending() nhung ATOMIC + tra ve DUNG cac item_id ma NHOM NAY
    dang thuc su giu quyen (moi them lan nay, hoac da la cua nhom nay tu seed truoc do) -
    dung cho BFS song song (nhieu Chrome profile/root cung luc): item da bi nhom KHAC
    seed truoc se KHONG co trong ket qua tra ve, phia goi biet ngay khong can xep vao
    hang doi cua minh nua (khoi ton 1 request that toi Shopee roi bi tu choi o
    try_assign_verified())."""
    groupid = str(groupid)
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        claimed = []
        for item in related_items:
            itemid = item.get("item_id")
            link = item.get("product_link")
            if not itemid or not link:
                continue
            itemid = str(itemid)
            sold = (item.get("batch_item_for_item_card_full") or {}).get("sold")
            ex = conn.execute("select groupid from products where itemid=?", (itemid,)).fetchone()
            if ex is None:
                conn.execute(
                    "insert into products (itemid, product_link, sold, link_type, groupid, status_link) "
                    "values (?, ?, ?, 'related', ?, 'pending')",
                    (itemid, link, sold, groupid),
                )
                claimed.append(itemid)
            elif ex["groupid"] == groupid:
                claimed.append(itemid)
            # else: da thuoc nhom/root khac - bo qua, khong tra ve
        conn.commit()
        return claimed
    finally:
        conn.close()


def verify_root(db_path, offer_data, promoted_7d_max=None, sold_min=None,
                 seller_commission_vnd_min=None):
    """Cap nhat metrics THAT (da goi offer/product that cho chinh root) - CHI cap nhat cot
    du lieu san pham, KHONG dung upsert_item()/map_v2_data_to_row() day du vi se ghi de
    assigned_key/claimed_at ve None giua luc dang xu ly (claim_root da set 2 cot nay de
    khoa 30 phut - ghi de som se khien claim_root() cho thiet bi KHAC tuong lease het,
    claim trung ngay chinh root nay giua chung). Tra ve co dat 3 tieu chi khong (dung de
    BFS quyet dinh root co tinh vao 60 khong - updatelogic.txt diem 4), KHONG tu gan/doi
    status_link - finish_root() lo viec do o cuoi."""
    import select_l1_l2_candidates as l1l2
    if promoted_7d_max is None or sold_min is None or seller_commission_vnd_min is None:
        _settings = get_settings(db_path)
        if promoted_7d_max is None:
            promoted_7d_max = _settings["promoted_7d_max"]
        if sold_min is None:
            sold_min = _settings["sold_min"]
        if seller_commission_vnd_min is None:
            seller_commission_vnd_min = _settings["seller_commission_vnd_min"]

    row = map_v2_data_to_row(offer_data, link_type="root", groupid=str(offer_data.get("item_id") or ""))
    itemid = row["itemid"]
    if not itemid:
        raise ValueError("offer_data khong co item_id hop le")
    metric_cols = [
        "shopid", "name", "price", "sold", "product_link", "default_commission",
        "seller_commission", "shopee_commission", "images", "stock", "rating_star",
        "rating_count", "ctime", "historical_sold", "shop_name", "original_price",
        "review_count", "affiliate_promoted_last_7days", "xtra",
    ]
    conn = _connect(db_path)
    try:
        conn.execute(
            f"update products set {', '.join(f'{c}=?' for c in metric_cols)} where itemid=?",
            [row[c] for c in metric_cols] + [itemid],
        )
        conn.commit()
    finally:
        conn.close()

    metrics = l1l2._row_metrics(row)
    passes = l1l2.passes_criteria(metrics, promoted_7d_max, sold_min, seller_commission_vnd_min)
    return {"passes": passes, "metrics": metrics}


def filter_new_itemids(db_path, itemids):
    """Loc ra cac itemid CHUA TUNG xuat hien trong DB (bat ky trang thai/nhom nao) - dung
    cho vong lap BFS phia Tampermonkey de bo qua ngay cac candidate da biet (da la
    root/member/pending/cached cua bat ky nhom nao), tranh xep vao hang doi roi lai bi
    tu choi o try_assign_verified() (do da bi nhom khac giu) - kiem tra som, tiet kiem
    request that toi Shopee."""
    if not itemids:
        return []
    conn = _connect(db_path)
    try:
        ids = [str(i) for i in itemids]
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"select itemid from products where itemid in ({placeholders})", ids
        ).fetchall()
        known = {r[0] for r in rows}
        return [i for i in ids if i not in known]
    finally:
        conn.close()


def cache_item(db_path, row):
    """Luu item da goi product_v2 nhung CHUA gan group (status='cached', groupid=null) de
    tai dung metrics lan sau (tranh goi lai product_v2). Bo qua neu itemid da ton tai (giu
    nguyen - khong de-khong clobber member)."""
    r = dict(row)
    r.update(link_type="related", groupid=None, status_link="cached")
    conn = _connect(db_path)
    try:
        cols = [c for c in COLUMNS if c in r]
        conn.execute(
            f"insert or ignore into products ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
            [r[c] for c in cols],
        )
        conn.commit()
    finally:
        conn.close()


def count_group_members(db_path, groupid):
    """So san pham tuong tu 'member' da gan vao group (KHONG tinh root)."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "select count(*) from products where groupid=? and status_link='member'",
            (str(groupid),),
        ).fetchone()[0]
    finally:
        conn.close()


def list_roots_with_counts(db_path, status=None, search=None, limit=200):
    """Danh sach root + so member da gom cua tung group - dung cho UI xem group."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where = ["link_type='root'"]
        params = []
        if status:
            where.append("status_link=?")
            params.append(status)
        if search:
            where.append("(itemid like ? or name like ?)")
            params += [f"%{search}%", f"%{search}%"]
        rows = conn.execute(
            f"select * from products where {' and '.join(where)} order by id asc limit ?",
            params + [limit],
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["member_count"] = conn.execute(
                "select count(*) from products where groupid=? and status_link='member'",
                (d["itemid"],),
            ).fetchone()[0]
            out.append(d)
        return out
    finally:
        conn.close()


def list_group_members(db_path, groupid):
    """San pham tuong tu 'member' cua 1 group, sap theo promoted_7d tang dan (tot nhat truoc)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select * from products where groupid=? and status_link='member' "
            "order by affiliate_promoted_last_7days asc",
            (str(groupid),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def finish_root(db_path, itemid):
    """Root xu ly xong -> status='done', nha claim, tu tinh lai cot merged_link cho ca
    group (khong can nguoi dung tu bam)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update products set status_link='done', assigned_key=null, claimed_at=null "
            "where itemid=?",
            (str(itemid),),
        )
        conn.commit()
    finally:
        conn.close()
    compute_merged_links(db_path, itemid)


def compute_merged_links(db_path, groupid, batch_size=6):
    """Gop moi batch_size link LIEN TIEP (theo thu tu them vao group - id tang dan) thanh
    1 chuoi noi bang '|', ghi de vao cot merged_link cua TUNG dong thuoc dung batch do
    (ca batch_size dong deu mang chung 1 gia tri merged_link). Neu ROOT cua group dat 3
    tieu chi (tinh la 1/60) thi no la link #1 trong day, cac 'member' xep tiep theo sau
    (thu tu id tang dan). Batch cuoi co the le (<batch_size) neu group khong du. Goi tu
    finish_root() - khong can nguoi dung tu tinh lai. Tra ve tong so link da gop."""
    import select_l1_l2_candidates as l1l2
    settings = get_settings(db_path)
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        root = conn.execute(
            "select * from products where itemid=? and link_type='root'", (str(groupid),)
        ).fetchone()
        members = conn.execute(
            "select * from products where groupid=? and status_link='member' order by id asc",
            (str(groupid),),
        ).fetchall()

        ordered = []  # [(itemid, product_link), ...] dung thu tu link1..linkN
        if root is not None and root["product_link"]:
            metrics = l1l2._row_metrics(root)
            if l1l2.passes_criteria(metrics, settings["promoted_7d_max"], settings["sold_min"],
                                     settings["seller_commission_vnd_min"]):
                ordered.append((root["itemid"], root["product_link"]))
        for m in members:
            if m["product_link"]:
                ordered.append((m["itemid"], m["product_link"]))

        for i in range(0, len(ordered), batch_size):
            chunk = ordered[i:i + batch_size]
            merged = "|".join(link for _, link in chunk)
            for itemid, _ in chunk:
                conn.execute("update products set merged_link=? where itemid=?", (merged, itemid))
        conn.commit()
        return len(ordered)
    finally:
        conn.close()


def recompute_all_merged_links(db_path):
    """Tinh lai merged_link cho TAT CA root cung 1 luc - dung cho nut bam hang loat tren
    UI, thay vi bat nguoi dung tu bam tung root mot (rieng root MOI hoan tat qua
    finish_root() thi da tu tinh, ham nay chi can cho cac root cu tu truoc khi co tinh
    nang nay). Tra ve {"roots_processed": n, "total_links": n}."""
    conn = _connect(db_path)
    try:
        root_ids = [r[0] for r in conn.execute(
            "select itemid from products where link_type='root'"
        ).fetchall()]
    finally:
        conn.close()
    total_links = 0
    for itemid in root_ids:
        total_links += compute_merged_links(db_path, itemid)
    return {"roots_processed": len(root_ids), "total_links": total_links}


def reset_all_insufficient_roots(db_path):
    """Dua VE 'pending' TAT CA root 'done' nhung CHUA du 60 thanh vien - dung cho nut bam
    hang loat, thay vi phai tu 'Dat lai' tung root mot (vd sau dot loi 403 khien nhieu
    root bi bo do o cung 1 lan). Tra ve {"reset_count": n, "itemids": [...]}."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select p.itemid, "
            "(select count(*) from products m where m.groupid=p.itemid and m.status_link='member') "
            "as member_count "
            "from products p where p.link_type='root' and p.status_link='done'"
        ).fetchall()
        to_reset = [r["itemid"] for r in rows if r["member_count"] < 60]
        for itemid in to_reset:
            conn.execute(
                "update products set status_link='pending', assigned_key=null, claimed_at=null, "
                "fail_reason=null where itemid=?",
                (itemid,),
            )
        conn.commit()
        return {"reset_count": len(to_reset), "itemids": to_reset}
    finally:
        conn.close()


def mark_root_failed(db_path, itemid, reason):
    """Danh dau 1 root la 'fail' (KHONG PHAI 'done') + nha claim - dung khi API tra loi
    THAT SU (vd Shopee bao 'invalid item id', item bi go/sai id...), KHONG PHAI truong
    hop "chay het ung vien truoc khi du 60" (do la 'done' + insufficient, xem finish_root()).
    BAT BUOC phai goi ham nay thay vi de nguyen 'pending' khi gap loi - da gap bug thuc te:
    de nguyen 'pending' + con assigned_key khien worker cu nhan lai DUNG root loi do moi
    lan poll, lap vo han. Nguoi dung van co the tu "Dat lai" tren dashboard neu muon thu lai."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update products set status_link='fail', assigned_key=null, claimed_at=null, "
            "fail_reason=? where itemid=? and link_type='root'",
            (reason, str(itemid)),
        )
        conn.commit()
    finally:
        conn.close()


def reset_root_to_pending(db_path, itemid):
    """Dua 1 root ve lai 'pending' + nha claim (assigned_key/claimed_at) - dung khi nguoi
    dung muon giao lai/thu lai 1 root da 'done' (vd bi bo do do loi 403 tam thoi, khong
    phai that su het ung vien) hoac 'fail'. KHONG dong toi cac 'related' da gan group cua
    lan chay truoc - BFS lan sau se tu bo qua chung qua seed_and_claim_candidates() (da
    thuoc group nay roi thi van tinh la "cua nhom nay", khong bi mat)."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "update products set status_link='pending', assigned_key=null, claimed_at=null, "
            "fail_reason=null where itemid=? and link_type='root'",
            (str(itemid),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def fetch_unfetched(db_path=DB_PATH_DEFAULT, link_type=None, groupid=None, min_sold=None,
                     order_by_sold_desc=False, limit=None):
    """Danh sach item CHUA CAO DU LIEU (affiliate_promoted_last_7days is null) - dung de
    XEM/DEM (khong claim, khong khoa ghi) - vong lap xu ly thuc su dung claim_pending().
    min_sold: loc so bo bang 'sold' da co san tu luc seed (mien phi, khong can cao) - dung
    cho related de khong phai cao het moi ung vien. order_by_sold_desc: uu tien ung vien
    sold cao truoc."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where = ["affiliate_promoted_last_7days is null"]
        params = []
        if link_type:
            where.append("link_type = ?")
            params.append(link_type)
        if groupid:
            where.append("groupid = ?")
            params.append(str(groupid))
        if min_sold is not None:
            where.append("sold is not null and sold > ?")
            params.append(min_sold)
        order_sql = "order by sold desc" if order_by_sold_desc else "order by id asc"
        limit_sql = ""
        if limit is not None:
            limit_sql = "limit ?"
            params.append(limit)
        rows = conn.execute(
            f"select * from products where {' and '.join(where)} {order_sql} {limit_sql}",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def reclassify_status(db_path, link_type=None, groupid=None, promoted_7d_max=None,
                       sold_min=None, seller_commission_vnd_min=None):
    """Voi MOI dong DA CO du lieu (affiliate_promoted_last_7days is not null) khop bo
    loc, tinh lai passes_criteria() (tai dung tu select_l1_l2_candidates - lazy import de
    tranh import vong, vi module do lai import shopee_db o top-level) theo nguong HIEN
    TAI roi cap nhat status_link = 'done'/'fail'. Goi lai ham nay bat ky luc nao (ke ca
    khong cao gi moi) se tu 'cham lai diem' theo nguong moi - khong can cao lai thiet bi
    vi du lieu goc (sold/seller_commission/affiliate_promoted_last_7days) da co san."""
    import select_l1_l2_candidates as l1l2
    if promoted_7d_max is None or sold_min is None or seller_commission_vnd_min is None:
        _settings = get_settings(db_path)
        if promoted_7d_max is None:
            promoted_7d_max = _settings["promoted_7d_max"]
        if sold_min is None:
            sold_min = _settings["sold_min"]
        if seller_commission_vnd_min is None:
            seller_commission_vnd_min = _settings["seller_commission_vnd_min"]

    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where = ["affiliate_promoted_last_7days is not null"]
        params = []
        if link_type:
            where.append("link_type = ?")
            params.append(link_type)
        if groupid:
            where.append("groupid = ?")
            params.append(str(groupid))
        rows = conn.execute(
            f"select itemid, sold, seller_commission, affiliate_promoted_last_7days "
            f"from products where {' and '.join(where)}",
            params,
        ).fetchall()

        updated = {"done": 0, "fail": 0}
        for row in rows:
            metrics = l1l2._row_metrics(row)
            status = ("done" if l1l2.passes_criteria(
                metrics, promoted_7d_max, sold_min, seller_commission_vnd_min
            ) else "fail")
            conn.execute("update products set status_link = ? where itemid = ?", (status, row["itemid"]))
            updated[status] += 1
        conn.commit()
        return updated
    finally:
        conn.close()


# --- Tinh nang "Tao video" (day thang qua videoai-api.devappnow.com, xem
# videoai_client.py) - dieu kien du de day: da co 'merged_link' (thuoc 1 nhom da gom du/
# hoan tat VA dat tieu chi loc, xem compute_merged_links()). Tai dung 2 cot co san tu truoc
# (cache_uploaded, job_id) lam co trang thai, KHONG dung status_link (tranh pha luong
# scrape dang dung cot do). Nguoi dung co nhieu may tao video (moi may 1 API key/thu
# muc/pool rieng, xem CREATE_VIDEO_MACHINES_TABLE_SQL) - chon 1 may khi chay day. ---

def add_video_machine(db_path, name, api_key, tag, pool="selfhostPool"):
    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into video_machines (name, api_key, tag, pool) values (?, ?, ?, ?)",
            (name, api_key, tag, pool),
        )
        conn.commit()
    finally:
        conn.close()


def list_video_machines(db_path):
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select * from video_machines order by id asc").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_video_machine(db_path, machine_id):
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from video_machines where id=?", (machine_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def remove_video_machine(db_path, machine_id):
    conn = _connect(db_path)
    try:
        cur = conn.execute("delete from video_machines where id=?", (machine_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_video_machine_enabled(db_path, machine_id, enabled):
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "update video_machines set enabled=? where id=?", (int(bool(enabled)), machine_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_video_push_candidates(db_path, limit=200):
    """Hang doi cho tinh nang 'Tao video': da co merged_link nhung CHUA tao xong task
    VideoAI (job_id con null) - bao gom ca dong da day cache tu lan chay truoc
    (cache_uploaded=1) lan dong chua day. Sap FIFO (id tang dan)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select * from products where merged_link is not null and job_id is null "
            "and product_link is not null order by id asc limit ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_cache_uploaded(db_path, itemids):
    """Danh dau da day cache VideoAI thanh cong cho danh sach itemid - lan chay sau se bo
    qua buoc day cache cho cac dong nay (chi con thieu buoc tao task)."""
    if not itemids:
        return
    conn = _connect(db_path)
    try:
        conn.executemany(
            "update products set cache_uploaded=1 where itemid=?",
            [(str(i),) for i in itemids],
        )
        conn.commit()
    finally:
        conn.close()


def mark_video_jobs(db_path, itemid_jobid_pairs):
    """Ghi job_id VideoAI tra ve cho tung itemid - co job_id nghia la XONG, se khong con
    xuat hien trong list_video_push_candidates() nua (khong day/tao lai)."""
    if not itemid_jobid_pairs:
        return
    conn = _connect(db_path)
    try:
        conn.executemany(
            "update products set job_id=? where itemid=?",
            [(job_id, str(itemid)) for itemid, job_id in itemid_jobid_pairs],
        )
        conn.commit()
    finally:
        conn.close()


def count_video_push_stats(db_path):
    """So lieu tong quan cho tab 'Tao video' tren dashboard."""
    conn = _connect(db_path)
    try:
        eligible = conn.execute(
            "select count(*) from products where merged_link is not null"
        ).fetchone()[0]
        cache_uploaded = conn.execute(
            "select count(*) from products where merged_link is not null and cache_uploaded=1"
        ).fetchone()[0]
        job_created = conn.execute(
            "select count(*) from products where merged_link is not null and job_id is not null"
        ).fetchone()[0]
        return {"eligible": eligible, "cache_uploaded": cache_uploaded, "job_created": job_created}
    finally:
        conn.close()


def list_video_created_items(db_path):
    """San pham DA co job_id (video da tao xong that su, khac voi 'du dieu kien' - xem
    count_video_push_stats()) - dung cho nut 'Xuat Excel' tren tab 'Tao video'. Sap theo id
    tang dan (FIFO, dung thu tu da tao)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select itemid, name, merged_link from products where job_id is not null order by id asc"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --- Tinh nang "Tao tai khoan Shopee" (mua mail dongvanfb + doc code, xem
# dongvanfb_client.py) - moi dong 1 mail da mua, nguoi dung tu dien shopee_id/device/market
# sau khi dang ky Shopee bang mail do. ---

def add_mail_accounts_from_buy(db_path, lines, account_type, order_code):
    """Parse tung dong "email|password|refresh_token|client_id" dongvanfb tra ve tu mua
    mail (list_data) va them vao bang. Bo qua dong khong parse duoc. Tra ve so dong da them."""
    import dongvanfb_client

    parsed_rows = []
    for line in lines:
        parsed = dongvanfb_client.parse_mail_line(line)
        if not parsed:
            continue
        parsed_rows.append((
            line, parsed["email"], parsed["password"], parsed["refresh_token"],
            parsed["client_id"], account_type, order_code,
        ))
    if not parsed_rows:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            "insert into mail_accounts (full_info, email, password, refresh_token, "
            "client_id, account_type, order_code) values (?, ?, ?, ?, ?, ?, ?)",
            parsed_rows,
        )
        conn.commit()
        return len(parsed_rows)
    finally:
        conn.close()


def list_mail_accounts(db_path, market=None, slot=None, search=None, limit=500):
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where = []
        params = []
        if market:
            where.append("market = ?")
            params.append(market)
        if slot:
            where.append("slot = ?")
            params.append(slot)
        if search:
            where.append("(email like ? or shopee_id like ? or device like ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        sql = "select * from mail_accounts"
        if where:
            sql += " where " + " and ".join(where)
        sql += " order by id desc limit ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_created_mail_accounts(db_path):
    """Mail DA duoc dung de tao tai khoan Shopee that (nguoi dung da dien shopee_id) - dung
    cho nut 'Xuat Excel' tren tab 'Tao tai khoan Shopee', khac voi list_mail_accounts()
    (liet ke TAT CA mail da mua, ke ca chua tao tai khoan). Sap theo id tang dan (FIFO,
    dung thu tu da mua/tao)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select * from mail_accounts where shopee_id is not null and shopee_id != '' "
            "order by id asc"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_mail_account(db_path, account_id):
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from mail_accounts where id=?", (account_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_mail_account_fields(db_path, account_id, shopee_id=None, device=None, slot=None, market=None):
    """Cap nhat MOT PHAN cot nguoi dung tu nhap (tham so None = giu nguyen), dung cho nut
    luu tung dong tren UI khi doi Shopee_id/Device/Slot/Market."""
    current = get_mail_account(db_path, account_id)
    if not current:
        return None
    new_vals = {
        "shopee_id": shopee_id if shopee_id is not None else current["shopee_id"],
        "device": device if device is not None else current["device"],
        "slot": slot if slot is not None else current["slot"],
        "market": market if market is not None else current["market"],
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            "update mail_accounts set shopee_id=?, device=?, slot=?, market=? where id=?",
            (new_vals["shopee_id"], new_vals["device"], new_vals["slot"], new_vals["market"], account_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_mail_account(db_path, account_id)


def set_mail_account_code(db_path, account_id, code):
    conn = _connect(db_path)
    try:
        conn.execute(
            "update mail_accounts set shopee_code=?, checked_at=current_timestamp where id=?",
            (code, account_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_mail_account(db_path, account_id):
    conn = _connect(db_path)
    try:
        cur = conn.execute("delete from mail_accounts where id=?", (account_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_mail_accounts(db_path):
    conn = _connect(db_path)
    try:
        cur = conn.execute("delete from mail_accounts")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def count_status(db_path=DB_PATH_DEFAULT, groupid=None, link_type=None, status_link=None):
    """Dem nhanh so dong khop bo loc - dung de hien thi so lieu tong quan (pending/done/
    fail, root/related) tren UI."""
    conn = _connect(db_path)
    try:
        where = []
        params = []
        if groupid:
            where.append("groupid = ?")
            params.append(str(groupid))
        if link_type:
            where.append("link_type = ?")
            params.append(link_type)
        if status_link:
            where.append("status_link = ?")
            params.append(status_link)
        where_sql = f"where {' and '.join(where)}" if where else ""
        return conn.execute(f"select count(*) from products {where_sql}", params).fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    test_db = "artifacts/db/shopee_test.db"
    init_db(test_db)
    fake_row = map_v2_data_to_row(
        {
            "item_id": "999999999",
            "product_link": "https://shopee.vn/product/1/999999999",
            "affiliate_promoted_last_7days": "42",
            "commission_rate": {"seller_commission": "₫1.234"},
            "batch_item_for_item_card_full": {"itemid": "999999999", "shopid": "1", "name": "Test"},
        },
        link_type="root",
    )
    upsert_item(test_db, fake_row)
    print("item_exists(999999999):", item_exists(test_db, "999999999"))
    print("item_exists(111):", item_exists(test_db, "111"))
    print("Test OK.")

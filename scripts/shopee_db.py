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

# Nguon chan ly DUY NHAT de suy market THAT tu domain cua 1 link Shopee - khop dung danh
# sach domain trong @match cua shopee_collector.user.js. videoai_client.py tai dung ham
# nay (import truc tiep) thay vi tu giu 1 ban regex hep rieng (truoc day chi co vn/sg/ph,
# la nguyen nhan cot 'market' trong DB bi mac dinh sai cho MOI market khac).
_MARKET_DOMAIN_RE = re.compile(
    r"shopee\.(vn|ph|tw|sg|cl|co\.th|com\.my|co\.id|com\.br|com\.mx|com\.co)",
    re.IGNORECASE,
)
_MARKET_CODE_BY_DOMAIN_SUFFIX = {
    "vn": "vn", "ph": "ph", "tw": "tw", "sg": "sg", "cl": "cl",
    "co.th": "th", "com.my": "my", "co.id": "id",
    "com.br": "br", "com.mx": "mx", "com.co": "co",
}


def market_from_link(link):
    """Suy market THAT tu domain cua link (vd shopee.co.th -> 'th'). Tra ve None neu
    khong nhan dien duoc domain nao (KHONG con mac dinh 'vn' nhu code cu - do chinh la
    nguyen nhan bug 'moi root deu bi ghi market=vn' du that ra tu PH/TH/MY)."""
    m = _MARKET_DOMAIN_RE.search(str(link or ""))
    if not m:
        return None
    return _MARKET_CODE_BY_DOMAIN_SUFFIX.get(m.group(1).lower())


# Dung thu tu, dung ten cot voi D:\Shopee369\0-database\migrate\schema.sql (bang
# products) + 1 cot moi affiliate_promoted_last_7days o cuoi.
COLUMNS = [
    "itemid", "shopid", "name", "price", "sold", "product_link",
    "default_commission", "seller_commission", "shopee_commission", "groupid",
    "images", "stock", "rating_star", "rating_count", "ctime", "link_type",
    "merged_link", "historical_sold", "shop_name", "description", "status_link",
    "created_at", "market", "category_group", "original_price", "review_count",
    "assigned_key", "claimed_at", "job_id", "cache_uploaded",
    "affiliate_promoted_last_7days", "xtra", "fail_reason", "cat_id", "cat_name",
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

# Nhat ky 1 dong / 1 lan goi POST /api/videos/push (1 lo toi da 200 video) - luu LAI TREN DB
# (khong chi hien tam thoi tren dashboard) de nguoi dung truy xuat lai lich su tao video sau
# nay (vd qua ngay hom sau, hoac mo tu may khac) - truoc day chi co videoLog() phia client
# (bien mat khi F5/dong tab). Ghi trong chinh push_videos() (affiliate_scrape_server.py),
# KHONG phai tu dashboard tu goi rieng - dam bao ghi dung dung 1 lan/lo bat ke ai/cach nao
# goi API nay (dashboard, script khac, hay goi tay qua curl).
CREATE_VIDEO_PUSH_LOG_TABLE_SQL = """
create table if not exists video_push_log (
    id integer primary key autoincrement,
    created_at timestamp default current_timestamp,
    market text,
    machine_id integer,
    machine_name text,
    requested_limit integer,
    done integer default 0,
    pushed integer default 0,
    created integer default 0,
    error_count integer default 0,
    error_detail text
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
    profile text default '',
    slot text default '',
    market text default 'PH',
    shopee_code text,
    checked_at timestamp,
    created_at timestamp default current_timestamp
);
"""

# Ghi lai "item X TUNG duoc de xuat lam candidate cho root Y" - populate tu
# seed_and_claim_candidates() cho MOI item nhan duoc tu similar_product_offers, BAT KE co
# dat tieu chi/duoc claim hay khong. Muc dich: item bi loai (status_link='cached',
# groupid=NULL - xem try_assign_verified()) truoc day MAT HET dau vet "tung la candidate
# cua root nao", nen khi nguoi dung doi dieu kien loc, KHONG CO CACH nao tu dong re-check +
# gan lai dung nhom ma khong cao lai root do (2026-09-02, yeu cau nguoi dung: "co san cache,
# doi dieu kien thi phai co co che check lai de ap dung luon thay vi phai cao lai"). Bang nay
# giu lai quan he do de recheck_cached_candidates() co the quet lai TOAN BO cache doi chieu
# voi dieu kien HIEN TAI ma KHONG can goi API Shopee. CHI co hieu luc voi item duoc cache TU
# LUC co bang nay tro di - item cached truoc do van can cao lai root tuong ung 1 lan.
CREATE_CANDIDATE_ROOT_SEEN_TABLE_SQL = """
create table if not exists candidate_root_seen (
    itemid text not null,
    market text not null,
    groupid text not null,
    seen_at timestamp default current_timestamp,
    primary key (itemid, market, groupid)
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
    last_heartbeat timestamp,
    market text
);
"""

CREATE_TABLE_SQL = """
create table if not exists products (
    id integer primary key autoincrement,
    itemid text,
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
    market text,
    category_group text,
    original_price integer,
    review_count integer,
    assigned_key text,
    claimed_at timestamp,
    job_id text,
    cache_uploaded integer default 0,
    affiliate_promoted_last_7days integer,
    xtra integer,
    fail_reason text,
    cat_id integer,
    cat_name text,
    unique(market, itemid)
);
"""

# Bang tu khoa cào root AFF (tinh nang "Cào root AFF" - cao san pham root tu trang
# affiliate offer product_offer bang cach TIM THEO TU KHOA, xem Cao_root_aff.txt):
# - 1 keyword duy nhat trong 1 market (unique(market, keyword)).
# - Worker (cdp_keyword_worker.mjs) claim 1 keyword dang 'pending' (hoac 'in_progress' ma
#   lease het han / cua CHINH device do con sot) roi dieu huong tab tren affiliate page lam
#   cho chinh trang goi /api/v3/offer/product/list theo keyword do (token chong bot hop le),
#   hook Network chup TUNG trang (page_offset) roi day ve server qua /api/keywords/page_done.
# - Server lo: loc san pham dat tieu chi (sold_min, hoa hong tien uoc tinh toi thieu) dua
#   tren CAU HINH CAO worker gui kem moi trang (khong luu o import - xem keyword_page_done:
#   sold_min/comm_money_min/filter_types nhan tu body) roi insert vao bang 'products' lam
#   ROOT (link_type='root', status_link='pending', groupid=itemid) - cac link da ton tai O
#   BAT KY dau trong DB deu bi bo (dup_skipped), dung y nghia "keyword crawler chi la nguon
#   bom root moi vao hang doi Root Navigator".
# - status: pending (cho claim) / in_progress (dang cao) / done / error.
# - checkpoint_page/roots_found/... chi la thong tin hien thi + resume; viec dedup dua vao
#   chinh bang products nen cao lai tu trang 0 van AN TOAN (insert-or-ignore).
CREATE_KEYWORDS_TABLE_SQL = """
create table if not exists keywords (
    id integer primary key autoincrement,
    market text not null,
    keyword text not null,
    cat_id integer,
    cat_name text,
    status text default 'pending',
    assigned_key text,
    claimed_at timestamp,
    checkpoint_page integer default 0,
    total_count integer,
    roots_found integer default 0,
    roots_inserted integer default 0,
    dup_skipped integer default 0,
    last_error text,
    last_page_at timestamp,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp,
    unique(market, keyword)
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
    # Migration itemid unique TOAN CUC -> unique(market, itemid): itemid Shopee duoc cap
    # DOC LAP theo tung market, KHONG dam bao duy nhat toan cau - 2 san pham o 2 market
    # khac nhau co the trung so itemid. SQLite khong cho ALTER constraint truc tiep nen
    # phai dung lai bang moi (products_new) + copy du lieu qua. Nhan tien BACKFILL luon
    # cot 'market' cho du lieu cu bang market_from_link(product_link) - truoc gio cot nay
    # bi ghi sai hang loat thanh 'vn' (mac dinh cua schema cu) du du lieu that tu PH/TH/MY,
    # vi khong insert path nao tung truyen market vao ca (xem videoai_client.py, module do
    # phai tu suy lai market tu URL thay vi tin cot nay - chinh la bang chung bug nay
    # thuc su ton tai chu khong phai ly thuyet). Doc theo 'order by id asc' de giu dung thu
    # tu FIFO cu (claim_pending/claim_root/compute_merged_links deu dua vao thu tu id tang
    # dan) - id moi se tu dong tang lai theo dung thu tu nay.
    if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
        conn.row_factory = sqlite3.Row
        existing_cols_old = {row[1] for row in conn.execute("pragma table_info(products)").fetchall()}
        select_cols = [c for c in COLUMNS if c in existing_cols_old]
        old_rows = conn.execute(
            f"select {', '.join(select_cols)} from products order by id asc"
        ).fetchall()
        conn.row_factory = None

        conn.execute("drop table if exists products_new")
        conn.execute(
            CREATE_TABLE_SQL.replace(
                "create table if not exists products (",
                "create table if not exists products_new (",
            )
        )
        insert_cols_sql = ", ".join(COLUMNS)
        placeholders = ", ".join("?" for _ in COLUMNS)
        for old_row in old_rows:
            r = {c: old_row[c] for c in select_cols}
            r["market"] = market_from_link(r.get("product_link")) or r.get("market")
            values = [r.get(c) for c in COLUMNS]
            conn.execute(
                f"insert into products_new ({insert_cols_sql}) values ({placeholders})", values
            )
        conn.execute("drop table products")
        conn.execute("alter table products_new rename to products")
        conn.execute("PRAGMA user_version = 2")
    # Cot 'xtra' them sau - DB cu (tao truoc khi co dong nay trong CREATE_TABLE_SQL) se
    # khong tu co cot, phai ALTER TABLE rieng. SQLite khong ho tro "ADD COLUMN IF NOT
    # EXISTS" nen phai tu kiem tra qua pragma table_info truoc.
    existing_cols = {row[1] for row in conn.execute("pragma table_info(products)").fetchall()}
    if "xtra" not in existing_cols:
        conn.execute("alter table products add column xtra integer")
    if "fail_reason" not in existing_cols:
        conn.execute("alter table products add column fail_reason text")
    # Cot 'cat_id'/'cat_name' them sau - danh muc Shopee (tu URL '-cat.<id>' khi cao bang
    # Shopee Product Link Collector, xem shopee_collector.user.js) gan cho root, san pham
    # tuong tu ke thua lai cua root (xem try_assign_verified()). NULL = cao ngoai tab danh
    # muc (dung nhu thiet ke, khong phai loi).
    if "cat_id" not in existing_cols:
        conn.execute("alter table products add column cat_id integer")
    if "cat_name" not in existing_cols:
        conn.execute("alter table products add column cat_name text")
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
    # itemid khong con TU DONG co index rieng nhu truoc (truoc day 'unique' 1 cot tu tao
    # index; gio unique la composite (market, itemid), khong tan dung duoc cho tra cuu CHI
    # theo itemid nhu search LIKE trong fetch_all_items()/list_roots_with_counts()) - can
    # index rieng.
    conn.execute("create index if not exists idx_products_itemid on products(itemid)")
    # Thong ke nganh hang (category_stats(), tab "Van hanh") GROUP BY (market, cat_id) tren
    # TOAN BO bang products (khong loc link_type/status_link truoc nhu cac truy van khac) -
    # can index rieng de tranh quet toan bang (~900k+ dong o quy mo thuc te du an nay) moi
    # lan goi, cung tinh than voi cac index idx_products_* khac o tren (da do dac gap van
    # de tuong tu voi /api/roots/list, /api/stats truoc khi co index).
    conn.execute("create index if not exists idx_products_cat on products(market, cat_id)")
    # Ho tro truy van GROUP BY trong list_roots_with_counts() (dem member cho TAT CA group
    # trong 1 lan, thay vi N+1 query rieng cho tung root - xem ghi chu ham do, sua
    # 2026-08-27 sau khi do dac CPU 100% khi chay nhieu tab BFS song song). Dan dau bang
    # market+status_link (loc "member" 1 thi truong) roi groupid (GROUP BY) giup SQLite
    # quet thang phan can, khong can scan/sort toan bang.
    conn.execute(
        "create index if not exists idx_products_market_status_group "
        "on products(market, status_link, groupid)"
    )
    conn.execute(CREATE_DEVICES_TABLE_SQL)
    conn.execute(CREATE_SETTINGS_TABLE_SQL)
    conn.execute(CREATE_WORKERS_TABLE_SQL)
    # Cot 'market' them sau - DB cu (bang workers tao truoc khi co dong nay) can ALTER
    # TABLE rieng, cung ly do nhu xtra/fail_reason o tren. Luu market cua CHINH tab
    # Tampermonkey (suy tu hostname luc heartbeat) de dashboard biet tab nao dang phuc vu
    # market nao - xem worker_heartbeat().
    existing_workers_cols = {row[1] for row in conn.execute("pragma table_info(workers)").fetchall()}
    if "market" not in existing_workers_cols:
        conn.execute("alter table workers add column market text")
    # Cot 'auto_assign' them sau - DB cu (bang settings tao truoc khi co dong nay) can
    # ALTER TABLE rieng, cung ly do nhu xtra/fail_reason o tren.
    existing_settings_cols = {row[1] for row in conn.execute("pragma table_info(settings)").fetchall()}
    if "auto_assign" not in existing_settings_cols:
        conn.execute("alter table settings add column auto_assign integer default 0")
    if "dongvanfb_api_key" not in existing_settings_cols:
        conn.execute("alter table settings add column dongvanfb_api_key text default ''")
    # Cot 'auto_assign_market' them sau - gioi han auto_assign CHI ap dung cho 1 market cu
    # the thay vi luon ca TAT CA market cung luc. Rong/NULL = tat ca market (hanh vi cu,
    # tuong thich nguoc). Xem get_assigned_root_for_worker().
    if "auto_assign_market" not in existing_settings_cols:
        conn.execute("alter table settings add column auto_assign_market text default ''")
    conn.execute(CREATE_VIDEO_MACHINES_TABLE_SQL)
    conn.execute(CREATE_VIDEO_PUSH_LOG_TABLE_SQL)
    conn.execute("create index if not exists idx_video_push_log_created on video_push_log(created_at)")
    conn.execute(CREATE_MAIL_ACCOUNTS_TABLE_SQL)
    # Cot 'slot' them sau - DB cu (tao truoc khi co dong nay trong
    # CREATE_MAIL_ACCOUNTS_TABLE_SQL) can ALTER TABLE rieng, cung ly do nhu xtra/fail_reason.
    existing_mail_cols = {row[1] for row in conn.execute("pragma table_info(mail_accounts)").fetchall()}
    if "slot" not in existing_mail_cols:
        conn.execute("alter table mail_accounts add column slot text default ''")
    if "profile" not in existing_mail_cols:
        conn.execute("alter table mail_accounts add column profile text default ''")
    conn.execute(
        "create index if not exists idx_mail_accounts_market on mail_accounts(market)"
    )
    conn.execute(CREATE_CANDIDATE_ROOT_SEEN_TABLE_SQL)
    conn.execute(
        "create index if not exists idx_candidate_root_seen_item on candidate_root_seen(itemid, market)"
    )
    conn.execute(CREATE_KEYWORDS_TABLE_SQL)
    # Cau hinh CÀO (sort_type/filter_types/sold_min/hoa hong tien toi thieu) KHONG con luu
    # tren bang keywords - chung la cau hinh "khi chay worker" (gui tu tab Vận hành GPM qua
    # /api/gpm/worker/start, worker gui kem moi /api/keywords/page_done), con import chi luu
    # tu khoa + market + danh muc. DB tao o phien truoc con giu may cot nay -> bo di cho
    # dong nhat (khong anh huong gi du lieu khac).
    kw_cols = {row[1] for row in conn.execute("pragma table_info(keywords)").fetchall()}
    for legacy_col in ("sort_type", "filter_types", "sold_min", "comm_money_min", "seller_com_min"):
        if legacy_col in kw_cols:
            try:
                conn.execute(f"alter table keywords drop column {legacy_col}")
            except sqlite3.OperationalError:
                pass  # SQLite cu khong ho tro DROP COLUMN - de nguyen cot thua (khong doc nua)
    conn.execute(
        "create index if not exists idx_keywords_claim on keywords(status, market)"
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
                     seller_commission_vnd_min=None, auto_assign=None, dongvanfb_api_key=None,
                     auto_assign_market=None):
    """Cap nhat MOT PHAN nguong loc (tham so None = giu nguyen gia tri cu). Tra ve settings
    day du sau khi cap nhat - dung cho endpoint POST /api/settings. dongvanfb_api_key dung
    chung cho tab "Tao tai khoan Shopee" (mua mail + lay code) - luu chung 1 dong settings
    nay thay vi bang rieng vi chi co DUY NHAT 1 key toan cuc, khong nhieu nhu video_machines.
    auto_assign_market: '' (chuoi rong) nghia la NGUOI DUNG CHU DINH chon "Tat ca market" -
    PHAI phan biet voi None (khong truyen = giu nguyen gia tri cu), nen dung is not None thay
    vi truthy check nhu cac cot khac."""
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
        "auto_assign_market": (
            auto_assign_market if auto_assign_market is not None else current["auto_assign_market"]
        ),
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            "update settings set promoted_7d_max=?, sold_min=?, seller_commission_vnd_min=?, "
            "auto_assign=?, dongvanfb_api_key=?, auto_assign_market=? where id=1",
            (new_vals["promoted_7d_max"], new_vals["sold_min"], new_vals["seller_commission_vnd_min"],
             new_vals["auto_assign"], new_vals["dongvanfb_api_key"], new_vals["auto_assign_market"]),
        )
        conn.commit()
    finally:
        conn.close()
    return get_settings(db_path)


# --- Quan ly danh sach 'accounts' (ten hien thi + duong dan Chrome profile) cho tab
# "Tai khoan" cua dashboard - launch qua chrome_launcher.py (POST /api/accounts/<name>/launch).
# Cot 'serial' la ten lich su (tung dung cho serial ADB thoi con u2/AutoX), hien tai luu
# duong dan THU MUC PROFILE Chrome day du - khong con lien quan thiet bi that. ---

def add_device(db_path, name, serial):
    """Them 1 account moi, hoac DOI TEN neu profile path (cot 'serial') da ton tai
    (ON CONFLICT tren cot 'serial' - unique)."""
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
    """Danh sach account (Chrome profile) da dang ky, sap theo ten."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("select * from devices order by name collate nocase asc").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_device(db_path, device_id, name, serial):
    """Cap nhat ten va/hoac duong dan profile (serial) cho 1 account da dang ky, theo id."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update devices set name = ?, serial = ? where id = ?",
            (name, serial, device_id),
        )
        conn.commit()
    finally:
        conn.close()


def remove_device(db_path, serial):
    """Xoa 1 account khoi danh sach dang ky (KHONG anh huong toi cac dong 'products'
    da claim boi 'serial' (profile path) nay truoc do - assigned_key la du lieu lich su,
    van giu nguyen)."""
    conn = _connect(db_path)
    try:
        conn.execute("delete from devices where serial = ?", (serial,))
        conn.commit()
    finally:
        conn.close()


def item_exists(db_path, itemid, market=None):
    """market=None: kiem tra itemid nay ton tai o BAT KY market nao (dung cho cac cho
    chi can biet 'da tung thay itemid nay chua', khong quan tam market nao)."""
    conn = _connect(db_path)
    try:
        if market is not None:
            row = conn.execute(
                "select 1 from products where itemid = ? and market = ?", (str(itemid), market)
            ).fetchone()
        else:
            row = conn.execute("select 1 from products where itemid = ?", (str(itemid),)).fetchone()
        return row is not None
    finally:
        conn.close()


def delete_item(db_path, itemid, market):
    """Xoa 1 dong bat ky (root hoac related) - dung cho tab quan ly san pham tren UI khi
    nguoi dung muon bo 1 item cu the (vd nham/khong con phu hop) ra khoi DB. Neu xoa 1
    ROOT thi cac 'related' cua no (groupid=itemid) VAN GIU NGUYEN (khong cascade) - tranh
    xoa nham hang loat, nguoi dung tu xoa them neu thuc su muon don sach ca nhom.

    market BAT BUOC - itemid khong con la khoa duy nhat toan cuc (2 market co the trung
    so itemid), thieu market se co nguy co xoa NHAM dong cua market khac."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "delete from products where itemid=? and market=?", (str(itemid), market)
        )
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


def map_v2_data_to_row(data, link_type=None, groupid=None, status_link="pending", market=None):
    """Map tu response_json['data'] cua API offer/product_v2 sang dict dung ten cot
    trong bang 'products'. link_type ('root'/'related') va groupid (itemid cua root lien
    quan) KHONG the suy ra tu chinh du lieu cua item - nguoi goi phai tu truyen vao.

    market=None: TU SUY tu data['product_link'] qua market_from_link() (an toan mac dinh -
    hau het noi goi ham nay deu co san product_link that trong response). Truyen market
    tuong minh khi noi goi da biet chac (vd ke thua tu root cua nhom).

    Vai cot chua ro ngu nghia/don vi thuc te ben du an 0-database (merged_link,
    category_group, don vi price) - de None/best-effort thay vi doan bua, ghi chu ro
    de dieu chinh sau khi doi chieu voi 1-cao."""
    if market is None:
        market = market_from_link(data.get("product_link"))
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
    """INSERT OR REPLACE 1 dong vao bang products theo (market, itemid) (unique)."""
    conn = _connect(db_path)
    try:
        cols = [c for c in COLUMNS if c in row]
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        values = [row[c] for c in cols]
        conn.execute(
            f"insert into products ({col_list}) values ({placeholders}) "
            f"on conflict(market, itemid) do update set "
            + ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("itemid", "market")),
            values,
        )
        conn.commit()
    finally:
        conn.close()


def clear_all_items(db_path=DB_PATH_DEFAULT, market=None):
    """Xoa du lieu san pham (root + related, moi trang thai) - dung khi nguoi dung bam "Xoa
    toan bo du lieu" tren UI de lam sach test/chay lai tu dau. market: None/'' xoa TOAN BO
    (hanh vi cu, moi thi truong); truyen 1 ma market cu the (vd 'ph') chi xoa DUNG thi
    truong do, giu nguyen du lieu cac thi truong khac. KHONG dong bang 'devices' (tai
    khoan/profile la cau hinh, khong phai du lieu cao duoc). Tra ve so dong da xoa."""
    conn = _connect(db_path)
    try:
        if market:
            cur = conn.execute("delete from products where market=?", (market,))
        else:
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


def get_item(db_path=DB_PATH_DEFAULT, itemid=None, market=None):
    """Lay dung 1 dong theo itemid (khop chinh xac, khong phai LIKE nhu fetch_all_items) -
    dung de kiem tra trang thai 1 item NGAY sau khi cao/cham diem xong. market=None: khop
    BAT KY market nao (co the tra ve 1 trong nhieu dong trung itemid o cac market khac
    nhau - truyen market khi da biet chac de tranh mo ho)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if market is not None:
            row = conn.execute(
                "select * from products where itemid = ? and market = ?", (str(itemid), market)
            ).fetchone()
        else:
            row = conn.execute("select * from products where itemid = ?", (str(itemid),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_all_items(db_path=DB_PATH_DEFAULT, link_type=None, status_link=None, search=None,
                     groupid=None, market=None, limit=500):
    """Doc lai danh sach item da luu trong DB - dung cho tab 'Xem DB' cua Streamlit UI.
    link_type: loc theo 'root'/'related' (None/'' = tat ca). status_link: loc theo
    'pending'/'done'/'fail' (None/'' = tat ca). search: loc itemid/name/shop_name co chua
    chuoi nay (khong phan biet hoa thuong). groupid: loc DUNG 1 nhom (khop chinh xac,
    khong phai LIKE - groupid la khoa nhom, khong can tim gan dung) - dung cho tab 'San
    pham da cao' khi nguoi dung muon xem het thanh vien cua 1 nhom cu the. market: loc DUNG
    1 thi truong (None/'' = tat ca) - dung cho dropdown loc o tab 'San pham da cao'. Dung
    tham so hoa (?) cho ca gia tri loc lan LIMIT - khong noi chuoi nguoi dung truc tiep vao
    cau SQL (tranh SQL injection)."""
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
        if groupid:
            where.append("groupid = ?")
            params.append(str(groupid))
        if market:
            where.append("market = ?")
            params.append(market)
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


def count_all_items(db_path=DB_PATH_DEFAULT, link_type=None, status_link=None, search=None,
                    groupid=None, market=None):
    """Dem tong so dong khop cac bo loc (cung dieu kien voi fetch_all_items) - dung de bao
    'Tong X link' tren tab 'San pham da cao' khi nguoi dung chon bo loc (khong gioi han 200)."""
    conn = _connect(db_path)
    try:
        where, params = [], []
        if link_type:
            where.append("link_type = ?")
            params.append(link_type)
        if status_link:
            where.append("status_link = ?")
            params.append(status_link)
        if groupid:
            where.append("groupid = ?")
            params.append(str(groupid))
        if market:
            where.append("market = ?")
            params.append(market)
        if search:
            where.append("(itemid LIKE ? OR name LIKE ? OR shop_name LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where_sql = f"where {' and '.join(where)}" if where else ""
        return conn.execute(f"select count(*) from products {where_sql}", params).fetchone()[0]
    finally:
        conn.close()


def video_ready_items(db_path=DB_PATH_DEFAULT, market=None, search=None, groupid=None, limit=200):
    """San pham DANG DU DIEU KIEN TAO VIDEO (cung dieu kien voi list_video_push_candidates():
    root da co merged_link, chua tao job VideoAI, co product_link). Tra (items, total) de UI
    bao tong so link theo trang thai nay."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where = ["link_type='root'", "merged_link is not null", "job_id is null", "product_link is not null"]
        params = []
        if market:
            where.append("market = ?")
            params.append(market)
        if groupid:
            where.append("groupid = ?")
            params.append(str(groupid))
        if search:
            where.append("(itemid LIKE ? OR name LIKE ? OR shop_name LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        where_sql = f"where {' and '.join(where)}"
        total = conn.execute(f"select count(*) from products {where_sql}", params).fetchone()[0]
        rows = conn.execute(
            f"select * from products {where_sql} order by id asc limit ?", params + [limit]
        ).fetchall()
        return [dict(r) for r in rows], total
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
        market = market_from_link(product_link)
        conn.execute(
            "insert into products (itemid, product_link, link_type, groupid, status_link, xtra, fail_reason, market) "
            "values (?, ?, ?, ?, 'fail', 0, 'no_affiliate', ?) "
            "on conflict(market, itemid) do update set status_link = 'fail', xtra = 0, fail_reason = 'no_affiliate'",
            (str(itemid), product_link, link_type, str(groupid) if groupid else None, market),
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
        market = market_from_link(product_link)
        conn.execute(
            "insert into products (itemid, product_link, link_type, groupid, status_link, fail_reason, market) "
            "values (?, ?, ?, ?, 'fail', 'not_found', ?) "
            "on conflict(market, itemid) do update set status_link = 'fail', fail_reason = 'not_found'",
            (str(itemid), product_link, link_type, str(groupid) if groupid else None, market),
        )
        conn.commit()
    finally:
        conn.close()


def claim_pending(db_path, device_key, market, limit=1, lease_seconds=1800, sold_min=None, groupid=None):
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
    nay). market BAT BUOC - itemid khong con la khoa duy nhat toan cuc, thieu market co
    the claim/tra ve NHAM dong cua market khac trung so itemid."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        where = [
            "status_link = 'pending'",
            "market = ?",
            "affiliate_promoted_last_7days is null",
            "(assigned_key is null or claimed_at is null "
            "or claimed_at < datetime('now', ?))",
        ]
        params = [market, f"-{lease_seconds} seconds"]
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
                f"where market = ? and itemid in ({placeholders})",
                [device_key, market] + ids,
            )
            claimed = conn.execute(
                f"select * from products where market = ? and itemid in ({placeholders})",
                [market] + ids,
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
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select distinct p.groupid, p.market from products p "
            "join products root on root.itemid = p.groupid and root.market = p.market "
            "where p.link_type = 'related' and p.status_link = 'pending' "
            "and root.status_link != 'pending'"
        ).fetchall()
        return [{"groupid": r["groupid"], "market": r["market"]} for r in rows]
    finally:
        conn.close()


def release_claim(db_path, itemid, market):
    """Xoa assigned_key/claimed_at cho 1 item (goi sau khi xu ly xong, du thanh cong hay
    loi ky thuat) - de item van con 'pending' kha dung NGAY cho thiet bi khac, khong can
    doi het lease_seconds. market BAT BUOC - itemid khong con la khoa duy nhat toan cuc."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update products set assigned_key = null, claimed_at = null "
            "where itemid = ? and market = ?",
            (str(itemid), market),
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
# de tai dung metrics tranh goi lai). Unique THEO MARKET: 1 cap (market, itemid) chi thuoc
# 1 group - itemid TRAN khong con la khoa duy nhat (2 market khac nhau co the trung so).
# =====================================================================================

def import_roots_as_pending(db_path, links, cat_id=None, cat_ids=None):
    """Nap link root -> insert link_type='root', status='pending', groupid=itemid, market
    suy tu domain cua chinh link (market_from_link). Bo qua (market, itemid) da ton tai
    (insert or ignore) - 2 link cung itemid nhung KHAC market (vd shopee.ph vs shopee.co.th)
    van duoc luu thanh 2 dong rieng biet, khong con bi de len nhau nhu truoc. Tra ve so root
    MOI them.

    cat_id: 1 gia tri DUY NHAT ap dung cho CA LO link nay - dung khi khong co cat_ids (vd
    dashboard dan link thu cong, luon None). cat_ids: danh sach SONG SONG voi links (cung
    do dai) - cat_id RIENG cho TUNG link, uu tien hon cat_id neu duoc truyen. Can thiet vi
    Shopee Product Link Collector co the cao NHIEU danh muc lien tiep truoc khi bam "Đẩy vao
    DB" 1 lan - neu chi dung 1 cat_id chung cho ca lo se gan SAI cat_id (lay danh muc cuoi
    cung) cho cac link cao tu danh muc truoc do (xem shopee_collector.user.js, gan cat_id
    ngay luc quet tung link thay vi luc day vao DB). cat_name tra cuu RIENG cho tung link
    theo market cua chinh link do (shopee_categories.cat_name_for)."""
    import shopee_categories
    if cat_ids is not None and len(cat_ids) != len(links):
        raise ValueError("cat_ids phai cung do dai voi links")
    conn = _connect(db_path)
    try:
        added = 0
        for idx, link in enumerate(links):
            m = re.search(r"/product/\d+/(\d+)", link) or re.search(r"(\d+)\s*$", str(link).strip())
            if not m:
                continue
            itemid = m.group(1)
            market = market_from_link(link)
            item_cat_id = cat_ids[idx] if cat_ids is not None else cat_id
            cat_name = shopee_categories.cat_name_for(market, item_cat_id) if item_cat_id is not None else None
            cur = conn.execute(
                "insert or ignore into products "
                "(itemid, product_link, link_type, groupid, status_link, market, cat_id, cat_name) "
                "values (?, ?, 'root', ?, 'pending', ?, ?, ?)",
                (itemid, link, itemid, market, item_cat_id, cat_name),
            )
            added += cur.rowcount
        conn.commit()
        return added
    finally:
        conn.close()


def claim_root(db_path, device_key, market, lease_seconds=1800):
    """Claim NGUYEN TU (BEGIN IMMEDIATE) 1 root 'pending' DUNG market nay, chua bi thiet
    bi khac giu (hoac lease het). Tra ve dict dong root, hoac None neu het root. market BAT
    BUOC - 1 tab Tampermonkey chi goi duoc API that cua DUNG market no dang mo (location.origin),
    giao nham root market khac se lam tab do that bai het cac request that (va lam root
    bi bo do oan uong vi 'fail')."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "select * from products where link_type='root' and status_link='pending' and market=? "
            "and (assigned_key is null or claimed_at is null or claimed_at < datetime('now', ?)) "
            "order by id asc limit 1",
            (market, f"-{lease_seconds} seconds"),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "update products set assigned_key=?, claimed_at=current_timestamp where itemid=? and market=?",
            (device_key, row["itemid"], market),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def assign_root_to_worker(db_path, itemid, device_key, market):
    """Giao THU CONG 1 root cu the cho 1 device_key cu the (dashboard chon tay) - dung lai
    dung 2 cot assigned_key/claimed_at nhu claim_root() (root van la 1 hang doi, chi khac
    AI duoc quyen chon: nguoi dung tren dashboard, khong phai tab tu claim). NGUYEN TU:
    tu choi neu root khong ton tai/khong phai 'pending', hoac dang bi device KHAC giu (va
    lease chua het). market BAT BUOC - itemid khong con la khoa duy nhat toan cuc (dashboard
    da co san market tu dong duoc chon trong danh sach root, xem list_roots_with_counts()).
    Tra ve {"ok": True, "root": {...}} hoac {"ok": False, "error": "..."}."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "select * from products where itemid=? and market=? and link_type='root'",
            (str(itemid), market),
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
            "update products set assigned_key=?, claimed_at=current_timestamp where itemid=? and market=?",
            (device_key, str(itemid), market),
        )
        conn.commit()
        updated = conn.execute(
            "select * from products where itemid=? and market=?", (str(itemid), market)
        ).fetchone()
        return {"ok": True, "root": dict(updated)}
    finally:
        conn.close()


def get_assigned_root_for_worker(db_path, device_key, market):
    """Root (neu co) dang duoc giao cho dung device_key nay va van con 'pending' - worker
    (Tampermonkey) goi lien tuc de biet co viec moi hay chua. market BAT BUOC (tab nay chi
    goi duoc API that cua dung market no dang mo).

    Neu chua co gi duoc giao THU CONG va setting 'auto_assign' dang bat: tu dong lay 1
    root 'pending' con trong (chua ai giu) giao luon cho worker nay, tai dung nguyen
    claim_root() (van la cung 1 co che nguyen tu/lease, chi khac la duoc goi tu day thay
    vi worker tu goi truc tiep nhu truoc). Nho vay nguoi dung KHONG can tu bam "Giao viec"
    tung root mot khi bat che do nay - worker ranh se tu nhan viec o lan poll ke tiep.

    'auto_assign_market' (rong = tat ca market) gioi han auto-claim CHI ap dung cho 1
    market cu the - worker o market khac se KHONG tu nhan viec (van co the duoc giao thu
    cong qua assign_root_to_worker()). Dung de nguoi dung chi muon chay tu dong 1 thi
    truong tai 1 thoi diem (vd dang tap trung PH, chua muon TH tu dong chay theo)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select * from products where link_type='root' and status_link='pending' "
            "and assigned_key=? and market=? order by claimed_at asc limit 1",
            (device_key, market),
        ).fetchone()
        if row:
            return dict(row)
    finally:
        conn.close()

    settings = get_settings(db_path)
    auto_assign_market = settings.get("auto_assign_market") or ""
    if settings["auto_assign"] and (not auto_assign_market or auto_assign_market == market):
        return claim_root(db_path, device_key, market)
    return None


def worker_heartbeat(db_path, device_key, status, current_root=None, market=None):
    """Upsert trang thai 'song' cua 1 tab Tampermonkey - CHI de dashboard hien thi
    (idle/working/blocked + root dang lam + market dang phuc vu), khong dung de gan viec
    (xem assign_root_to_worker())."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into workers (device_key, status, current_root, last_heartbeat, market) "
            "values (?, ?, ?, current_timestamp, ?) "
            "on conflict(device_key) do update set status=excluded.status, "
            "current_root=excluded.current_root, last_heartbeat=excluded.last_heartbeat, "
            "market=excluded.market",
            (device_key, status, current_root, market),
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


def remove_worker(db_path, device_key):
    """Xoa 1 worker khoi bang 'workers' - dung cho nut 'Xoa' o tab 'Van hanh' (vd device_key
    go nham, tab/profile khong con dung nua nhung van con hien trong danh sach vi tung
    heartbeat truoc do). Nha luon claim (assigned_key/claimed_at) ma DUNG device_key nay
    dang giu tren bang products (xem release_claims_for_device()) - tranh de lai root "mo
    coi" bi khoa boi 1 device_key khong con ai heartbeat, khong bao gio duoc nha tu nhien
    (worker khac khong the "cuop" claim con hieu luc). Xoa xong, worker nay se bien mat khoi
    danh sach ngay - neu tab Tampermonkey do van con dang chay va tiep tuc heartbeat, no se
    tu xuat hien lai o lan bao cao ke tiep (khong ngan duoc viec do, chi xoa duoc 1 lan).
    Tra ve {"removed": bool, "released_claims": n}."""
    released = release_claims_for_device(db_path, device_key)
    kw_released = release_keyword_claims_for_device(db_path, device_key)
    conn = _connect(db_path)
    try:
        cur = conn.execute("delete from workers where device_key=?", (device_key,))
        conn.commit()
        return {"removed": cur.rowcount > 0, "released_claims": released,
                "released_keywords": kw_released}
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


def try_assign_verified(db_path, row: dict, groupid, sold_min=None,
                         seller_commission_vnd_min=None):
    """Dung cho pipeline web/BFS moi (nhieu Chrome profile chay SONG SONG, moi profile tu
    duyet 1 root khac nhau) - khac assign_member() o cho: assign_member() coi moi dong
    'pending' (chua verify) la con TRONG, cho phep group khac de len duoc (dung cho phone
    pipeline cu, cao/gan tuan tu tung root 1 nen hiem khi dung race that). Voi BFS song
    song, 2 nhom co the cung phat hien 1 candidate qua similar_product_offers cua 2 root
    khac nhau - dong 'pending' do insert_related_as_pending() seed truoc GIU CHO nhom da
    seed no, nhom khac phai bi tu choi (khong duoc "cuop" dua vao viec chua verify).

    row: dict tu map_v2_data_to_row() (du affiliate_promoted_last_7days/sold/seller_commission
    That, tuc DA goi offer/product that cho item nay). Ham nay CHI dung cho 'related' (KHONG
    PHAI root - root xet o verify_root()) - tu 2026-08-29, related CHI can dat sold +
    seller_commission (KHONG con xet promoted_7d_max/so KOL 7 ngay, theo yeu cau nguoi dung -
    xem select_l1_l2_candidates.passes_criteria_related()). Tinh passes_criteria_related()
    luon, roi:
      - Khong dat  -> luu 'cached' (groupid=null, tai dung metrics lan sau, khong goi lai API)
      - Dat + o duoc  -> gan 'member' cua groupid nay
      - Dat nhung da bi nhom KHAC giu (root cua no, hoac 'member'/'pending' groupid khac)
        -> tu choi, KHONG ghi de

    Tra ve dict {"outcome": "assigned"|"already_member"|"claimed_by_other"|"failed_criteria",
    "group_member_count": int} - group_member_count chi co gia tri khi outcome="assigned"
    hoac "already_member" (dung de vong lap BFS phia goi biet khi nao dat GROUP_TARGET ma dung).

    market luon lay tu row["market"] (da duoc map_v2_data_to_row() tu suy tu product_link) -
    moi tra cuu/khoa itemid ben duoi deu KEM market de tranh dung nham dong cua market
    khac trung so itemid (xem CREATE_TABLE_SQL: unique la (market, itemid), khong con la
    itemid don le)."""
    import select_l1_l2_candidates as l1l2
    if sold_min is None or seller_commission_vnd_min is None:
        _settings = get_settings(db_path)
        if sold_min is None:
            sold_min = _settings["sold_min"]
        if seller_commission_vnd_min is None:
            seller_commission_vnd_min = _settings["seller_commission_vnd_min"]

    itemid = str(row["itemid"])
    market = row["market"]
    groupid = str(groupid)
    metrics = l1l2._row_metrics(row)
    passes = l1l2.passes_criteria_related(metrics, sold_min, seller_commission_vnd_min)

    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        ex = conn.execute(
            "select status_link, groupid, link_type from products where itemid=? and market=?",
            (itemid, market),
        ).fetchone()
        # Ke thua cat_id/cat_name cua CHINH root (groupid=itemid cua root) cho san pham
        # tuong tu khi no thuc su duoc gan vao group (2 nhanh 'member' ben duoi) - root
        # khong co danh muc (cao ngoai tab danh muc) thi cac dong nay cung NULL, dung nhu
        # thiet ke (xem import_roots_as_pending()).
        root_cat = conn.execute(
            "select cat_id, cat_name from products where itemid=? and market=? and link_type='root'",
            (groupid, market),
        ).fetchone()
        root_cat_id = root_cat["cat_id"] if root_cat else None
        root_cat_name = root_cat["cat_name"] if root_cat else None

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
                    f"update products set {', '.join(f'{c}=?' for c in setcols)} where itemid=? and market=?",
                    [r[c] for c in setcols] + [itemid, market],
                )
            conn.commit()
            return {"outcome": "failed_criteria", "group_member_count": None}

        if ex is None:
            r = dict(row)
            r.update(link_type="related", groupid=groupid, status_link="member",
                      cat_id=root_cat_id, cat_name=root_cat_name)
            cols = [c for c in COLUMNS if c in r]
            conn.execute(
                f"insert into products ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
                [r[c] for c in cols],
            )
            conn.commit()
            count = count_group_members(db_path, groupid, market)
            return {"outcome": "assigned", "group_member_count": count}

        status, gid, lt = ex["status_link"], ex["groupid"], ex["link_type"]
        if lt == "root":
            conn.commit()
            return {"outcome": "claimed_by_other", "group_member_count": None}
        if status == "member":
            conn.commit()
            if gid == groupid:
                return {
                    "outcome": "already_member",
                    "group_member_count": count_group_members(db_path, groupid, market),
                }
            return {"outcome": "claimed_by_other", "group_member_count": None}
        if gid is not None and gid != groupid:
            # 'pending'/'cached' nhung da bi nhom khac seed/giu truoc
            conn.commit()
            return {"outcome": "claimed_by_other", "group_member_count": None}

        r = dict(row)
        r.update(link_type="related", groupid=groupid, status_link="member",
                  cat_id=root_cat_id, cat_name=root_cat_name)
        setcols = [c for c in COLUMNS if c in r and c != "itemid"]
        conn.execute(
            f"update products set {', '.join(f'{c}=?' for c in setcols)} where itemid=? and market=?",
            [r[c] for c in setcols] + [itemid, market],
        )
        conn.commit()
        count = count_group_members(db_path, groupid, market)
        return {"outcome": "assigned", "group_member_count": count}
    finally:
        conn.close()


def seed_and_claim_candidates(db_path, groupid, related_items, market=None):
    """Nhu insert_related_as_pending() nhung ATOMIC + tra ve DUNG cac item_id ma NHOM NAY
    dang thuc su giu quyen (moi them lan nay, hoac da la cua nhom nay tu seed truoc do, hoac
    duoc "nhan lai" tu trang thai 'cached' - xem nhanh duoi) - dung cho BFS song song (nhieu
    Chrome profile/root cung luc): item da bi nhom KHAC giu that su se KHONG co trong ket qua
    tra ve, phia goi biet ngay khong can xep vao hang doi cua minh nua (khoi ton 1 request
    that toi Shopee roi bi tu choi o try_assign_verified()).

    Luon ghi vao candidate_root_seen cho MOI item nhan duoc (bat ke co duoc claim hay
    khong) - phuc vu recheck_cached_candidates() sau nay (xem ghi chu bang do), KHONG anh
    huong logic claim.

    market=None: tu suy tu market cua CHINH root (groupid=itemid cua root) - san pham
    tuong tu Shopee tra ve luon CUNG market voi root dang xem, nen day la nguon dang tin
    cay nhat (khong phu thuoc domain cua tung product_link rieng le co the thieu/le)."""
    groupid = str(groupid)
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if market is None:
            root = conn.execute(
                "select market from products where itemid=? and link_type='root'", (groupid,)
            ).fetchone()
            market = root["market"] if root else None
        claimed = []
        for item in related_items:
            itemid = item.get("item_id")
            link = item.get("product_link")
            if not itemid or not link:
                continue
            itemid = str(itemid)
            sold = (item.get("batch_item_for_item_card_full") or {}).get("sold")
            conn.execute(
                "insert or ignore into candidate_root_seen (itemid, market, groupid) values (?, ?, ?)",
                (itemid, market, groupid),
            )
            ex = conn.execute(
                "select groupid, status_link from products where itemid=? and market=?",
                (itemid, market),
            ).fetchone()
            if ex is None:
                conn.execute(
                    "insert into products (itemid, product_link, sold, link_type, groupid, status_link, market) "
                    "values (?, ?, ?, 'related', ?, 'pending', ?)",
                    (itemid, link, sold, groupid, market),
                )
                claimed.append(itemid)
            elif ex["groupid"] == groupid:
                claimed.append(itemid)
            elif ex["status_link"] == "cached" and ex["groupid"] is None:
                # Da bi loai truoc do (khong con thuoc nhom nao - xem try_assign_verified())
                # - cho root nay "nhan lai" de duoc danh gia lai voi dieu kien HIEN TAI (co
                # the da doi tu luc bi cache). Sua bug thuc te 2026-09-02: truoc day 1 item
                # cached bi khoa cung VINH VIEN, khong bao gio duoc xem xet lai du dieu kien
                # loc thay doi ra sao.
                conn.execute(
                    "update products set groupid=?, status_link='pending' where itemid=? and market=?",
                    (groupid, itemid, market),
                )
                claimed.append(itemid)
            # else: da thuoc nhom/root khac that su (member, hoac dang pending cua nhom khac) -
            # bo qua, khong tra ve.
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
    userscript quyet dinh loai luon hay tiep tuc lay related - xem runBfsForRoot()), KHONG tu gan/doi
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
    market = row["market"]
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
            f"update products set {', '.join(f'{c}=?' for c in metric_cols)} "
            f"where itemid=? and market=?",
            [row[c] for c in metric_cols] + [itemid, market],
        )
        conn.commit()
    finally:
        conn.close()

    metrics = l1l2._row_metrics(row)
    passes = l1l2.passes_criteria(metrics, promoted_7d_max, sold_min, seller_commission_vnd_min)
    return {"passes": passes, "metrics": metrics}


def filter_new_itemids(db_path, itemids, market=None):
    """Loc ra cac itemid CHUA TUNG xuat hien trong DB - dung cho vong lap BFS phia
    Tampermonkey de bo qua ngay cac candidate da biet (da la root/member/pending/cached
    cua bat ky nhom nao), tranh xep vao hang doi roi lai bi tu choi o try_assign_verified()
    (do da bi nhom khac giu) - kiem tra som, tiet kiem request that toi Shopee.

    market=None: khop 'da tung xuat hien o BAT KY market nao' (dang khong duoc goi tu
    dau trong pipeline hien tai - endpoint /api/items/filter_new con lai tu thiet ke cu,
    xem chu thich o affiliate_scrape_server.py). Truyen market khi biet chac de tranh loc
    nham theo du lieu cua market khac."""
    if not itemids:
        return []
    conn = _connect(db_path)
    try:
        ids = [str(i) for i in itemids]
        placeholders = ",".join("?" for _ in ids)
        if market is not None:
            rows = conn.execute(
                f"select itemid from products where market = ? and itemid in ({placeholders})",
                [market] + ids,
            ).fetchall()
        else:
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


def count_group_members(db_path, groupid, market):
    """So san pham tuong tu 'member' da gan vao group (KHONG tinh root). market BAT BUOC -
    groupid (= itemid cua root) khong con la khoa duy nhat toan cuc, 2 root o 2 market
    khac nhau co the trung so groupid."""
    conn = _connect(db_path)
    try:
        return conn.execute(
            "select count(*) from products where groupid=? and market=? and status_link='member'",
            (str(groupid), market),
        ).fetchone()[0]
    finally:
        conn.close()


def list_roots_with_counts(db_path, status=None, search=None, market=None, limit=200):
    """Danh sach root + so member da gom cua tung group - dung cho UI xem group. market
    loc dung 1 thi truong cu the (dung cho dropdown loc o tab "Van hanh" - xem
    count_roots_by_market() cho bang tong hop theo TAT CA market).

    Truoc day dem member bang 1 truy van RIENG cho TUNG root (N+1 query - toi da 200 truy
    van/lan goi). Da do dac thuc te (2026-08-27, py-spy + mo phong 5 tab BFS ghi song
    song): endpoint nay chiem CPU rat nang (200 vong lap Python + truy van tuan tu tren 1
    process Flask), la nguyen nhan chinh gay "CPU 100%" nguoi dung bao cao khi chay nhieu
    tab BFS - dashboard tu poll endpoint nay moi 5s, cong don voi ghi song song tu cac tab
    lam moi lan goi cham hon, co the CHONG CHAT nhieu lan goi cung luc, cang lam CPU te
    hon. Gio dem TAT CA group trong 1 (hoac vai) truy van GROUP BY duy nhat thay vi N+1."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where = ["link_type='root'"]
        params = []
        if status:
            where.append("status_link=?")
            params.append(status)
        if market:
            where.append("market=?")
            params.append(market)
        if search:
            where.append("(itemid like ? or name like ?)")
            params += [f"%{search}%", f"%{search}%"]
        rows = conn.execute(
            f"select * from products where {' and '.join(where)} order by id asc limit ?",
            params + [limit],
        ).fetchall()
        count_map = {}
        if rows:
            markets = {r["market"] for r in rows}
            for m in markets:
                for grow in conn.execute(
                    "select groupid, count(*) as cnt from products "
                    "where market=? and status_link='member' group by groupid",
                    (m,),
                ).fetchall():
                    count_map[(grow["groupid"], m)] = grow["cnt"]
        out = []
        for r in rows:
            d = dict(r)
            d["member_count"] = count_map.get((d["itemid"], d["market"]), 0)
            out.append(d)
        return out
    finally:
        conn.close()


def count_roots_by_market(db_path):
    """Tong hop so root theo TUNG market (pending chua giao/dang giao/done/fail) - dung
    cho bang "Root theo market" o tab "Van hanh" (hien thi ro rang thay vi chi 1 con so
    tong gop nhu stats-bar) VA de dashboard biet co nhung market nao de dien vao dropdown
    chon market cho auto-assign. Sap theo market A-Z, root chua nhan dien duoc market
    (market NULL, thuong do link la, khong khop domain nao trong market_from_link()) gom
    chung vao 1 dong '(chua ro)' o cuoi de khong bi that lac."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "select "
            "  coalesce(market, '') as market, "
            "  count(*) as total, "
            "  sum(case when status_link='pending' and assigned_key is null then 1 else 0 end) as pending, "
            "  sum(case when status_link='pending' and assigned_key is not null then 1 else 0 end) as claimed, "
            "  sum(case when status_link='done' then 1 else 0 end) as done, "
            "  sum(case when status_link='fail' then 1 else 0 end) as fail "
            "from products where link_type='root' group by coalesce(market, '') order by market asc"
        ).fetchall()
        return [
            {
                "market": market or None,
                "total": total,
                "pending": pending,
                "claimed": claimed,
                "done": done,
                "fail": fail,
            }
            for market, total, pending, claimed, done, fail in rows
        ]
    finally:
        conn.close()


def category_stats(db_path, market=None):
    """Tong so san pham (ca root lan related, moi status_link) theo TUNG danh muc - dung
    cho khoi 'Thong ke nganh hang' o tab 'Van hanh'. market: gioi han 1 thi truong cu the
    (None/'' = tat ca thi truong).

    Gop thanh 1 hang theo TEN danh muc (khong phan biet hoa/thuong). Ly do gop 2 cap:
      - truoc day GROUP BY (cat_id, cat_name): cung cat_id ma cat_name lech (NULL/hoa thuong)
        -> 1 danh muc nổ thanh nhieu hang trung.
      - gop theo (market, cat_id): giai quyet tren, NHUNG nhieu cat_id khac nhau lai co cung
        ten (products/cat-db luu ten trung, vd nhieu 'Home Appliances') -> ten van lap lai.
    Ten hien thi uu tien bang cat-db chuan, fallback ten dang luu trong DB; xem 'Tat ca' thi
    ghi them market vao nhan (cat_id cap rieng tung market)."""
    import shopee_categories
    conn = _connect(db_path)
    try:
        where = "where market=?" if market else ""
        params = [market] if market else []
        rows = conn.execute(
            f"select market, cat_id, count(*) as total from products {where} "
            "group by market, cat_id order by total desc",
            params,
        ).fetchall()
        stored = {}
        stored_where = "where market=? and cat_name is not null" if market else "where cat_name is not null"
        stored_params = [market] if market else []
        for m, cid, name in conn.execute(
            f"select market, cat_id, cat_name from products {stored_where} "
            "group by market, cat_id, cat_name",
            stored_params,
        ).fetchall():
            stored.setdefault((m, cid), name)

        # Gop theo ten (khong phan biet hoa/thuong): count cong don; cat_id giu cua nhom lon nhat
        merged = {}
        for m, cat_id, cnt in rows:
            name = None
            if cat_id is not None:
                name = shopee_categories.cat_name_for(m, cat_id)
            if not name:
                name = stored.get((m, cat_id))
            if not name:
                name = f"cat_id {cat_id} (chưa rõ tên)" if cat_id is not None else "Chưa gán danh mục"
            if not market:
                name = f"{name} ({m.upper()})"
            key = (m, str(name).strip().lower())
            prev = merged.get(key)
            if prev is None:
                merged[key] = {"market": m, "cat_name": name, "count": cnt, "cat_id": cat_id}
            else:
                prev["count"] += cnt
                if cnt > prev.get("_max", -1):
                    prev["_max"] = cnt
                    prev["cat_id"] = cat_id
        categories = []
        for (_, _k), d in merged.items():
            d.pop("_max", None)
            categories.append(d)
        categories.sort(key=lambda x: (-x["count"], str(x["cat_name"]).lower()))
        total_all = sum(c["count"] for c in categories)
        return {"total": total_all, "categories": categories}
    finally:
        conn.close()


def list_group_members(db_path, groupid, market):
    """San pham tuong tu 'member' cua 1 group, sap theo promoted_7d tang dan (tot nhat
    truoc). market BAT BUOC - xem count_group_members()."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select * from products where groupid=? and market=? and status_link='member' "
            "order by affiliate_promoted_last_7days asc",
            (str(groupid), market),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def finish_root(db_path, itemid, market):
    """Root xu ly xong -> status='done', nha claim, tu tinh lai cot merged_link cho ca
    group (khong can nguoi dung tu bam). market BAT BUOC - xem count_group_members()."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update products set status_link='done', assigned_key=null, claimed_at=null "
            "where itemid=? and market=?",
            (str(itemid), market),
        )
        conn.commit()
    finally:
        conn.close()
    compute_merged_links(db_path, itemid, market)


def compute_merged_links(db_path, groupid, market, batch_size=6):
    """Gop moi batch_size link LIEN TIEP (theo thu tu them vao group - id tang dan) thanh
    1 chuoi noi bang '|', ghi de vao cot merged_link cua TUNG dong thuoc dung batch do
    (ca batch_size dong deu mang chung 1 gia tri merged_link). Neu ROOT cua group dat 3
    tieu chi (tinh la 1/60) thi no la link #1 trong day, cac 'member' xep tiep theo sau
    (thu tu id tang dan). Batch cuoi co the le (<batch_size) neu group khong du. Goi tu
    finish_root() - khong can nguoi dung tu tinh lai. market BAT BUOC - xem
    count_group_members(). Tra ve tong so link da gop."""
    import select_l1_l2_candidates as l1l2
    settings = get_settings(db_path)
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        root = conn.execute(
            "select * from products where itemid=? and market=? and link_type='root'",
            (str(groupid), market),
        ).fetchone()
        members = conn.execute(
            "select * from products where groupid=? and market=? and status_link='member' "
            "order by id asc",
            (str(groupid), market),
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
                conn.execute(
                    "update products set merged_link=? where itemid=? and market=?",
                    (merged, itemid, market),
                )
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
        conn.row_factory = sqlite3.Row
        roots = conn.execute(
            "select itemid, market from products where link_type='root'"
        ).fetchall()
    finally:
        conn.close()
    total_links = 0
    for root in roots:
        total_links += compute_merged_links(db_path, root["itemid"], root["market"])
    return {"roots_processed": len(roots), "total_links": total_links}


def reset_all_insufficient_roots(db_path):
    """Dua VE 'pending' TAT CA root 'done' nhung CHUA du GROUP_TARGET (6: root + 5 related,
    xem tampermonkey_affiliate_group_scraper.user.js) thanh vien - dung cho nut bam hang
    loat, thay vi phai tu 'Dat lai' tung root mot (vd sau dot loi 403 khien nhieu root bi
    bo do o cung 1 lan). CHI tinh la "thieu" khi CHINH root da dat 3 tieu chi (con < 5
    related) - root KHONG dat tieu chi la bi loai VINH VIEN theo thiet ke moi (BFS dung
    ngay, khong lay related), KHONG phai truong hop "thieu" nen KHONG dua vao day de tranh
    cao lai vo ich 1 root da biet chac se bi loai. Tra ve {"reset_count": n, "itemids": [...]}."""
    import select_l1_l2_candidates as l1l2
    settings = get_settings(db_path)
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select p.*, "
            "(select count(*) from products m where m.groupid=p.itemid and m.market=p.market "
            "and m.status_link='member') as member_count "
            "from products p where p.link_type='root' and p.status_link='done'"
        ).fetchall()
        to_reset = []
        for r in rows:
            if r["member_count"] >= 5:
                continue
            root_metrics = l1l2._row_metrics(r)
            root_passes = l1l2.passes_criteria(
                root_metrics, settings["promoted_7d_max"], settings["sold_min"],
                settings["seller_commission_vnd_min"],
            )
            if root_passes:
                to_reset.append(r)
        for r in to_reset:
            conn.execute(
                "update products set status_link='pending', assigned_key=null, claimed_at=null, "
                "fail_reason=null where itemid=? and market=?",
                (r["itemid"], r["market"]),
            )
        conn.commit()
        return {"reset_count": len(to_reset), "itemids": [r["itemid"] for r in to_reset]}
    finally:
        conn.close()


def release_disqualified_root_members(db_path, market=None):
    """Giai phong 'related' cua cac root DA TUNG dat (dang giu member that su) nhung KHONG
    CON dat dieu kien loc HIEN TAI (nguoi dung vua doi settings) - dua related ve lai
    'cached' (groupid=null) de co co hoi duoc gan cho 1 root KHAC dang thuc su dat chuan
    (qua recheck_cached_candidates(), goi NGAY SAU ham nay trong cung 1 lan bam nut tren
    UI). Nguyen tac nguoi dung chot 2026-09-02: "root khong dat dieu kien thi luon luon
    khong duoc giu related" - PHAI dung MOI LUC settings thay doi, khong chi luc cao lan
    dau (luc do BFS da tu loai root khong dat truoc khi lay related roi, xem
    runBfsForRoot() - ke ho DUY NHAT con lai la root DA TUNG dat o qua khu).

    Xoa merged_link cua CA root va cac related vua giai phong (khong con hop le de tao
    video nua - neu khong xoa, compute_merged_links() se KHONG tu xoa gium vi no CHI ghi de
    cho cac dong con nam trong danh sach hop le, khong dong toi dong bi loai ra, merged_link
    cu se con sot lai va bi list_video_push_candidates() hieu nham la "van con hop le").

    Tra ve {"roots_disqualified": n, "released_itemids": [...]}."""
    import select_l1_l2_candidates as l1l2
    settings = get_settings(db_path)
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        market_sql = "and p.market=? " if market else ""
        params = [market] if market else []
        rows = conn.execute(
            "select p.*, "
            "(select count(*) from products m where m.groupid=p.itemid and m.market=p.market "
            "and m.status_link='member') as member_count "
            f"from products p where p.link_type='root' and p.status_link='done' {market_sql}",
            params,
        ).fetchall()
        released = []
        disqualified_roots = []
        for r in rows:
            if r["member_count"] == 0:
                continue  # khong co gi de giai phong
            root_metrics = l1l2._row_metrics(r)
            root_passes = l1l2.passes_criteria(
                root_metrics, settings["promoted_7d_max"], settings["sold_min"],
                settings["seller_commission_vnd_min"],
            )
            if root_passes:
                continue
            members = conn.execute(
                "select itemid from products where groupid=? and market=? and status_link='member'",
                (r["itemid"], r["market"]),
            ).fetchall()
            for m in members:
                conn.execute(
                    "update products set groupid=null, status_link='cached', merged_link=null "
                    "where itemid=? and market=?",
                    (m["itemid"], r["market"]),
                )
                released.append(m["itemid"])
            conn.execute(
                "update products set merged_link=null where itemid=? and market=?",
                (r["itemid"], r["market"]),
            )
            disqualified_roots.append(r["itemid"])
        conn.commit()
        return {"roots_disqualified": len(disqualified_roots), "released_itemids": released}
    finally:
        conn.close()


def recheck_cached_candidates(db_path, market=None):
    """Quet lai TOAN BO candidate dang 'cached' (da bi loai truoc do vi khong dat tieu chi
    'related') doi chieu voi dieu kien LOC HIEN TAI (nguoi dung co the vua doi) - HOAN TOAN
    KHONG goi API Shopee, chi dung du lieu + quan he da luu san trong candidate_root_seen
    (xem CREATE_CANDIDATE_ROOT_SEEN_TABLE_SQL, ghi tu seed_and_claim_candidates()). Yeu cau
    nguoi dung 2026-09-02: "co san cache, doi dieu kien thi phai co co che check lai de ap
    dung luon thay vi phai cao lai".

    Voi moi item cached NAY DA DAT tieu chi related: tim cac root tung de xuat no (theo
    thu tu seen_at som nhat truoc), chon root DAU TIEN van con thieu member (<5) VA CHINH
    root do van dang dat 3 tieu chi (dung settings hien tai, tren metrics da luu san cua
    root) - gan item vao root do (status_link='member'). Item khong con root nao phu hop
    (het lien quan hoac tat ca da du 5) thi giu nguyen 'cached'.

    LUU Y quan trong: CHI hoat dong voi item duoc cache TU LUC co bang candidate_root_seen
    (2026-09-02) tro di - item cached truoc do khong co du lieu quan he nay trong bang, se
    bi bo qua o day (khong loi) va van can cao lai root tuong ung 1 lan de he thong biet lai
    no thuoc root nao (xem seed_and_claim_candidates()).

    Tra ve {"checked": n, "assigned": n, "itemids": [...]}."""
    import select_l1_l2_candidates as l1l2
    settings = get_settings(db_path)
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        market_sql = "and p.market=? " if market else ""
        params = [market] if market else []
        cached_rows = conn.execute(
            "select * from products p where p.link_type='related' and p.status_link='cached' "
            f"{market_sql}",
            params,
        ).fetchall()
        checked = 0
        assigned = []
        touched_roots = set()  # {(groupid, market), ...} - can compute_merged_links() lai sau
        for row in cached_rows:
            checked += 1
            metrics = l1l2._row_metrics(row)
            if not l1l2.passes_criteria_related(
                metrics, settings["sold_min"], settings["seller_commission_vnd_min"]
            ):
                continue
            itemid = row["itemid"]
            item_market = row["market"]
            candidate_roots = conn.execute(
                "select cr.groupid, "
                "(select count(*) from products m where m.groupid=cr.groupid and m.market=cr.market "
                " and m.status_link='member') as member_count "
                "from candidate_root_seen cr where cr.itemid=? and cr.market=? order by cr.seen_at asc",
                (itemid, item_market),
            ).fetchall()
            target_root = None
            for cr in candidate_roots:
                if cr["member_count"] >= 5:
                    continue
                root_row = conn.execute(
                    "select * from products where itemid=? and market=? and link_type='root'",
                    (cr["groupid"], item_market),
                ).fetchone()
                if root_row is None:
                    continue
                root_metrics = l1l2._row_metrics(root_row)
                if not l1l2.passes_criteria(
                    root_metrics, settings["promoted_7d_max"], settings["sold_min"],
                    settings["seller_commission_vnd_min"],
                ):
                    continue
                target_root = cr["groupid"]
                break
            if target_root is None:
                continue
            root_cat = conn.execute(
                "select cat_id, cat_name from products where itemid=? and market=? and link_type='root'",
                (target_root, item_market),
            ).fetchone()
            conn.execute(
                "update products set groupid=?, status_link='member', cat_id=?, cat_name=? "
                "where itemid=? and market=?",
                (
                    target_root, root_cat["cat_id"] if root_cat else None,
                    root_cat["cat_name"] if root_cat else None, itemid, item_market,
                ),
            )
            assigned.append(itemid)
            touched_roots.add((target_root, item_market))
        conn.commit()
    finally:
        conn.close()
    # compute_merged_links() tu mo ket noi rieng - goi SAU khi conn o tren da dong/commit,
    # tranh giao dich long nhau. Cap nhat lai merged_link cho dung group vua them member moi
    # (khac finish_root() la lan dau tinh - o day la tinh LAI vi thanh phan nhom da doi).
    for groupid, group_market in touched_roots:
        compute_merged_links(db_path, groupid, group_market)
    return {"checked": checked, "assigned": len(assigned), "itemids": assigned}


def mark_root_failed(db_path, itemid, market, reason):
    """Danh dau 1 root la 'fail' (KHONG PHAI 'done') + nha claim - dung khi API tra loi
    THAT SU (vd Shopee bao 'invalid item id', item bi go/sai id...), KHONG PHAI truong
    hop "chay het ung vien truoc khi du 60" (do la 'done' + insufficient, xem finish_root()).
    BAT BUOC phai goi ham nay thay vi de nguyen 'pending' khi gap loi - da gap bug thuc te:
    de nguyen 'pending' + con assigned_key khien worker cu nhan lai DUNG root loi do moi
    lan poll, lap vo han. Nguoi dung van co the tu "Dat lai" tren dashboard neu muon thu lai.
    market BAT BUOC - xem count_group_members()."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update products set status_link='fail', assigned_key=null, claimed_at=null, "
            "fail_reason=? where itemid=? and market=? and link_type='root'",
            (reason, str(itemid), market),
        )
        conn.commit()
    finally:
        conn.close()


def reset_root_to_pending(db_path, itemid, market):
    """Dua 1 root ve lai 'pending' + nha claim (assigned_key/claimed_at) - dung khi nguoi
    dung muon giao lai/thu lai 1 root da 'done' (vd bi bo do do loi 403 tam thoi, khong
    phai that su het ung vien) hoac 'fail'. KHONG dong toi cac 'related' da gan group cua
    lan chay truoc - BFS lan sau se tu bo qua chung qua seed_and_claim_candidates() (da
    thuoc group nay roi thi van tinh la "cua nhom nay", khong bi mat). market BAT BUOC -
    xem count_group_members()."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "update products set status_link='pending', assigned_key=null, claimed_at=null, "
            "fail_reason=null where itemid=? and market=? and link_type='root'",
            (str(itemid), market),
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


def reclassify_status(db_path, link_type=None, groupid=None, market=None, promoted_7d_max=None,
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
        if market:
            where.append("market = ?")
            params.append(market)
        rows = conn.execute(
            f"select itemid, market, sold, seller_commission, affiliate_promoted_last_7days "
            f"from products where {' and '.join(where)}",
            params,
        ).fetchall()

        updated = {"done": 0, "fail": 0}
        for row in rows:
            metrics = l1l2._row_metrics(row)
            status = ("done" if l1l2.passes_criteria(
                metrics, promoted_7d_max, sold_min, seller_commission_vnd_min
            ) else "fail")
            conn.execute(
                "update products set status_link = ? where itemid = ? and market = ?",
                (status, row["itemid"], row["market"]),
            )
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


def set_video_machine_tag(db_path, machine_id, tag):
    """Doi Tag (thu muc) cua 1 may tao video - dung cho nut 'Sua' canh 'Xoa' o tab 'Tao
    video'. Tra ve False neu khong tim thay machine_id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "update video_machines set tag=? where id=?", (tag, machine_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_video_push_candidates(db_path, limit=200, market=None):
    """Hang doi cho tinh nang 'Tao video': da co merged_link nhung CHUA tao xong task
    VideoAI (job_id con null) - bao gom ca dong da day cache tu lan chay truoc
    (cache_uploaded=1) lan dong chua day. Sap FIFO (id tang dan). market: gioi han CHI 1
    thi truong (None/'' = tat ca) - dung cho dropdown chon market o tab 'Tao video'.

    CHI lay link_type='root' (theo yeu cau nguoi dung 2026-08-29: chi tao video bang link
    root, KHONG tao rieng cho tung 'related' nua) - merged_link cua dong root van chua ca
    toi da 6 link (root + related cung nhom, xem compute_merged_links()) nen video van co
    du ngu canh san pham lien quan, chi la KHONG con tao THEM 1 video rieng cho moi related."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        market_sql = "and market=? " if market else ""
        params = ([market] if market else []) + [limit]
        rows = conn.execute(
            "select * from products where merged_link is not null and job_id is null "
            f"and product_link is not null and link_type='root' {market_sql}order by id asc limit ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_cache_uploaded(db_path, itemid_market_pairs):
    """Danh dau da day cache VideoAI thanh cong cho danh sach (itemid, market) - lan chay
    sau se bo qua buoc day cache cho cac dong nay (chi con thieu buoc tao task). market
    BAT BUOC trong tung cap - xem count_group_members()."""
    if not itemid_market_pairs:
        return
    conn = _connect(db_path)
    try:
        conn.executemany(
            "update products set cache_uploaded=1 where itemid=? and market=?",
            [(str(i), m) for i, m in itemid_market_pairs],
        )
        conn.commit()
    finally:
        conn.close()


def mark_video_jobs(db_path, itemid_market_jobid_triples):
    """Ghi job_id VideoAI tra ve cho tung (itemid, market) - co job_id nghia la XONG, se
    khong con xuat hien trong list_video_push_candidates() nua (khong day/tao lai)."""
    if not itemid_market_jobid_triples:
        return
    conn = _connect(db_path)
    try:
        conn.executemany(
            "update products set job_id=? where itemid=? and market=?",
            [(job_id, str(itemid), market) for itemid, market, job_id in itemid_market_jobid_triples],
        )
        conn.commit()
    finally:
        conn.close()


def log_video_push(db_path, market, machine_id, machine_name, requested_limit, done, pushed, created, errors):
    """Ghi 1 dong nhat ky cho 1 lan goi POST /api/videos/push - xem CREATE_VIDEO_PUSH_LOG_TABLE_SQL.
    errors: list [{itemid, reason}, ...] tu push_videos() - luu nguyen dang JSON (list rong ->
    '[]', khong luu null) de tab "Tao Video" hien lai chi tiet loi khi truy xuat sau nay."""
    import json
    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into video_push_log "
            "(market, machine_id, machine_name, requested_limit, done, pushed, created, error_count, error_detail) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (market, machine_id, machine_name, requested_limit, done, pushed, created,
             len(errors or []), json.dumps(errors or [], ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def list_video_push_log(db_path, market=None, limit=100, offset=0):
    """Lich su tao video (moi dong = 1 lan goi push_videos()), moi nhat truoc - dung cho tab
    "Tao Video" tra cuu lai lich su qua nhieu phien lam viec (khac videoLog() phia client,
    chi song trong bo nho tab, mat khi F5). Tra ve {"rows": [...], "total": n} de dashboard
    tu ve phan trang."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        market_sql = " where market=?" if market else ""
        params = [market] if market else []
        total = conn.execute(
            f"select count(*) from video_push_log{market_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"select * from video_push_log{market_sql} order by id desc limit ? offset ?",
            params + [limit, offset],
        ).fetchall()
        return {"rows": [dict(r) for r in rows], "total": total}
    finally:
        conn.close()


def clear_video_push_log(db_path, market=None):
    """Xoa lich su "Nhat ky tao video" (bang video_push_log) - dung cho nut 'Xoa nhat ky' o
    tab 'Tao video'. CHI xoa dong nhat ky (khong dung/anh huong toi job_id/cache_uploaded
    cua san pham - muon dat lai trang thai do thi dung reset_video_jobs()/nut 'Dat lai trang
    thai' rieng). market: gioi han CHI 1 thi truong dang chon tren dropdown (None/'' = xoa
    TAT CA). Tra ve so dong da xoa."""
    conn = _connect(db_path)
    try:
        market_sql = " where market=?" if market else ""
        params = [market] if market else []
        cur = conn.execute(f"delete from video_push_log{market_sql}", params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def reset_video_jobs(db_path, market=None):
    """Dat lai trang thai 've cho tao video' cho san pham DA co job_id (xoa job_id, giu
    nguyen cache_uploaded vi cache van con dung, chi can tao lai task) - dung cho nut 'Dat
    lai trang thai' o tab 'Tao video'. market: gioi han CHI 1 thi truong (None/'' = tat ca),
    dung dropdown chon market cua tab. Tra ve so dong da reset."""
    conn = _connect(db_path)
    try:
        market_sql = " and market=?" if market else ""
        params = [market] if market else []
        cur = conn.execute(
            f"update products set job_id=null where job_id is not null{market_sql}",
            params,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def count_video_push_stats(db_path):
    """So lieu tong quan cho tab 'Tao video' tren dashboard.

    QUAN TRONG: PHAI loc them link_type='root', giong het dieu kien cua
    list_video_push_candidates() (ham THUC SU chon ung vien khi bam 'Tao video') - da tung
    co bug thuc te o day: compute_merged_links() ghi merged_link cho CA related trong cung
    nhom (khong rieng root), nen dem "merged_link is not null" suong se dem lan CA cac dong
    related (khong bao gio duoc chon de tao video/khong bao gio co job_id), khien UI hien
    "con lai chua tao" > 0 that su MAC DU list_video_push_candidates() da tra ve rong (dung
    dong nguoi dung bao cao: 'giao dien van bao con link du dieu kien nhung bam tao video
    thi he thong bao het')."""
    conn = _connect(db_path)
    try:
        eligible = conn.execute(
            "select count(*) from products where merged_link is not null and link_type='root'"
        ).fetchone()[0]
        cache_uploaded = conn.execute(
            "select count(*) from products where merged_link is not null and link_type='root' and cache_uploaded=1"
        ).fetchone()[0]
        job_created = conn.execute(
            "select count(*) from products where merged_link is not null and link_type='root' and job_id is not null"
        ).fetchone()[0]
        return {"eligible": eligible, "cache_uploaded": cache_uploaded, "job_created": job_created}
    finally:
        conn.close()


def count_video_push_stats_by_market(db_path):
    """Nhu count_video_push_stats() nhung gom theo TUNG market - dung cho bang "Link theo
    market" o tab "Tao video" (hien thi ro thay vi chi 1 con so tong gop). Sap theo market
    A-Z, dong nao merged_link con thieu market (khong khop domain nao trong
    market_from_link() - hiem, thuong do link nhap tay sai dinh dang) gom chung vao 1 dong
    '(chua ro)' o cuoi de khong bi that lac.

    Cung PHAI loc link_type='root' - xem ghi chu chi tiet o count_video_push_stats()."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "select "
            "  coalesce(market, '') as market, "
            "  count(*) as eligible, "
            "  sum(case when cache_uploaded=1 then 1 else 0 end) as cache_uploaded, "
            "  sum(case when job_id is not null then 1 else 0 end) as job_created "
            "from products where merged_link is not null and link_type='root' "
            "group by coalesce(market, '') order by market asc"
        ).fetchall()
        return [
            {
                "market": market or None,
                "eligible": eligible,
                "cache_uploaded": cache_uploaded,
                "job_created": job_created,
            }
            for market, eligible, cache_uploaded, job_created in rows
        ]
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

def _insert_mail_account_rows(db_path, parsed_rows):
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
    return _insert_mail_account_rows(db_path, parsed_rows)


def add_mail_accounts_manual(db_path, lines):
    """Them mail nguoi dung tu dan thu cong (moi dong dung dinh dang "full info" giong mail
    mua tren dongvanfb: "email|password|refresh_token|client_id" - xem parse_mail_line()).
    Khac add_mail_accounts_from_buy() o cho day la nguoi dung tu go/dan nen de sai dinh dang
    hon mail dongvanfb tra ve san, nen tra them so dong loi dinh dang de bao lai cho nguoi
    dung sua ('invalid'). account_type='manual', order_code=None de phan biet voi mail mua."""
    import dongvanfb_client

    parsed_rows = []
    invalid = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        parsed = dongvanfb_client.parse_mail_line(line)
        if not parsed:
            invalid += 1
            continue
        parsed_rows.append((
            line, parsed["email"], parsed["password"], parsed["refresh_token"],
            parsed["client_id"], "manual", None,
        ))
    added = _insert_mail_account_rows(db_path, parsed_rows)
    return {"added": added, "invalid": invalid}


def import_mail_accounts_from_rows(db_path, rows):
    """Nhap lai danh sach 'Tai khoan da tao' tu file .xlsx dung dinh dang cot cua
    export_mail_accounts_xlsx() (Full info, Email, PassEmail, Shopee_id, Device, Profile,
    Slot, Market, Shopee_code, Thoi gian tao) - dung cho nut 'Import Excel' canh 'Xuat Excel',
    vd khoi phuc du lieu sau khi bam nham 'Xoa tat ca' hoac chuyen sang may khac. Moi dong la
    1 dict {"full_info", "shopee_id", "device", "profile", "slot", "market", "shopee_code"}.
    Email/PassEmail trong file KHONG dung truc tiep - luon parse lai tu full_info (nguon du
    lieu goc, co ca refresh_token/client_id ma 2 cot do khong co) bang
    dongvanfb_client.parse_mail_line(). Bo qua dong full_info rong/khong parse duoc, VA bo
    qua dong co full_info DA TON TAI trong bang (tranh nhan doi khi lo import lai cung 1
    file). Tra ve {"added", "skipped_duplicate", "invalid"}."""
    import dongvanfb_client

    def _cell_str(value):
        """Excel co the tra so (int/float) cho cot tuong "text" (vd slot '1' go lai thanh
        so) - ep ve str truoc .strip() de tranh AttributeError."""
        if value is None:
            return ""
        return str(value).strip()

    conn = _connect(db_path)
    try:
        existing_full_info = {
            row[0] for row in conn.execute("select full_info from mail_accounts").fetchall()
        }
        parsed_rows = []
        invalid = 0
        skipped_duplicate = 0
        for row in rows:
            full_info = _cell_str(row.get("full_info"))
            if not full_info:
                continue
            if full_info in existing_full_info:
                skipped_duplicate += 1
                continue
            parsed = dongvanfb_client.parse_mail_line(full_info)
            if not parsed:
                invalid += 1
                continue
            existing_full_info.add(full_info)
            parsed_rows.append((
                full_info, parsed["email"], parsed["password"], parsed["refresh_token"],
                parsed["client_id"], "import", None,
                _cell_str(row.get("shopee_id")), _cell_str(row.get("device")),
                _cell_str(row.get("profile")), _cell_str(row.get("slot")),
                _cell_str(row.get("market")) or "PH",
                _cell_str(row.get("shopee_code")) or None,
            ))
        if not parsed_rows:
            return {"added": 0, "skipped_duplicate": skipped_duplicate, "invalid": invalid}
        conn.executemany(
            "insert into mail_accounts (full_info, email, password, refresh_token, "
            "client_id, account_type, order_code, shopee_id, device, profile, slot, "
            "market, shopee_code) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            parsed_rows,
        )
        conn.commit()
        return {"added": len(parsed_rows), "skipped_duplicate": skipped_duplicate, "invalid": invalid}
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


def update_mail_account_fields(db_path, account_id, shopee_id=None, device=None, profile=None, slot=None, market=None):
    """Cap nhat MOT PHAN cot nguoi dung tu nhap (tham so None = giu nguyen), dung cho nut
    luu tung dong tren UI khi doi Shopee_id/Device/Profile/Slot/Market."""
    current = get_mail_account(db_path, account_id)
    if not current:
        return None
    new_vals = {
        "shopee_id": shopee_id if shopee_id is not None else current["shopee_id"],
        "device": device if device is not None else current["device"],
        "profile": profile if profile is not None else current["profile"],
        "slot": slot if slot is not None else current["slot"],
        "market": market if market is not None else current["market"],
    }
    conn = _connect(db_path)
    try:
        conn.execute(
            "update mail_accounts set shopee_id=?, device=?, profile=?, slot=?, market=? where id=?",
            (
                new_vals["shopee_id"], new_vals["device"], new_vals["profile"],
                new_vals["slot"], new_vals["market"], account_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_mail_account(db_path, account_id)


def find_mail_accounts_by_shopee_id(db_path, shopee_id, exclude_id=None):
    """Danh sach cac mail KHAC (loai tru exclude_id - chinh dong dang sua) dang dung CUNG
    shopee_id nay - dung cho canh bao chong nhap trung o UI (tab 'Tạo tài khoản Shopee').
    shopee_id rong khong tinh la trung (nhieu dong cung de trong la binh thuong)."""
    if not shopee_id:
        return []
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        if exclude_id is not None:
            rows = conn.execute(
                "select id, email from mail_accounts where shopee_id=? and id<>?",
                (shopee_id, exclude_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "select id, email from mail_accounts where shopee_id=?", (shopee_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


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


def update_mail_account_refresh_token(db_path, account_id, refresh_token):
    """Ghi de refresh_token MOI ma Microsoft cap kem moi lan doc mail thanh cong qua Graph
    truc tiep (xem microsoft_mail_client.fetch_shopee_code()). Chi la thoi quen an toan -
    da kiem chung THAT (2026-08-24) token CU van dung duoc binh thuong sau khi "bi thay" boi
    ban moi (Microsoft khong thu hoi ngay), nen KHONG goi ham nay cung khong sao."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update mail_accounts set refresh_token=? where id=?",
            (refresh_token, account_id),
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


# =====================================================================================
# CAO ROOT AFF THEO TU KHOA (Cao_root_aff.txt) - tu khoa la hang doi viec RIENG cho
# cdp_keyword_worker.mjs. Root dat tieu chi duoc insert vao bang 'products' chung
# (link_type='root', status_link='pending', groupid=itemid) de Root Navigator/navigator
# xu ly tiep - keyword crawler CHI la nguon BOM ROOT MOI, khong tu verify/gan group.
# =====================================================================================

def _parse_pct_rate(raw):
    """'5%' / '11 %' / '-' / None -> float ty le % (5.0). Tra 0.0 khi khong doc duoc."""
    if raw in (None, "", "-"):
        return 0.0
    try:
        return float(str(raw).replace("%", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def _normalize_keyword(text):
    """Chuan hoa 1 tu khoa: bo khoang trang 2 dau, gom khoang trang lien tiep."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def import_keywords(db_path, market, keywords, cat_id=None, cat_name=None):
    """Nap 1 LO tu khoa vao bang 'keywords' (cung market + cung cat_id/cat_name cho CA LO -
    dung quyet dinh "gắn cả lô khi người dùng ấn nút import"). CAC GIA TRI SORT/FILTER/
    SOLD/HOA HONG KHONG LUU O DAY - chung la cau hinh "khi cào" (worker gui kem moi trang,
    xem keyword_page_done). Tu khoa duy nhat tren 1 market (so sanh khong phan biet
    hoa/thuong de tranh 'Mini Projector' vs 'mini projector' trung nhau). Tra ve so lieu:
    {"added": n, "duplicates": n, "skipped": n, "keywords": [dong keyword moi them]}."""
    if not market:
        return {"added": 0, "duplicates": 0, "skipped": 0, "keywords": []}
    try:
        cat_id_int = int(cat_id) if cat_id not in (None, "") else None
    except (TypeError, ValueError):
        cat_id_int = None

    rows_out = []
    added = dup = skipped = 0
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        existing = {
            str(r["keyword"]).lower()
            for r in conn.execute(
                "select keyword from keywords where market=?", (market,)
            ).fetchall()
        }
        seen_in_batch = set()
        for raw in keywords:
            kw = _normalize_keyword(raw)
            if not kw:
                skipped += 1
                continue
            key = kw.lower()
            if key in existing or key in seen_in_batch:
                dup += 1
                continue
            seen_in_batch.add(key)
            cur = conn.execute(
                "insert into keywords (market, keyword, cat_id, cat_name) values (?, ?, ?, ?)",
                (market, kw, cat_id_int, cat_name),
            )
            if cur.rowcount:
                added += 1
                row = conn.execute("select * from keywords where id=?", (cur.lastrowid,)).fetchone()
                if row:
                    rows_out.append(dict(row))
            else:
                dup += 1
        conn.commit()
    finally:
        conn.close()
    return {"added": added, "duplicates": dup, "skipped": skipped, "keywords": rows_out}


def list_keywords(db_path, market=None, status=None, search=None, cat_id=None, limit=500):
    """Danh sach tu khoa (MOI nhat truoc) khop bo loc - cho UI quan ly + bo loc market/category."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where, params = [], []
        if market:
            where.append("market = ?")
            params.append(market)
        if status:
            where.append("status = ?")
            params.append(status)
        if cat_id not in (None, ""):
            where.append("cat_id = ?")
            params.append(int(cat_id))
        if search:
            where.append("lower(keyword) like ?")
            params.append("%" + str(search).lower() + "%")
        where_sql = f"where {' and '.join(where)}" if where else ""
        rows = conn.execute(
            f"select * from keywords {where_sql} order by id desc limit ?",
            params + [int(limit)],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def keyword_summary(db_path, market=None):
    """Tong hop so lieu tu khoa cho dashboard (dang cho/dang cao/xong/loi + root da bom)."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        where, params = "", []
        if market:
            where = "where market=?"
            params = [market]
        rows = conn.execute(
            f"select status, count(*) n, coalesce(sum(roots_inserted),0) ri, "
            f"coalesce(sum(roots_found),0) rf, coalesce(sum(dup_skipped),0) d "
            f"from keywords {where} group by status",
            params,
        ).fetchall()
        total = {"pending": 0, "in_progress": 0, "done": 0, "error": 0}
        roots = {"inserted": 0, "found": 0, "dup": 0}
        for r in rows:
            total[r["status"]] = r["n"]
            roots["inserted"] += r["ri"]
            roots["found"] += r["rf"]
            roots["dup"] += r["d"]
        total["all"] = sum(total.values())
        markets = [
            dict(r)
            for r in conn.execute(
                "select market, count(*) n from keywords group by market order by market",
            ).fetchall()
        ]
        return {"counts": total, "roots": roots, "markets": markets}
    finally:
        conn.close()


def get_keyword(db_path, keyword_id):
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select * from keywords where id=?", (int(keyword_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def reset_keyword(db_path, keyword_id):
    """Dat lai 1 tu khoa ve 'pending' (xoa claim/checkpoint/dem/lỗi) - tu khoa se duoc
    cao lai tu dau. KHONG xoa cac root da bom vao products (dung thiet ke)."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "update keywords set status='pending', assigned_key=null, claimed_at=null, "
            "checkpoint_page=0, roots_found=0, roots_inserted=0, dup_skipped=0, "
            "last_error=null, updated_at=current_timestamp where id=?",
            (int(keyword_id),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def reset_keywords(db_path, market=None, status=None):
    """Dat lai HANG LOAT tu khoa khop bo loc (bo loc rong = tat ca) ve 'pending'."""
    conn = _connect(db_path)
    try:
        where, params = [], []
        if market:
            where.append("market = ?")
            params.append(market)
        if status:
            where.append("status = ?")
            params.append(status)
        where_sql = f"where {' and '.join(where)}" if where else ""
        cur = conn.execute(
            f"update keywords set status='pending', assigned_key=null, claimed_at=null, "
            f"checkpoint_page=0, roots_found=0, roots_inserted=0, dup_skipped=0, "
            f"last_error=null, updated_at=current_timestamp {where_sql}",
            params,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_keyword(db_path, keyword_id):
    """Xoa 1 tu khoa khoi danh sach (KHONG dong cham toi root da bom - xem reset_keyword)."""
    conn = _connect(db_path)
    try:
        cur = conn.execute("delete from keywords where id=?", (int(keyword_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_keywords(db_path, market=None, status=None):
    conn = _connect(db_path)
    try:
        where, params = [], []
        if market:
            where.append("market = ?")
            params.append(market)
        if status:
            where.append("status = ?")
            params.append(status)
        where_sql = f"where {' and '.join(where)}" if where else ""
        cur = conn.execute(f"delete from keywords {where_sql}", params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def release_keyword_claims_for_device(db_path, device_key):
    """Tra ve 'pending' cac tu khoa 'in_progress' dang bi DUNG device_key giu - dung khi xoa
    worker/device (remove_worker) de tu khoa do khong bi khoa mo coi den het lease."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "update keywords set status='pending', assigned_key=null, claimed_at=null, "
            "updated_at=current_timestamp where status='in_progress' and assigned_key=?",
            (device_key,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def claim_keyword(db_path, device_key, market, lease_seconds=3600):
    """Claim NGUYEN TU (BEGIN IMMEDIATE) 1 tu khoa cho worker cua DUNG market nay:
      1) uu tien tu khoa 'in_progress' con sot cua CHINH device nay (worker vua restart),
      2) tu khoa 'in_progress' ma lease het han (worker cu chet giua chung - cao lai tu dau,
         dedup dua tren bang products nen an toan),
      3) tu khoa 'pending' chua ai giu (hoac lease het).
    Tra ve dict dong tu khoa (kem cat_id/thresholds de worker dieu khien search), hoac None."""
    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "select * from keywords where market=? and ("
            "  (status='in_progress' and assigned_key=?) "
            "  or (status='in_progress' and (claimed_at is null or claimed_at < datetime('now', ?))) "
            "  or (status='pending' and (assigned_key is null or claimed_at is null "
            "     or claimed_at < datetime('now', ?)))"
            ") order by (case when status='in_progress' and assigned_key=? then 0 else 1 end), "
            "id asc limit 1",
            (market, device_key, f"-{lease_seconds} seconds", f"-{lease_seconds} seconds", device_key),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "update keywords set status='in_progress', assigned_key=?, claimed_at=current_timestamp, "
            "last_error=null, updated_at=current_timestamp where id=?",
            (device_key, row["id"]),
        )
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def _display_price_from_int(raw):
    """Gia hien thi (don vi tien market) quy doi tu gia API (int price) - dung CUNG quy uoc
    voi phan uoc tinh seller_commission cua candidate related (xem nav_complete trong
    affiliate_scrape_server.py): price > 100000 -> /100000 (vd 19900000 -> 199.00), nguoc
    lai giu nguyen (da la gia hien thi roi)."""
    try:
        p = int(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    return (p / 100000.0) if p > 100000 else float(p)


def _commission_money_from_item(item, rate_pct):
    """Uoc tinh SO TIEN hoa hong cua 1 item trong response /api/v3/offer/product/list -
    response chi co ty le % (seller_commission_rate), khong kem so tien that, nen quy doi:
    Tien hoa hong ~ rate% * gia hien thi (don vi tien cua market: PH=PHP, TH=THB, VN=VND...).
    Dung cho: (1) loc 'comm_money_min' o keyword_page_done, (2) ghi seller_commission vao
    root de dashboard hien thi so tien uoc tinh. Tra 0.0 neu khong tinh duoc."""
    batch = item.get("batch_item_for_item_card_full") or {}
    return round((float(rate_pct) / 100.0) * _display_price_from_int(batch.get("price")), 2)


def _keyword_passes_filters(item, sold_min, comm_money_min):
    """Kiem tra 1 item trong response product/list co dat tieu chi root cua keyword khong:
      - sold (luot ban) >= sold_min
      - Tien hoa hong uoc tinh (seller_commission_rate% * gia hien thi) >= comm_money_min -
        "seller_com quy doi thanh tien" (mac dinh 0 = khong loc theo tien).
    Tra ve None neu item khong hop le / khong dat, hoac dict metric da parse."""
    batch = item.get("batch_item_for_item_card_full") or {}
    itemid = str(item.get("item_id") or batch.get("itemid") or "")
    if not itemid or not item.get("product_link"):
        return None
    try:
        sold = int(batch.get("sold") or 0)
    except (TypeError, ValueError):
        sold = 0
    if sold_min and sold < sold_min:
        return None
    rate = _parse_pct_rate(item.get("seller_commission_rate") or item.get("default_commission_rate"))
    comm_money = _commission_money_from_item(item, rate)
    if comm_money_min and comm_money < comm_money_min:
        return None
    return {"itemid": itemid, "sold": sold, "rate": rate,
            "comm_money": comm_money, "batch": batch}


def keyword_page_done(db_path, keyword_id, device_key, market, page_offset, page_limit,
                      total_count, items, sold_min=None, comm_money_min=None,
                      filter_types=None):
    """Nhan 1 trang (page_offset) item that do CHINH TRANG affiliate goi (token hop le) tu
    worker: loc tieu chi -> insert root pending moi vao 'products' (bo qua item da ton tai
    BAT KY dau trong DB -> dup_skipped) -> cap nhat checkpoint/dem cua tu khoa. Trang cuoi
    (trang rong / it hon page_limit / da toi duoi total_count) -> status='done', nha claim.
    items: list raw data.list[] tu response product/list.

    CAC NGUONG LOC (sold_min, comm_money_min, filter_types) la CAU HINH KHI CAO - worker gui
    kem MOI trang nay (tu cau hinh bat dau o tab Vận hành GPM), KHONG luu tren keyword o luc
    import. Neu khong gui (tuong thich client cu) thi mac dinh 0 / khong loc. Giao dich
    NGUYEN TU de nhieu worker cung luc khong dem trung/insert trung."""
    try:
        page_offset = int(page_offset or 0)
        page_limit = int(page_limit or 20)
    except (TypeError, ValueError):
        return {"ok": False, "error": "'page_offset'/'page_limit' phai la so nguyen"}
    if not isinstance(items, list):
        return {"ok": False, "error": "thieu 'items' (danh sach item cua trang)"}
    # Nguong loc KHI CAO: neu worker khong gui (sold_min/comm_money_min/filter_types rong) thi
    # lay tu "Điều kiện lọc chung" (bang settings, 1 noi duy nhat tren dashboard tab Worker GPM
    # Login - dung chung cho ROOT verify va cào từ khoá). comm_money_min la so TIEN theo don vi
    # cua market (1 = 1 ₱ / 1 ฿ / 1 RM / 1 ₫; cho phep thap phan nhu 0.1 vi MY co the < 1).
    try:
        if sold_min is None or comm_money_min is None or filter_types is None:
            settings = get_settings(db_path)
        if sold_min is None:
            sold_min = settings["sold_min"] or 0
        if comm_money_min is None:
            comm_money_min = settings["seller_commission_vnd_min"] or 0.0
        if filter_types is None:
            filter_types = 0
        sold_min = max(0, int(sold_min or 0))
        comm_money_min = max(0.0, float(comm_money_min or 0))
        filter_types = int(filter_types or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "'sold_min'/'comm_money_min'/'filter_types' phai la so"}

    conn = _connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("select * from keywords where id=?", (int(keyword_id),)).fetchone()
        if row is None:
            conn.commit()
            return {"ok": False, "error": "khong tim thay keyword nay"}
        if market and row["market"] != market:
            conn.commit()
            return {"ok": False, "error": f"keyword thuoc market '{row['market']}', khong phai '{market}'"}
        if row["assigned_key"] != device_key:
            conn.commit()
            return {"ok": False, "error": "keyword khong duoc giao cho device nay (bi worker khac giu?)"}

        found = inserted = dup = 0
        # Ban do cat_id (cap 1 trong cat-db cua market) -> ten: chi dung cat_id co trong ban
        # do; con lai giu NGUYEN cat_id/cat_name cua LO KEYWORD de cat_id va cat_name luon
        # di doi (tranh bug: cat_id that cua item khong co trong cat-db nhung lai gan ten cua
        # keyword -> nhieu cat_id khac nhau cung 1 ten nhu 'Home Appliances').
        import shopee_categories
        cat_index = {
            c["cat_id"]: c["cat_name"]
            for c in shopee_categories.list_categories(row["market"])
        }
        for item in items:
            metric = _keyword_passes_filters(item, sold_min, comm_money_min)
            if metric is None:
                continue
            found += 1
            # Da ton tai O BAT KY dau trong DB (root/related/pending/cached/member...) thi
            # khong lay lam root nua - dung y nghia "link tu keyword la duy nhat toan DB".
            ex = conn.execute(
                "select 1 from products where itemid=? and market=?",
                (metric["itemid"], row["market"]),
            ).fetchone()
            if ex is not None:
                dup += 1
                continue
            # Map item -> row root pending (gio nguyen phong cach map_v2_data_to_row).
            r = map_v2_data_to_row(
                item, link_type="root", groupid=metric["itemid"],
                status_link="pending", market=row["market"],
            )
            if not r.get("itemid") or not r.get("product_link"):
                continue
            batch = metric["batch"]
            # Cat cua san pham: uu tien cat_id THAT tu response NHUNG chi khi cat-db biet ten
            # cua no (bao dam cat_id+cat_name khop). Neu cat_id that khong co trong cat-db
            # (pho bien: id leaf/chi tiet, cat-db chi chua danh muc cap 1) -> dung cat_id +
            # cat_name cua LO KEYWORD (nguoi dung chon luc import) de 1 ten chi di voi 1 id.
            real_cat_id = None
            try:
                real_cat_id = int(batch.get("catid")) if batch.get("catid") not in (None, "") else None
            except (TypeError, ValueError):
                real_cat_id = None
            if real_cat_id is not None and real_cat_id in cat_index:
                r["cat_id"] = real_cat_id
                r["cat_name"] = cat_index[real_cat_id]
            else:
                r["cat_id"] = row["cat_id"]
                r["cat_name"] = row["cat_name"]
            # Xtra: filter_types=2 la loc "Comm Xtra" -> item trong ket qua do co hoa hong Xtra.
            r["xtra"] = 1 if filter_types == 2 else None
            # Ghi seller_commission = TIEN hoa hong UOC TINH (rate% * gia hien thi) de root
            # co so tien hien thi tren dashboard; Root Navigator khi verify that se ghi de
            # bang so tien that tu offer/product (neu co). Cot default/shopee_commission de
            # None - product/list chi co ty le seller_commission_rate, khong co so tien that.
            r["seller_commission"] = metric["comm_money"] if metric["comm_money"] > 0 else None
            r["default_commission"] = None
            r["shopee_commission"] = None
            cols = [c for c in COLUMNS if r.get(c) is not None]
            if not cols or "itemid" not in cols or "product_link" not in cols:
                continue
            cur = conn.execute(
                f"insert or ignore into products ({', '.join(cols)}) "
                f"values ({', '.join('?' for _ in cols)})",
                [r[c] for c in cols],
            )
            if cur.rowcount:
                inserted += 1
            else:
                dup += 1

        # Trang cuoi hay chua? DA XAC MINH API product/list dung page_offset = so ITEM da bo
        # qua (item-offset, KHONG phai so trang): probe offset=1 tra list bat dau tu item thu 2,
        # offset=5 tu item thu 6; offset cua moi lan goi tiep = offset truoc + page_limit.
        # Ket thuc khi: trang rong / it hon page_limit / da toi duoi total_count (offset +
        # so item trang nay >= total_count - phu dung ca trang cuoi DAY du).
        try:
            total_count = int(total_count) if total_count not in (None, "") else None
        except (TypeError, ValueError):
            total_count = None
        prev_total = row["total_count"]
        got_total = total_count if total_count is not None else prev_total
        got_len = len(items)
        finished = (
            got_len == 0
            or got_len < page_limit
            or (got_total is not None and page_offset + got_len >= got_total)
        )
        checkpoint = max(row["checkpoint_page"] or 0, page_offset)

        if finished:
            conn.execute(
                "update keywords set status='done', assigned_key=null, claimed_at=null, "
                "checkpoint_page=?, total_count=?, roots_found=roots_found+?, "
                "roots_inserted=roots_inserted+?, dup_skipped=dup_skipped+?, "
                "last_page_at=current_timestamp, updated_at=current_timestamp where id=?",
                (checkpoint, got_total, found, inserted, dup, int(keyword_id)),
            )
        else:
            # con trang -> gia han lease (claimed_at moi) de worker khac khong "cuop" giua chung
            conn.execute(
                "update keywords set checkpoint_page=?, total_count=?, "
                "roots_found=roots_found+?, roots_inserted=roots_inserted+?, "
                "dup_skipped=dup_skipped+?, last_page_at=current_timestamp, "
                "claimed_at=current_timestamp, updated_at=current_timestamp where id=?",
                (checkpoint, got_total, found, inserted, dup, int(keyword_id)),
            )
        conn.commit()
        fresh = conn.execute("select * from keywords where id=?", (int(keyword_id),)).fetchone()
        d = dict(fresh) if fresh else None
        return {
            "ok": True,
            "keyword_id": int(keyword_id),
            "found": found,
            "inserted": inserted,
            "dup_skipped": dup,
            "finished": finished,
            "checkpoint_page": d["checkpoint_page"] if d else checkpoint,
            "roots_inserted_total": d["roots_inserted"] if d else None,
            "roots_found_total": d["roots_found"] if d else None,
            "dup_skipped_total": d["dup_skipped"] if d else None,
            "status": d["status"] if d else None,
        }
    finally:
        conn.close()


def fail_keyword(db_path, keyword_id, reason):
    """Danh dau tu khoa loi (worker khong the cao duoc - search bi chan, loi token...) -> nha
    claim de khong bi nhan lai lien tuc; nguoi dung xem last_error tren dashboard roi bam
    reset de chay lai."""
    conn = _connect(db_path)
    try:
        conn.execute(
            "update keywords set status='error', assigned_key=null, claimed_at=null, "
            "last_error=?, updated_at=current_timestamp where id=?",
            (str(reason or "")[:500], int(keyword_id)),
        )
        conn.commit()
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

"""Doc mail Shopee (xac minh dang ky) TRUC TIEP qua Microsoft Graph API - KHONG qua dich vu
trung gian (dongvanfb tools/smail1s) - dung refresh_token + client_id da co san trong
mail_accounts (mua tu dongvanfb, xem dongvanfb_client.buy_mail()/parse_mail_line()).

Da test THAT (2026-08-24) qua endpoint token cua Microsoft voi 1 cap refresh_token/client_id
mua tu dongvanfb - xac nhan client_id LA public client (KHONG can client_secret), scope cap
duoc gom ca Mail.ReadWrite/IMAP.AccessAsUser.All/POP.AccessAsUser.All/SMTP.Send/Mail.Send -
du quyen doc (va ca gui neu can sau nay).

Ve refresh_token "xoay vong": Microsoft co cap kem 1 refresh_token MOI moi lan goi (khi scope
co 'offline_access'), NHUNG da kiem chung THAT (2026-08-24, goi lai token GOC 2 lan lien
tiep sau khi no da "bi thay" boi 2 lan xoay vong truoc do) - token CU VAN dung duoc binh
thuong, KHONG bi Microsoft thu hoi ngay nhu suy doan ban dau (day cung la ly do dongvanfb/
smail1s dung lai duoc refresh_token GOC nhieu lan ma khong can tu cap nhat). Van luu lai
refresh_token moi nhat vao DB (xem shopee_db.update_mail_account_refresh_token()) nhu 1 thoi
quen an toan (khong hai, co the giup neu token that su het han theo thoi gian - MSA refresh
token co han su dung dai han/idle-timeout rieng), nhung KHONG phai buoc bat buoc de tranh loi
'invalid_grant' nhu ghi chu truoc day - da sua lai cho dung.

Tai su dung extract_otp_code() tu dongvanfb_client.py (da test that voi nhieu ngon ngu Shopee
khac nhau, xem ghi chu o do) thay vi viet lai regex OTP rieng - Graph API tra body.content da
la HTML/text sach (KHONG phai MIME quoted-printable tho nhu dongvanfb tools tra ve), nhung
ham do van dung tot vi buoc giai ma quoted-printable la vo hai (idempotent) tren van ban
khong co ky tu can giai ma.
"""
import re

import requests

from dongvanfb_client import extract_otp_code

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
# Dung '.default' (xin lai DUNG NHUNG QUYEN da tung duoc cap khi mail nay duoc tao/ban) thay
# vi xin ten quyen cu the (vd 'Mail.Read offline_access') - da gap that te (2026-08-24): xin
# ten quyen cu the bi Microsoft tu choi voi loi AADSTS70000 "scopes requested are
# unauthorized" cho 1 so tai khoan (co le do lo mail duoc ban voi bo quyen consent khac
# nhau tuy dot), TRONG KHI '.default' luon thanh cong voi CUNG refresh_token do. Danh doi:
# '.default' KHONG kem 'offline_access' nen response se KHONG co refresh_token moi - chap
# nhan duoc vi da xac nhan token cu van dung binh thuong nhieu lan (xem ghi chu duoi ham
# fetch_shopee_code()).
#
# NGUOC LAI (2026-09-03, tai khoan that): mot so tai khoan refresh_token KHONG co scope nao
# "applicable" cho '.default' (loi AADSTS90023 "No applicable permissions were found for
# this user") nhung van refresh duoc bang scope TUONG MINH Mail.Read / Mail.ReadWrite (da
# test that: Mail.Read cho access_token hop le, doc duoc inbox, tim duoc link kich hoat).
# Vi vay thu lan luot: .default -> Mail.ReadWrite+offline_access -> Mail.Read+offline_access.
# Scope nao thanh cong dau tien thi dung. invalid_grant (token chet/thu hoi) la het duong -
# khong thu tiep.
SCOPES_ORDER = [
    "https://graph.microsoft.com/.default",
    "https://graph.microsoft.com/Mail.ReadWrite offline_access",
    "https://graph.microsoft.com/Mail.Read offline_access",
]
SCOPE = SCOPES_ORDER[0]  # giu cho tuong thich nguoc voi noi import SCOPE


class MicrosoftMailError(RuntimeError):
    pass


def refresh_access_token(refresh_token, client_id, timeout=20):
    """Doi refresh_token -> access_token qua endpoint token cua Microsoft (public client,
    KHONG can client_secret - da xac nhan qua test that). Lan luot thu cac scope trong
    SCOPES_ORDER ('.default' truoc, roi den Mail.ReadWrite/Mail.Read - xem ghi chu tren).
    Tra ve dict Microsoft goc (co 'access_token', co the co 'refresh_token' MOI neu scope
    kem 'offline_access', 'expires_in',...). Nem MicrosoftMailError voi thong diep ro rang
    neu token het han/bi thu hoi (error='invalid_grant' - mail nay KHONG con doc duoc nua)."""
    last_err = None
    for scope in SCOPES_ORDER:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": scope,
            },
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        try:
            body = resp.json()
        except ValueError:
            raise MicrosoftMailError(f"Microsoft tra ve khong phai JSON (HTTP {resp.status_code}): {resp.text[:200]}")
        if resp.status_code == 200 and body.get("access_token"):
            return body
        err = body.get("error") or "unknown_error"
        desc = (body.get("error_description") or "").split("\r\n")[0]
        if err == "invalid_grant":
            # token chet that - thu scope khac cung vo ich
            raise MicrosoftMailError(f"loi refresh token ({err}): {desc}")
        last_err = f"loi refresh token ({err}): {desc} (scope: {scope})"
    raise MicrosoftMailError(last_err or "loi refresh token khong ro")


def list_recent_messages(access_token, top=15, timeout=20):
    """Lay top N tin nhan Inbox MOI NHAT. Loc Shopee client-side (xem _find_shopee_code)
    thay vi dung $search/$filter cua Graph - tranh cu phap OData phuc tap/can header
    ConsistencyLevel rieng, top=15 la du cho muc dich doc ma vua nhan (email that su ve
    trong vai giay/phut sau khi dang ky, luon nam trong vai tin gan nhat)."""
    resp = requests.get(
        GRAPH_MESSAGES_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        params={
            "$top": top,
            "$orderby": "receivedDateTime desc",
            "$select": "from,subject,body,receivedDateTime",
        },
        timeout=timeout,
    )
    try:
        body = resp.json()
    except ValueError:
        raise MicrosoftMailError(f"Graph tra ve khong phai JSON (HTTP {resp.status_code}): {resp.text[:200]}")
    if resp.status_code != 200:
        err = (body.get("error") or {}).get("message") or "unknown_error"
        raise MicrosoftMailError(f"loi Graph API (HTTP {resp.status_code}): {err}")
    return body.get("value") or []


def _find_shopee_code(messages):
    """Giong het dongvanfb_client._find_code_in_messages() ve muc dich, nhung doc dung cau
    truc JSON cua Graph API (from.emailAddress.address, body.content) thay vi cau truc rieng
    cua dongvanfb tools."""
    for m in messages:
        from_addr = ((m.get("from") or {}).get("emailAddress") or {}).get("address") or ""
        subject = m.get("subject") or ""
        if not (re.search(r"shopee", from_addr, re.I) or re.search(r"shopee", subject, re.I)):
            continue
        content = (m.get("body") or {}).get("content") or ""
        found = extract_otp_code(content) or extract_otp_code(subject)
        if found:
            return found, subject
    return None, None


# Link rut gon dang "kich hoat dang nhap" cua Shopee (vd https://th.shp.ee/dlink/q7281kd5)
# - subdomain (th/vn/ph/...) TU THAN link da the hien market, khong can bang tra market
# rieng de tong quat hoa cho nhieu thi truong (xac nhan qua test that voi mail TH, 2026-08-24).
_LOGIN_LINK_PATTERN = re.compile(r"https?://[\w-]+\.shp\.ee/dlink/[\w-]+", re.I)

# Nguoi gui email "co lan dang nhap moi" cua Shopee - GIA DINH dung chung cau truc
# "info@security.shopee.<tld>" cho MOI thi truong (cung pattern voi affiliate@mail.shopee.<tld>
# da xac nhan o dongvanfb_client.py/shopee_collector.user.js, chi khac tld), da xac nhan
# THAT voi 1 mail TH (info@security.shopee.co.th, 2026-08-24). Neu Shopee dung dia chi khac
# o thi truong khac, can bo sung vi du that truoc khi tin tuong hoan toan cho market do.
#
# Prefix (khong phai regex) - dung truc tiep trong $filter startswith() phia Graph API (xem
# list_login_link_candidates()) thay vi tai ve top-N tin roi loc sender O PHIA PYTHON nhu truoc
# (2026-09-12: yeu cau nguoi dung sau khi phat hien bug thuc te - TH-00013/TH-00017 bao "khong
# tim thay link sau nhieu lan thu" du email THAT SU ton tai trong hop thu, chi la bi CHON VUI
# phia sau >=15 email khac (newsletter/spam Shopee gui rat day) truoc khi loc-tay kip thay -
# loc thang tai server tranh han che nay HOAN TOAN, bat ke hop thu co bao nhieu email khac xen
# giua).
_SECURITY_SENDER_PREFIX = "info@security.shopee."
_SECURITY_SENDER_RE = re.compile(r"^info@security\.shopee\.", re.I)  # con dung lam luoi an toan doi chieu client-side


# Shopee (it nhat thi truong TH, xac nhan THAT 2026-09-11) doi sang gui link kich hoat qua
# SendGrid click-tracking: MOI the <a href> trong mail bi SendGrid boc lai thanh
# "https://<id>.ct.sendgrid.net/ls/click?upn=..." - link shp.ee/dlink that KHONG con nam truc
# tiep trong noi dung mail nua (khien _LOGIN_LINK_PATTERN.search() truc tiep khong tim thay
# gi, du email THAT SU la email kich hoat dang nhap - bug nguoi dung phat hien 2026-09-11 qua
# nut "Login Shopee": mail toi dung, nhung fetch_login_link tra ve None).
_SENDGRID_CLICK_PATTERN = re.compile(r"https?://[\w.-]*\.ct\.sendgrid\.net/ls/click\?[^\s\"'<>]+", re.I)

# URL DICH cuoi cung sau khi SendGrid resolve co the la link rut gon (*.shp.ee/dlink/...) HOAC
# thang link that cua Shopee (vd shopee.co.th/dlink/verify/email-link?...) - khac voi gia dinh
# ban dau (chi co dang *.shp.ee) - xac nhan THAT qua test 2026-09-11 (mail market TH tra thang
# ve shopee.co.th/dlink/..., khong qua hop trung gian *.shp.ee nua trong lan resolve nay).
_LOGIN_DLINK_DEST_RE = re.compile(r"\.shp\.ee/dlink/|shopee\.[a-z.]+/dlink/", re.I)


def _resolve_sendgrid_login_link(content, timeout=15):
    """Voi TUNG link SendGrid click-tracking tim thay trong noi dung mail, GIAI QUYET (follow
    redirect qua GET - da xac nhan AN TOAN/idempotent bang test that 2026-09-11: goi truoc
    bang requests tran khong co cookie KHONG lam hong duoc lan approve that su sau do trong
    browser that) de xem URL DICH cuoi cung co khop _LOGIN_LINK_PATTERN (shp.ee/dlink/...)
    khong. Tra ve link SendGrid GOC (CHUA resolve) cua ung vien dau tien khop - de trinh duyet
    that (cdp_login_shopee.mjs --step activate) tu di theo chuoi redirect voi dung cookie/
    session cua chinh no, KHONG dung URL da resolve san o day (co the mat q=... token dung 1
    lan neu server gan token o buoc redirect trung gian). None neu khong ung vien nao khop
    hoac loi mang."""
    for link in _SENDGRID_CLICK_PATTERN.findall(content):
        try:
            r = requests.get(link, allow_redirects=True, timeout=timeout, stream=True)
            r.close()
        except requests.RequestException:
            continue
        if _LOGIN_DLINK_DEST_RE.search(r.url or ""):
            return link
    return None


def list_login_link_candidates(access_token, since_iso=None, top=15, timeout=20):
    """Lay cac tin nhan tu dung sender 'info@security.shopee.*' - LOC NGAY TAI SERVER Graph qua
    $filter (thay vi tai top-N tin BAT KY roi loc sender O PHIA PYTHON nhu list_recent_messages()
    cu) - xem _SECURITY_SENDER_PREFIX ve ly do (bug thuc te 2026-09-12: email that su ton tai
    trong hop thu nhung bi CHON VUI phia sau >=15 email newsletter/spam khac truoc khi loc-tay
    kip thay).

    since_iso: neu co (chuoi ISO8601 UTC, vd '2026-09-12T07:30:00Z'), CHI lay tin nhan nhan
    DUOC TU MOC NAY TRO DI (`receivedDateTime ge {since_iso}`) - yeu cau nguoi dung 2026-09-12
    "phải đảm bảo là chỉ lấy mail được gửi trong thời điểm chạy login", tranh nham phai 1 link
    CU tu lan dang nhap TRUOC DO (da xac nhan thuc te: TH-00013 co san 1 email hop le nhung tu
    3 NGAY TRUOC - link kieu nay gan nhu chac chan da HET HAN, dung se chi that bai o buoc
    activate). None = khong gioi han thoi gian (dung cho nut "⟳ Kích hoạt" thu cong hien co -
    xem mail_accounts_activate_login(), khong gan voi 1 lan chay login cu the nao).

    QUAN TRONG - da xac nhan qua test THAT tren mailbox @outlook.com ca nhan (endpoint
    /consumers/, KHONG phai tai khoan work/school): Graph API tra loi 400 'InefficientFilter'
    ('restriction or sort order qua phuc tap') neu ket hop startswith(...) VOI $orderby CUNG
    LUC, nhung ket hop startswith(...) AND receivedDateTime ge ... (khong kem $orderby) THI
    hoat dong binh thuong (200 OK). Vi vay KHONG dung $orderby o day - tu sap lai O PHIA PYTHON
    sau khi nhan ve (danh sach da loc theo sender + since_iso nen rat nho, sap tay khong dang
    ke)."""
    filt = f"startswith(from/emailAddress/address,'{_SECURITY_SENDER_PREFIX}')"
    if since_iso:
        filt += f" and receivedDateTime ge {since_iso}"
    resp = requests.get(
        GRAPH_MESSAGES_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        params={"$filter": filt, "$top": top, "$select": "from,subject,body,receivedDateTime"},
        timeout=timeout,
    )
    try:
        body = resp.json()
    except ValueError:
        raise MicrosoftMailError(f"Graph tra ve khong phai JSON (HTTP {resp.status_code}): {resp.text[:200]}")
    if resp.status_code != 200:
        err = (body.get("error") or {}).get("message") or "unknown_error"
        raise MicrosoftMailError(f"loi Graph API (HTTP {resp.status_code}): {err}")
    messages = body.get("value") or []
    messages.sort(key=lambda m: m.get("receivedDateTime") or "", reverse=True)
    return messages


def _find_login_link(messages):
    """Tim email 'co lan dang nhap moi' + trich link kich hoat. messages da duoc LOC SAN theo
    dung sender (xem list_login_link_candidates() - $filter phia Graph, khong con can doi
    chieu _SECURITY_SENDER_RE o day nua). Uu tien pattern truc tiep (mail cu/thi truong khac co
    the van gui link tho khong qua SendGrid); fallback sang giai quyet link SendGrid
    click-tracking neu khong tim thay truc tiep (xem _resolve_sendgrid_login_link)."""
    for m in messages:
        subject = m.get("subject") or ""
        content = (m.get("body") or {}).get("content") or ""
        match = _LOGIN_LINK_PATTERN.search(content) or _LOGIN_LINK_PATTERN.search(subject)
        if match:
            return match.group(0), subject
        sendgrid_link = _resolve_sendgrid_login_link(content)
        if sendgrid_link:
            return sendgrid_link, subject
    return None, None


def fetch_login_link(refresh_token, client_id, since_iso=None):
    """Tim link kich hoat dang nhap TRUC TIEP qua Microsoft Graph - cung co che voi
    fetch_shopee_code() (refresh token roi quet Inbox), chi khac dieu kien tim: $filter theo
    dung sender (+ tuy chon moc thoi gian since_iso, xem list_login_link_candidates()) thay vi
    dinh dang ma OTP. Tra ve (link, note, new_refresh_token) - xem fetch_shopee_code() ve y
    nghia new_refresh_token (nen luu, khong bat buoc)."""
    token_data = refresh_access_token(refresh_token, client_id)
    new_refresh_token = token_data.get("refresh_token") or refresh_token
    messages = list_login_link_candidates(token_data["access_token"], since_iso)
    link, subject = _find_login_link(messages)
    if link:
        note = f'Graph API trực tiếp - tìm thấy link trong "{subject}"'
    else:
        scope = f"gửi từ {since_iso} trở đi" if since_iso else "gần đây"
        note = f"Graph API trực tiếp - không tìm thấy email xác nhận đăng nhập ({scope}, {len(messages)} tin khớp người gửi)"
    return link, note, new_refresh_token


def fetch_shopee_code(refresh_token, client_id):
    """Doc ma xac minh Shopee TRUC TIEP qua Microsoft Graph (khong qua dongvanfb/smail1s).
    Tra ve (code, note, new_refresh_token). new_refresh_token LUON duoc tra ve khi refresh
    THANH CONG (Microsoft cap kem 1 ban moi moi lan goi) - noi goi nen luu lai nhu 1 thoi
    quen an toan, nhung KHONG bat buoc: da kiem chung token CU van dung duoc binh thuong sau
    khi da "bi thay" boi ban moi (xem ghi chu dau file). Nem MicrosoftMailError neu buoc
    refresh token that bai that su (token het han/thu hoi/sai client_id) - KHONG tu fallback
    dongvanfb o day, de noi goi (affiliate_scrape_server.py) tu quyet dinh co fallback hay
    khong."""
    token_data = refresh_access_token(refresh_token, client_id)
    new_refresh_token = token_data.get("refresh_token") or refresh_token
    messages = list_recent_messages(token_data["access_token"])
    code, subject = _find_shopee_code(messages)
    if code:
        note = f'Graph API trực tiếp - tìm thấy mã trong "{subject}"'
    else:
        note = f"Graph API trực tiếp - không tìm thấy email Shopee có mã trong {len(messages)} tin nhắn gần nhất"
    return code, note, new_refresh_token

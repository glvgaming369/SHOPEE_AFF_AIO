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
# offline_access BAT BUOC de Microsoft kem refresh_token MOI trong response - thieu no co
# the chi tra access_token, khien token cu "chet dan" sau vai lan dung ma khong ai cap nhat.
SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"


class MicrosoftMailError(RuntimeError):
    pass


def refresh_access_token(refresh_token, client_id, timeout=20):
    """Doi refresh_token -> access_token qua endpoint token cua Microsoft (public client,
    KHONG can client_secret - da xac nhan qua test that). Tra ve dict Microsoft goc (co
    'access_token', 'refresh_token' MOI, 'expires_in',...). Nem MicrosoftMailError voi thong
    diep ro rang neu token da het han/bi thu hoi (error='invalid_grant' - mail nay KHONG con
    doc duoc nua qua duong nay, can mua lai hoac kiem tra lai tai khoan)."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": SCOPE,
        },
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    try:
        body = resp.json()
    except ValueError:
        raise MicrosoftMailError(f"Microsoft tra ve khong phai JSON (HTTP {resp.status_code}): {resp.text[:200]}")
    if resp.status_code != 200:
        err = body.get("error") or "unknown_error"
        desc = (body.get("error_description") or "").split("\r\n")[0]
        raise MicrosoftMailError(f"loi refresh token ({err}): {desc}")
    if not body.get("access_token"):
        raise MicrosoftMailError("Microsoft khong tra ve access_token.")
    return body


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

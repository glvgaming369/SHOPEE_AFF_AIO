"""Goi thang toi dongvanfb (mua mail + doc code Shopee tu hop thu), dung cho tab "Tao tai
khoan Shopee" cua dashboard nay. Port lai TU logic da xac nhan qua test that (2026-08-10)
trong scripts/userscripts/shopee_ph_phone_checker.user.js - xem ghi chu chi tiet o cac ham
tuong ung ben do (fetchShopeeCodeForRow, decodeQuotedPrintable, extractOtpCode).

Endpoint (xem check_phone_exist/api_docs_dongvanfb.txt):
    GET  https://api.dongvanfb.net/user/buy?apikey=...&account_type=...&quality=0&quantity=...&type=full
    GET  https://api.dongvanfb.net/user/balance?apikey=...
    POST https://tools.dongvanfb.net/api/graph_code          {email, refresh_token, client_id, type}
    POST https://tools.dongvanfb.net/api/graph_messages      {email, refresh_token, client_id, list_mail}
    POST https://tools.dongvanfb.net/api/get_code_oauth2     {email, refresh_token, client_id, type}
    POST https://tools.dongvanfb.net/api/get_messages_oauth2 {email, refresh_token, client_id, list_mail}

QUAN TRONG da xac nhan qua test that voi tai khoan Shopee Thai Lan that (2026-08-10,
ConleyBoush867420@outlook.com):
    - graph_code/graph_messages co the loi rieng cho 1 tai khoan cu the ("Graph token
      invalid" - refresh_token khong doi duoc access token Graph API), TRONG KHI
      get_code_oauth2/get_messages_oauth2 (nhanh IMAP/OAuth2 khac, doc lap voi Graph) van
      doc DUOC dung hop thu do - PHAI thu ca 2 nhanh truoc khi ket luan "chua co ma".
    - Field 'code' cua ca 2 nhanh co the tra ve RONG du hop thu THAT SU co ma (gioi han/loi
      phia dongvanfb, khong phai do goi sai) - phai tu quet toan bo tin nhan va tu trich ma.
    - Noi dung 'message' la RAW MIME quoted-printable (vd "=E0=B8=A3=E0=..." cho ky tu UTF-8
      ngoai ASCII) - PHAI giai ma quoted-printable ra UTF-8 truoc roi moi bo the HTML/regex,
      neu khong cum tu bao quanh ma OTP (vd tieng Thai) se con nguyen dang byte vun, khong
      regex nao khop duoc.
"""
import quopri
import re

import requests

BASE_URL = "https://api.dongvanfb.net"
TOOLS_BASE_URL = "https://tools.dongvanfb.net/api"

# Chi liet ke cac loai TRUSTED (ho tro ca IMAP/POP3 lan Graph API) - loai NEW re hon
# thuong khong on dinh du de dang ky Shopee (xem ghi chu MAIL_ACCOUNT_TYPES trong
# shopee_ph_phone_checker.user.js).
ACCOUNT_TYPES = [
    {"id": "5", "name": "Hotmail TRUSTED [GRAPH API]"},
    {"id": "6", "name": "Outlook TRUSTED [GRAPH API]"},
    {"id": "59", "name": "Hotmail TRUSTED [IMAP/POP3/GRAPH API]"},
    {"id": "60", "name": "Outlook TRUSTED [IMAP/POP3/GRAPH API]"},
]

_OTP_PATTERNS = [
    re.compile(r"(?:OTP|verification)\s*Code\s*is:?\s*(\d{4,8})", re.I),
    re.compile(r"(?:OTP|verification)\s*code:?\s*(\d{4,8})", re.I),
    re.compile(r"\b(\d{4,8})\s*(?:is your|la ma)\b", re.I),
    # Mau khong phai tieng Anh (vd Thai Lan "OTP ...cua ban la: 283096") - "OTP" van luon
    # xuat hien nguyen van trong moi ngon ngu Shopee dung, nen bat so 4-8 chu so trong pham
    # vi gan sau tu "OTP" thay vi doi hoi dung cum tu tieng Anh.
    re.compile(r"OTP[^0-9]{0,60}(\d{4,8})\b", re.I),
]


class DongvanfbError(RuntimeError):
    pass


def _get(url, timeout=30):
    resp = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
    try:
        return resp.json()
    except ValueError:
        raise DongvanfbError(f"dongvanfb tra ve khong phai JSON: {resp.text[:200]}")


def _post(path, body, timeout=30):
    resp = requests.post(
        TOOLS_BASE_URL + path,
        json=body,
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    try:
        return resp.json()
    except ValueError:
        raise DongvanfbError(f"dongvanfb tools tra ve khong phai JSON: {resp.text[:200]}")


def get_balance(api_key):
    json_ = _get(f"{BASE_URL}/user/balance?apikey={api_key}")
    return json_.get("balance") if json_.get("status") else None


def buy_mail(api_key, account_type, quantity):
    """Tra ve nguyen JSON tu dongvanfb (data.list_data la danh sach dong
    "email|password|refresh_token|client_id", xem parse_mail_line())."""
    url = (
        f"{BASE_URL}/user/buy?apikey={api_key}&account_type={account_type}"
        f"&quality=0&quantity={quantity}&type=full"
    )
    return _get(url, timeout=60)


def parse_mail_line(line):
    """Tach "email|password|refresh_token|client_id". Chi email/client_id (UUID, luon o
    dau/cuoi) la CHAC CHAN khong chua '|' - lay client_id la phan SAU dau '|' CUOI CUNG,
    phan giua con lai (sau password) la refresh_token, phong truong hop refresh_token vo
    tinh chua ky tu '|' (giong het logic parseMailLine() trong userscript)."""
    first_pipe = line.find("|")
    if first_pipe == -1:
        return None
    email = line[:first_pipe]
    second_pipe = line.find("|", first_pipe + 1)
    if second_pipe == -1:
        return None
    password = line[first_pipe + 1:second_pipe]
    last_pipe = line.rfind("|")
    if last_pipe <= second_pipe:
        return None
    refresh_token = line[second_pipe + 1:last_pipe]
    client_id = line[last_pipe + 1:]
    if not email or not client_id:
        return None
    return {"email": email, "password": password, "refresh_token": refresh_token, "client_id": client_id}


def _strip_html(raw):
    """Giai ma quoted-printable ra UTF-8 TRUOC roi moi bo the HTML - xem ghi chu module."""
    try:
        decoded = quopri.decodestring(raw.encode("utf-8", errors="ignore")).decode("utf-8", errors="replace")
    except Exception:
        decoded = raw
    no_tags = re.sub(r"<[^>]*>", " ", decoded)
    no_nbsp = no_tags.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", no_nbsp).strip()


def extract_otp_code(text):
    if not text:
        return None
    plain = _strip_html(text)
    for pattern in _OTP_PATTERNS:
        m = pattern.search(plain)
        if m:
            return m.group(1)
    return None


def _graph_code(email, refresh_token, client_id):
    return _post("/graph_code", {"email": email, "refresh_token": refresh_token, "client_id": client_id, "type": "shopee"})


def _graph_messages(email, refresh_token, client_id):
    return _post("/graph_messages", {"email": email, "refresh_token": refresh_token, "client_id": client_id, "list_mail": "all"})


def _code_oauth2(email, refresh_token, client_id):
    return _post("/get_code_oauth2", {"email": email, "refresh_token": refresh_token, "client_id": client_id, "type": "shopee"})


def _messages_oauth2(email, refresh_token, client_id):
    return _post("/get_messages_oauth2", {"email": email, "refresh_token": refresh_token, "client_id": client_id, "list_mail": "all"})


def _find_code_in_messages(messages):
    shopee_msgs = [
        m for m in (messages or [])
        if re.search(r"shopee", m.get("from") or "", re.I) or re.search(r"shopee", m.get("subject") or "", re.I)
    ]
    for m in shopee_msgs:
        found = extract_otp_code(m.get("message")) or extract_otp_code(m.get("subject"))
        if found:
            return found, m.get("subject")
    return None, None


def fetch_shopee_code(email, refresh_token, client_id):
    """Thu lan luot Graph API roi den OAuth2/IMAP - 2 nhanh cua dongvanfb hoat dong doc lap
    (mot tai khoan co the loi nhanh nay nhung nhanh kia van doc duoc). Tra ve (code, note) -
    note mo ta CACH lay duoc ma (hoac ly do that bai) de log lai cho nguoi dung, giong het
    fetchShopeeCodeForRow() trong userscript."""
    errors = []

    try:
        result = _graph_code(email, refresh_token, client_id)
        if result.get("status") and result.get("code"):
            return result["code"], "Graph API (graph_code)"
    except Exception as e:
        errors.append(f"graph_code: {e}")

    try:
        result = _graph_messages(email, refresh_token, client_id)
        code, subject = _find_code_in_messages(result.get("messages"))
        if code:
            return code, f'Graph API - tu quet hop thu (field code rong cho "{subject}")'
    except Exception as e:
        errors.append(f"graph_messages: {e}")

    try:
        result = _code_oauth2(email, refresh_token, client_id)
        if result.get("status") and result.get("code"):
            return result["code"], "OAuth2/IMAP (get_code_oauth2) - Graph API khong doc duoc hop thu nay"
    except Exception as e:
        errors.append(f"get_code_oauth2: {e}")

    try:
        result = _messages_oauth2(email, refresh_token, client_id)
        code, subject = _find_code_in_messages(result.get("messages"))
        if code:
            return code, f'OAuth2/IMAP - tu quet hop thu (Graph API khong doc duoc, field code rong cho "{subject}")'
    except Exception as e:
        errors.append(f"get_messages_oauth2: {e}")

    note = "Ca 4 cach lay ma deu loi/rong" + (f": {' | '.join(errors)}" if errors else "")
    return None, note

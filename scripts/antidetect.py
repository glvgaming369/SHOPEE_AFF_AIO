# -*- coding: utf-8 -*-
"""Adapter chung cho cac phan mem antidetect (trinh duyet gia lap) - hien tai ho tro 2 engine:
- 'gpm': GPMLogin - Local API mac dinh http://127.0.0.1:9495 (xem "gpm Login/" trong repo)
- 'gem': GemLogin - Local API mac dinh http://127.0.0.1:1010 (docs: manual-gemlogin-vn.gitbook.io)

Muc dich: code quan ly account (tab Mail) va worker cao (root/keyword) chi goi cac ham o day
theo engine, khong quan tam engine cu the tra loi ra sao. GPM giu nguyen hanh vi cu.

Cac hanh vi duoc trich xuat (da xac minh bang GPM that + GEM that 2026-09-08):
- list_groups()          -> [{id, name}, ...]
- list_profiles()        -> [{id, name, group_id, raw_proxy, browser}, ...]
- create_profile()       -> id profile moi (GPM: body {"name", "group_id"}; GEM: body
                           {"profile_name", ...} - chi name la bat buoc, group qua "group_name")
- start_profile()        -> {"port", "debug_address", ...} - port CDP de noi /json/* (GPM tra
                           remote_debugging_port; GEM tra remote_debugging_address host:port)
- close_profile()        -> dong browser profile
- delete_profile()       -> xoa profile (GEM FREE KHONG HO TRO xoa qua API - se bao loi ro)
- engine_base_url(engine) -> base URL mac dinh (se cho override tu settings o server)

Ghi chu kiem chung:
- GEM: profile khong thuoc nhom nao co group_id = null hoac chuoi "noGroup".
- GEM FREE: /api/profiles/delete tra "The free version does not work this feature".
- GEM: khong co endpoint tao group trong docs -> nhom phai tao san trong app GemLogin.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

ENGINE_GPM = "gpm"
ENGINE_GEM = "gem"

ENGINE_DEFAULT_BASE = {
    ENGINE_GPM: "http://127.0.0.1:9495",
    ENGINE_GEM: "http://127.0.0.1:1010",
}

# Nhan hien thi khi profile KHONG thuoc nhom nao, theo engine (dung cho cot GROUP khi dong bo).
UNGROUPED_LABEL = {
    ENGINE_GPM: "Default group",
    ENGINE_GEM: "All",  # GemLogin mac dinh nhom "All" cho profile chua xep nhom
}


class AntidetectError(Exception):
    pass


def normalize_engine(engine):
    e = str(engine or "").strip().lower()
    if e not in (ENGINE_GPM, ENGINE_GEM):
        raise AntidetectError(
            f"Engine khong ho tro: '{engine}' (chi nhan 'gpm' hoac 'gem')."
        )
    return e


def engine_base_url(engine):
    return ENGINE_DEFAULT_BASE[normalize_engine(engine)]


def _request(engine, method, path, base, body=None, timeout=20):
    """Goi Local API cua engine. Tra ve dict JSON da parse (khong bao gom viec kiem success -
    caller tu kiem theo tung engine)."""
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            return {"success": False, "message": f"HTTP {e.code}: {raw[:200]}"}
    except Exception as e:
        raise AntidetectError(f"Khong goi duoc {engine} Local API ({base}): {e}") from e
    try:
        return json.loads(raw)
    except ValueError:
        return {"success": False, "message": raw[:200]}


# ---------------------------------------------------------------------------
# Doc du lieu
# ---------------------------------------------------------------------------

def list_groups(engine, base=None):
    """Danh sach nhom dang co. GPM: GET /api/v1/groups (data.data). GEM: GET /api/groups (data la mang)."""
    engine = normalize_engine(engine)
    base = base or engine_base_url(engine)
    if engine == ENGINE_GPM:
        payload = _request(engine, "GET", "/api/v1/groups", base)
        if not payload.get("success"):
            raise AntidetectError(str(payload.get("message") or "GPM loi"))
        raw = payload.get("data")
        items = raw.get("data") if isinstance(raw, dict) else (raw or [])
        return [{"id": str(g.get("id")), "name": str(g.get("name") or "")} for g in items if g]
    # GEM
    payload = _request(engine, "GET", "/api/groups", base)
    if not payload.get("success"):
        raise AntidetectError(str(payload.get("message") or "GemLogin loi"))
    items = payload.get("data") or []
    return [{"id": str(g.get("id")), "name": str(g.get("name") or "")} for g in items if g]


def list_profiles(engine, base=None, group_id=None, page=1, per_page=1000):
    """Danh sach profile. group_id khong bat buoc (filter phia sau neu GPM khong ho tro)."""
    engine = normalize_engine(engine)
    base = base or engine_base_url(engine)
    items = []
    if engine == ENGINE_GPM:
        payload = _request(engine, "GET", "/api/v1/profiles", base)
        if not payload.get("success"):
            raise AntidetectError(str(payload.get("message") or "GPM loi"))
        raw = payload.get("data")
        items = raw.get("data") if isinstance(raw, dict) else (raw or [])
    else:
        path = "/api/profiles?page=%d&per_page=%d&sort=0" % (page, per_page)
        if group_id not in (None, ""):
            path += "&group_id=" + urllib.parse.quote(str(group_id))
        payload = _request(engine, "GET", path, base)
        if not payload.get("success"):
            raise AntidetectError(str(payload.get("message") or "GemLogin loi"))
        items = payload.get("data") or []
    out = []
    for p in items or []:
        if not p:
            continue
        pid = p.get("id")
        if pid is None:
            continue
        b = p.get("browser_type") or p.get("browser") or ""
        if isinstance(b, dict):
            b = b.get("name") or ""
        out.append({
            "id": str(pid),
            "name": str(p.get("name") or ""),
            "group_id": str(p.get("group_id") or "") if p.get("group_id") not in (None, "", "noGroup") else "",
            "raw_proxy": str(p.get("raw_proxy") or ""),
            "browser": str(b),
        })
    if group_id not in (None, ""):
        out = [p for p in out if p["group_id"] == str(group_id)]
    return out


def ungrouped_label(engine):
    return UNGROUPED_LABEL[normalize_engine(engine)]


# ---------------------------------------------------------------------------
# Thao tac
# ---------------------------------------------------------------------------

def create_profile(engine, base=None, profile_name="", group_name="", raw_proxy=""):
    """Tao 1 profile moi. Tra ve (id, data_goc). GEM: chi profile_name la bat buoc (da xac
    minh); muon gắn nhom phai truyen group_name (nhom phai ton tai - GEM khong tao nhom qua
    API). GPM: group qua group_id - caller truyen group_name="" thi de mac dinh."""
    engine = normalize_engine(engine)
    base = base or engine_base_url(engine)
    if engine == ENGINE_GPM:
        # GPM goi truc tiep bang id nhom: caller phai truyen nhom da giai ra id. De don gian
        # cho ca 2 engine, create_profile nhan group_name; ham nay khong dung cho GPM truc tiep
        # (GPM goi qua helper rieng o server). Giữ code de README: chi GEM dung ham nay.
        raise AntidetectError("GPM nen tao profile qua code cu (group bang id). Dung create_profile cho GEM.")
    # GEM
    body = {"profile_name": profile_name}
    if group_name:
        body["group_name"] = group_name
    if raw_proxy:
        body["raw_proxy"] = raw_proxy
    payload = _request(engine, "POST", "/api/profiles/create", base, body=body, timeout=60)
    if not payload.get("success"):
        msg = str(payload.get("message") or payload.get("error") or "GemLogin tao profile that bai")
        raise AntidetectError(f"GemLogin tao profile that bai: {msg}")
    data = payload.get("data") or {}
    pid = data.get("id")
    if pid is None:
        raise AntidetectError(f"GemLogin bao tao thanh cong nhung khong co id: {payload}")
    return str(pid), data


def start_profile(engine, base=None, profile_id=""):
    """Mo browser cua profile (neu chua chay) va tra port CDP de dieu khien.
    GPM: GET /api/v1/profiles/start/{id} -> remote_debugging_port.
    GEM: GET /api/profiles/start/{id} -> remote_debugging_address '127.0.0.1:PORT'."""
    engine = normalize_engine(engine)
    base = base or engine_base_url(engine)
    pid = urllib.parse.quote(str(profile_id))
    if engine == ENGINE_GPM:
        payload = _request(engine, "GET", f"/api/v1/profiles/start/{pid}", base, timeout=60)
        if not payload.get("success"):
            msg = str(payload.get("message") or "GPM loi")
            if "InUse" in msg:
                raise AntidetectError(f"Profile {pid} dang mo noi khac: {msg}")
            raise AntidetectError(f"GPM start loi: {msg}")
        data = payload.get("data") or {}
        port = data.get("remote_debugging_port")
        return {"port": int(port) if port else None, "debug_address": "", "data": data}
    payload = _request(engine, "GET", f"/api/profiles/start/{pid}", base, timeout=60)
    if not payload.get("success"):
        raise AntidetectError(f"GemLogin start loi: {payload.get('message') or payload}")
    data = payload.get("data") or {}
    addr = str(data.get("remote_debugging_address") or "").strip()
    port = None
    m = re.search(r":(\d+)\s*$", addr)
    if m:
        port = int(m.group(1))
    if not port:
        raise AntidetectError(f"GemLogin start khong tra duoc dia chi CDP: {payload}")
    return {"port": port, "debug_address": addr, "data": data}


def close_profile(engine, base=None, profile_id=""):
    """Dong browser cua profile. GPM: GET /api/v1/profiles/stop/{id}; GEM: GET /api/profiles/close/{id}."""
    engine = normalize_engine(engine)
    base = base or engine_base_url(engine)
    pid = urllib.parse.quote(str(profile_id))
    if engine == ENGINE_GPM:
        payload = _request(engine, "GET", f"/api/v1/profiles/stop/{pid}", base, timeout=30)
        if not payload.get("success"):
            raise AntidetectError(f"GPM stop loi: {payload.get('message') or payload}")
        return payload
    payload = _request(engine, "GET", f"/api/profiles/close/{pid}", base, timeout=30)
    if not payload.get("success"):
        raise AntidetectError(f"GemLogin close loi: {payload.get('message') or payload}")
    return payload


def delete_profile(engine, base=None, profile_id=""):
    """Xoa profile. GEM FREE KHONG HO TRO (tra loi "The free version does not work this feature")."""
    engine = normalize_engine(engine)
    base = base or engine_base_url(engine)
    pid = urllib.parse.quote(str(profile_id))
    if engine == ENGINE_GPM:
        payload = _request(engine, "GET", f"/api/v1/profiles/delete/{pid}", base, timeout=30)
        if not payload.get("success"):
            raise AntidetectError(f"GPM delete loi: {payload.get('message') or payload}")
        return payload
    payload = _request(engine, "GET", f"/api/profiles/delete/{pid}", base, timeout=30)
    if not payload.get("success"):
        raise AntidetectError(
            f"GemLogin khong xoa duoc profile (ban free khong ho tro qua API): "
            f"{payload.get('message') or payload}"
        )
    return payload

// cdp_login_shopee.mjs - Dang nhap Shopee bang loginKey (email) + password (Shopee Password)
// cho 1 dong mail_accounts, theo dung luong da mo ta trong login_plan.txt. Dung chung cho
// engine GPM (GPMLogin) va GEM (GemLogin), cung mo hinh voi cdp_get_shopee_id.mjs.
//
// Chay theo 2 "step" (goi tu affiliate_scrape_server.py, dieu phoi giua 2 lan goi vi buoc
// xac thuc qua email can Python doc mail qua Microsoft Graph - microsoft_mail_client.py -
// Node khong co creds/logic doc mail):
//
//  --step login   : start profile -> mo tab 0 -> vao trang profile (se bi dieu huong sang
//                    trang dang nhap neu chua login) -> dong popup chon ngon ngu (neu co) ->
//                    dien loginKey/password -> bam "Log In" -> poll ket qua:
//                      ok                 -> da dang nhap (trang profile / thay "my profile")
//                      invalid_credentials-> sai tai khoan/mat khau
//                      captcha            -> bi chan captcha/traffic
//                      verify_email_link  -> Shopee bat xac thuc qua link email: DA bam nut
//                                            "Verify by Email Link", tra ve port+tab_id de
//                                            Python doc mail lay link roi goi lai --step activate
//                      timeout/error      -> qua han / loi
//
//  --step activate: nhan --port (tu buoc login) + --tab0-id (tab dang cho xac thuc) + --link
//                    (link kich hoat doc duoc tu email) -> mo TAB MOI (tab 1) dieu huong toi
//                    link -> doi text "Sign-in attempt has been approved." -> quay lai tab 0
//                    (Page.bringToFront) -> poll tiep ket qua dang nhap tren tab 0 (ok/
//                    invalid_credentials/captcha/timeout), giong doan cuoi cua --step login.
//
// IN RA DUY NHAT 1 dong JSON len stdout de server doc; log loi ra stderr. exit 0 khi da phan
// loai xong (ke ca that bai/timeout), exit !=0 khi loi fatal (khong start duoc profile/CDP).

// Khi gap captcha/traffic (status 'captcha' tu pollLoginOutcome), tu dong goi module giai
// captcha da xay dung san (re_work/captcha_re/) thay vi bo cuoc ngay - xem handleCaptchaIfNeeded()
// / trySolveCaptcha() ben duoi (yeu cau nguoi dung 2026-09-11 "Nếu gặp captcha hãy gọi luôn logic
// giải captcha chúng ta đã xây dựng luôn đi").
import { solveShopeeCaptcha } from '../re_work/captcha_re/shopee_captcha_solver.mjs';

const arg = (n, d) => { const i = process.argv.indexOf(n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const STEP = arg('--step', 'login');
const ENGINE = arg('--engine', 'gpm');
const PROFILE = arg('--profile', '');
const URL = arg('--url', '');
const LOGIN_KEY = arg('--login-key', '');
const PASSWORD = arg('--password', '');
const PORT = parseInt(arg('--port', '0'), 10) || 0;
const TAB0_ID = arg('--tab0-id', '');
const LINK = arg('--link', '');
const GPM_BASE = arg('--gpm-base', 'http://127.0.0.1:9495');
const GEM_BASE = arg('--gem-base', 'http://127.0.0.1:1010');
const TIMEOUT = parseInt(arg('--timeout', '90000'), 10) || 90000;
const POLL = parseInt(arg('--poll', '900'), 10) || 900;

// Suffix co dinh giong extension mau (popup.js: additionalCookies) - KHONG phai cookie thuc,
// chi la thong tin app dinh kem theo dinh dang yeu cau - COPY nguyen tu cdp_get_cookie.mjs de
// chuoi cookie lay duoc sau khi dang nhap thanh cong GIONG HET dinh dang nut "Get Cookie" dang
// dung (pipeline dang video doc cot 'cookie' theo dinh dang nay).
const FIXED_SUFFIX_PAIRS = [
  ['language', 'en'],
  ['SPC_RNBV', '6073008'],
  ['shopee_app_version', '29627'],
  ['shopee_rn_bundle_version', '6073008'],
  ['shopee_rn_version', '1671807778'],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ft = (u, opts = {}) => {
  const ms = opts.ms || 20000;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  return fetch(u, Object.assign({}, opts, { signal: ctl.signal })).finally(() => clearTimeout(t));
};

function out(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
function fail(status, detail, extra = {}) { out(Object.assign({ status, url: URL, detail }, extra)); process.exit(0); }

// Bao tien trinh THEO THOI GIAN THUC ve server (Popen doc stdout tung dong mot, xem
// _run_login_node() trong affiliate_scrape_server.py) - khac out()/fail() la KET QUA CUOI
// CUNG, day chi la 1 dong trang thai TAM THOI ("progress": true de server phan biet), server
// ghi lai buoc moi nhat cho frontend poll hien chi tiet tung buoc (yeu cau nguoi dung
// 2026-09-11), khong lam script dung lai/thay doi luong xu ly.
function progress(step, detail) { out({ progress: true, step, detail: detail || '' }); }

// Phu 1 lop mau xanh nhat len TOAN BO man hinh tab kem chu thong bao lon - nguoi dung thuong
// mo NHIEU cua so profile GPM/GEM cung luc, can nhan biet NGAY ket qua (thanh cong/that bai) ma
// khong phai doc log rieng (yeu cau nguoi dung 2026-09-11 "phủ lên màn hình profile màu xanh
// nhạt kèm chữ Thông báo ... sau mỗi logic"). Goi 2 lan tren 1 tab (vd "Login thành công" roi
// sau do "Get cookie thành công") - tu xoa overlay CU truoc khi ve MOI, tranh chong 2 lop.
// Best-effort (nuot loi) - khong lam hong ket qua da co du inject that bai (vd tab da dong).
function overlayJs(title, ok) {
  const accent = ok ? '#16a34a' : '#dc2626';
  const icon = ok ? '✓' : '✕';
  return `(() => {
    try {
      const old = document.getElementById('__dsh_notice_overlay');
      if (old) old.remove();
      const d = document.createElement('div');
      d.id = '__dsh_notice_overlay';
      d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(173,216,230,0.92);display:flex;align-items:center;justify-content:center;font-family:Arial,Helvetica,sans-serif;';
      const card = document.createElement('div');
      card.style.cssText = 'background:#ffffff;border-radius:16px;padding:32px 56px;box-shadow:0 8px 30px rgba(0,0,0,0.28);text-align:center;border-top:8px solid ${accent};';
      const iconEl = document.createElement('div');
      iconEl.textContent = '${icon}';
      iconEl.style.cssText = 'font-size:48px;line-height:1;color:${accent};margin-bottom:12px;';
      const textEl = document.createElement('div');
      textEl.textContent = ${JSON.stringify(title)};
      textEl.style.cssText = 'font-size:26px;font-weight:700;color:#0f172a;white-space:nowrap;';
      card.appendChild(iconEl);
      card.appendChild(textEl);
      d.appendChild(card);
      document.body.appendChild(d);
    } catch (e) {}
  })()`;
}
async function showOverlay(cdp, title, ok) {
  if (!cdp) return;
  try { await cdp.evaluate(overlayJs(title, ok)); } catch (e) {}
}

async function startProfile() {
  const base = ENGINE === 'gem' ? GEM_BASE : GPM_BASE;
  const path = ENGINE === 'gem' ? `/api/profiles/start/${PROFILE}` : `/api/v1/profiles/start/${PROFILE}`;
  const doStart = async () => {
    const r = await ft(base + path, { ms: 20000 });
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch (e) {}
    if (!r.ok || !(j && j.success)) return { ok: false, raw: t };
    return { ok: true, data: (j && j.data) || {} };
  };
  let res = await doStart();
  if (!res.ok && /InUse/i.test(res.raw)) {
    try {
      const stop = ENGINE === 'gem' ? `/api/profiles/close/${PROFILE}` : `/api/v1/profiles/stop/${PROFILE}`;
      await ft(base + stop, { ms: 20000 });
    } catch (e) {}
    await sleep(2500);
    res = await doStart();
  }
  if (!res.ok) throw new Error((ENGINE === 'gem' ? 'GemLogin' : 'GPM') + ' start fail: ' + res.raw.slice(0, 240));
  const d = res.data;
  if (ENGINE === 'gem') {
    const addr = String(d.remote_debugging_address || '');
    const m = /:(\d+)\s*$/.exec(addr);
    if (!m) throw new Error('GemLogin start khong tra CDP address');
    return { port: parseInt(m[1], 10) };
  }
  const port = d.remote_debugging_port;
  if (!port) throw new Error('GPM start khong tra remote_debugging_port');
  return { port: parseInt(port, 10) };
}

async function waitCdpUp(port, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try { const v = await (await ft(`http://127.0.0.1:${port}/json/version`, { ms: 6000 })).json(); if (v.Browser) return true; } catch (e) {}
    await sleep(500);
  }
  return false;
}

async function openTab(port, url) {
  const u = `http://127.0.0.1:${port}/json/new?${url}`;
  for (const method of ['PUT', 'GET']) {
    try {
      const r = await ft(u, { method, ms: 15000 });
      if (r.ok) {
        const tab = await r.json();
        if (tab && tab.webSocketDebuggerUrl) return tab;
      }
    } catch (e) {}
  }
  return null;
}

async function firstPageTarget(port, tries = 15) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await ft(`http://127.0.0.1:${port}/json/list`, { ms: 4000 });
      if (r.ok) {
        const list = await r.json();
        if (Array.isArray(list)) {
          const tab = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
          if (tab) return tab;
        }
      }
    } catch (e) {}
    await sleep(400);
  }
  return null;
}

async function pickTab(port, fallbackUrl) {
  const existing = await firstPageTarget(port);
  if (existing) return existing;
  return await openTab(port, fallbackUrl);
}

// Dong hang tab qua CDP HTTP endpoint - dung sau khi xong tab kich hoat (tab1 trong
// runActivate()) de no KHONG con nam trong /json/list, tranh shopee_captcha_solver.mjs's
// findShopeeTab() (chi loc theo URL chua "shopee", khong biet tab id nao dang can giai captcha)
// vo tinh gan nham vao tab1 thay vi tab0 dang thuc su can giai captcha.
async function closeTab(port, tabId) {
  for (const method of ['PUT', 'GET']) {
    try {
      const r = await ft(`http://127.0.0.1:${port}/json/close/${tabId}`, { method, ms: 5000 });
      if (r.ok) return true;
    } catch (e) {}
  }
  return false;
}

class Cdp {
  constructor(wsUrl) { this.wsUrl = wsUrl; this.ws = null; this.id = 0; this.pending = new Map(); this.eventHandlers = new Map(); }
  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    this.ws.onmessage = (ev) => {
      let msg = null; try { msg = JSON.parse(String(ev.data)); } catch (e) {}
      if (!msg) return;
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id);
        if (p) { this.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
        return;
      }
      // Message KHONG co id = 1 SU KIEN CDP (vd Page.loadEventFired) - bao cho tat ca
      // handler dang dang ky cho dung method nay (xem on()/waitForEvent()).
      if (msg.method) {
        const handlers = this.eventHandlers.get(msg.method);
        if (handlers) handlers.slice().forEach((h) => { try { h(msg.params); } catch (e) {} });
      }
    };
    await new Promise((ok, no) => { this.ws.onopen = ok; this.ws.onerror = () => no(new Error('ws error')); });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolveP, rejectP) => {
      this.pending.set(id, { resolve: resolveP, reject: rejectP });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    return (r && r.result && r.result.value !== undefined) ? r.result.value : null;
  }
  on(method, handler) {
    if (!this.eventHandlers.has(method)) this.eventHandlers.set(method, []);
    this.eventHandlers.get(method).push(handler);
  }
  off(method, handler) {
    const list = this.eventHandlers.get(method);
    if (!list) return;
    const i = list.indexOf(handler);
    if (i >= 0) list.splice(i, 1);
  }
  // Doi 1 SU KIEN CDP THAT SU xay ra (vd 'Page.loadEventFired' - trang MOI da tai xong that
  // su, KHONG phai doan bang sleep()) - tra ve true neu nhan duoc trong timeoutMs, false neu
  // qua han (van tiep tuc chay binh thuong, khong throw - coi nhu "khong chac chan" thay vi loi
  // fatal).
  waitForEvent(method, timeoutMs) {
    return new Promise((resolve) => {
      let done = false;
      const handler = () => { if (done) return; done = true; clearTimeout(timer); this.off(method, handler); resolve(true); };
      const timer = setTimeout(() => { if (done) return; done = true; this.off(method, handler); resolve(false); }, timeoutMs);
      this.on(method, handler);
    });
  }
  close() { try { this.ws && this.ws.close(); } catch (e) {} }
}

// ---- JS chay trong trang (qua cdp.evaluate) - tung IIFE doc lap, khong phu thuoc lan nhau ----

// Chi bao "ok" khi CA 3 dieu kien deu dung: (a) KHONG con o URL /login hay /buyer/login, (b)
// form dang nhap (input[name="loginKey"]) KHONG con trong DOM, (c) popup chon ngon ngu KHONG
// con hien. 3 dieu kien nay chan false-positive tung gap (URL/text co the tam thoi khop
// "profile"/"my profile" ngay sau Page.navigate, truoc khi app client-side kip dieu huong
// sang trang dang nhap that su - xem bao loi nguoi dung 2026-09-11 LAN 2: du da co debounce
// 2 lan lien tiep + cho 1200ms truoc do, van con bao 'ok' sau ~3s trong khi man hinh THAT SU
// van con popup chon ngon ngu tieng Thai "เลือกภาษา" chua bam - chung to redirect cua Shopee
// co the CHAM HON ca debounce cu, can them chan CUNG theo cau truc trang (khong chi theo thoi
// gian) + keo dai debounce o pollLoginOutcome()).
//
// (c) LUC DAU chi kiem tra "co leaf node nao text dung 'English' khong" (bat ke o dau trong
// trang) - da phat hien la SAI qua live test that 2026-09-11: trang /user/account/profile that
// su (thi truong TH) co SAN 1 nut chuyen ngon ngu "English" thuong truc trong header/nav
// (KHONG phai popup), khien SUCCESS_JS bao false MAI MAI du da dang nhap thanh cong that su
// (url dung la /user/account/profile?is_from_login=true nhung van bi bao 'timeout' vi
// successStreak khong bao gio dat). PHAI phan biet popup CHON NGON NGU (che kin man hinh,
// backdrop lon) voi nut chuyen ngon ngu thuong truc (nho, nam trong header/nav): chi coi la
// popup dang hien neu node "English" nam BEN TRONG 1 to tien co position fixed/absolute VA
// kich thuoc phu it nhat nua chieu rong + 30% chieu cao khung nhin (dac trung modal/overlay
// toan man hinh, mot header/nav thuong chi cao vai chuc px nen khong dat nguong chieu cao nay).
const SUCCESS_JS = `(() => {
  const url = location.href;
  if (/\\/(buyer\\/)?login(\\?|$)/i.test(url)) return false;
  if (document.querySelector('input[name="loginKey"]')) return false;
  const langNodes = Array.from(document.querySelectorAll('button, a, div, span, li'));
  const engNode = langNodes.find(n => n.children.length === 0 && n.textContent.trim() === 'English');
  if (engNode) {
    let el = engNode, inModal = false;
    for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed' || cs.position === 'absolute') {
        const r = el.getBoundingClientRect();
        if (r.width >= window.innerWidth * 0.5 && r.height >= window.innerHeight * 0.3) { inModal = true; break; }
      }
    }
    if (inModal) return false;
  }
  if (/\\/user\\/account\\/profile/i.test(url)) return true;
  const t = (document.body && document.body.innerText || '').toLowerCase();
  return t.includes('my profile');
})()`;

const INVALID_CRED_JS = `(() => {
  const t = document.body && document.body.innerText || '';
  return t.includes('Your account and/or password is incorrect');
})()`;

const VERIFY_EMAIL_CLICK_JS = `(() => {
  if (window.__dshVerifyClicked) return false;
  const b = document.querySelector('button[aria-label="Verify by Email Link"]');
  if (!b) return false;
  b.click();
  window.__dshVerifyClicked = true;
  return true;
})()`;

const WAITING_EMAIL_TEXT_JS = `(() => {
  const t = document.body && document.body.innerText || '';
  return t.includes('Please respond to the notification sent via Email to');
})()`;

const APPROVED_TEXT_JS = `(() => {
  const t = document.body && document.body.innerText || '';
  return t.includes('Sign-in attempt has been approved.');
})()`;

// Dien loginKey/password (React controlled input - phai dung native setter + dispatch
// 'input'/'change' de React nhan dien, khong the gan .value truc tiep) roi bam "Log In" khi
// nut khong bi disabled. Idempotent qua window.__dshSubmitted - chi bam 1 lan.
function fillSubmitJs(loginKey, password) {
  return `(() => {
    if (window.__dshSubmitted) return 'already_submitted';
    const keyEl = document.querySelector('input[name="loginKey"]');
    const passEl = document.querySelector('input[type="password"]');
    if (!keyEl || !passEl) return 'no_form';
    const setVal = (el, val) => {
      const proto = Object.getPrototypeOf(el);
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      desc.set.call(el, val);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    };
    setVal(keyEl, ${JSON.stringify(loginKey)});
    setVal(passEl, ${JSON.stringify(password)});
    const btns = Array.from(document.querySelectorAll('button'));
    const submit = btns.find(b => b.textContent.trim() === 'Log In' && !b.disabled);
    if (!submit) return 'no_button';
    submit.click();
    window.__dshSubmitted = true;
    return 'submitted';
  })()`;
}

// Popup chon ngon ngu (khong phai luon xuat hien) - bam "English" neu dang hien popup
// "Select Your Language". Click bubble len handler cua React nen chi can bam dung node la
// (khong can tim to tien nhu XPath goc trong login_plan.txt).
// KHONG duoc gate theo tieu de tieng Anh "Select Your Language" - popup nay hien THEO NGON
// NGU CUA THI TRUONG (vd market TH hien tieu de tieng Thai "เลือกภาษา", khong chua chuoi
// tieng Anh do), gate nhu vay se bo lo popup va ket dinh form dang nhap ben duoi (bug nguoi
// dung bao 2026-09-11). Thay vao do cu thu bam leaf node co dung text "English" (nut chon
// ngon ngu luon ghi "English" bang chu La Tinh du trang o thi truong nao) - vo hai neu khong
// tim thay (khong co popup nao dang hien).
// CUNG dung guard modal/overlay nhu SUCCESS_JS (xem ghi chu o do) - tranh bam NHAM nut chuyen
// ngon ngu THUONG TRUC trong header/nav (khong phai popup chon ngon ngu that su), thu duoc qua
// live test 2026-09-11 tren trang /user/account/profile thi truong TH.
const LANG_POPUP_CLICK_JS = `(() => {
  const nodes = Array.from(document.querySelectorAll('button, a, div, span, li'));
  const match = nodes.find(n => n.children.length === 0 && n.textContent.trim() === 'English');
  if (!match) return false;
  let el = match, inModal = false;
  for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' || cs.position === 'absolute') {
      const r = el.getBoundingClientRect();
      if (r.width >= window.innerWidth * 0.5 && r.height >= window.innerHeight * 0.3) { inModal = true; break; }
    }
  }
  if (!inModal) return false;
  match.click();
  return true;
})()`;

// Poll 1 tab (qua cdp da connect) cho toi khi ok/invalid_credentials/captcha hoac het TIMEOUT.
// dungFillSubmit=true: moi vong lap cung thu dien form + bam Login (dung cho --step login).
const DEBUG = !!process.env.DSH_DEBUG;
function dbg(...args) { if (DEBUG) process.stderr.write('[DSH_DEBUG] ' + args.join(' ') + '\n'); }

async function pollLoginOutcome(cdp, deadline, dungFillSubmit) {
  let href = '';
  let iter = 0;
  let successStreak = 0;
  let langClickedReported = false;
  let submittedReported = false;
  while (Date.now() < deadline) {
    iter++;
    try { href = String(await cdp.evaluate('location.href') || ''); } catch (e) { dbg('iter', iter, 'href evaluate threw:', e.message); }
    dbg('iter', iter, 'href=', href);
    if (/\/verify\/(captcha|traffic)/.test(href)) return { status: 'captcha', detail: 'Bi chan captcha/traffic: ' + href };
    try {
      if (await cdp.evaluate(LANG_POPUP_CLICK_JS)) {
        dbg('iter', iter, 'clicked English lang option');
        if (!langClickedReported) { langClickedReported = true; progress('Đã chọn ngôn ngữ English trên popup chọn ngôn ngữ.'); }
      }
    } catch (e) {}
    let success = false;
    try { success = await cdp.evaluate(SUCCESS_JS); } catch (e) { dbg('iter', iter, 'SUCCESS_JS threw:', e.message); }
    // Ngay sau Page.navigate, browser co the DA COMMIT sang URL dich (vd /user/account/profile)
    // TRUOC KHI SPA kip tu dieu huong sang trang dang nhap that su - bat buoc SUCCESS_JS phai
    // dung NHIEU LAN LIEN TIEP (cach nhau 1 nhip POLL) moi cong nhan, tranh false-positive vao
    // dung khoanh khac "cua so hep" do. Nang tu 2 len 3 lan (bao loi nguoi dung 2026-09-11 LAN
    // 2: redirect that su cua Shopee co the CHAM HON 2 nhip debounce cu ~1.8s, van bao 'ok' du
    // man hinh THAT SU van con popup chon ngon ngu - xem them guard cung trong SUCCESS_JS).
    successStreak = success ? successStreak + 1 : 0;
    dbg('iter', iter, 'success=', JSON.stringify(success), 'successStreak=', successStreak);
    if (successStreak >= 3) return { status: 'ok', detail: 'Da dang nhap Shopee thanh cong.' };
    if (success) { await sleep(POLL); continue; } // cho 1 nhip de xac nhan lai, KHONG fill/submit trong luc nay
    let invalid = false;
    try { invalid = await cdp.evaluate(INVALID_CRED_JS); } catch (e) {}
    if (invalid) return { status: 'invalid_credentials', detail: 'Sai loginKey/password.' };
    if (dungFillSubmit) {
      let clicked = false;
      try { clicked = await cdp.evaluate(VERIFY_EMAIL_CLICK_JS); } catch (e) {}
      if (clicked) {
        progress('Shopee yêu cầu xác thực qua email - đã bấm "Verify by Email Link", đang chờ xác nhận...');
        let confirmed = false;
        const innerDeadline = Date.now() + 15000;
        while (Date.now() < innerDeadline) {
          try { confirmed = await cdp.evaluate(WAITING_EMAIL_TEXT_JS); } catch (e) {}
          if (confirmed) break;
          await sleep(POLL);
        }
        return { status: 'verify_email_link', detail: confirmed
          ? 'Da bam "Verify by Email Link", Shopee dang cho xac thuc qua mail.'
          : 'Da bam "Verify by Email Link" nhung chua thay man hinh cho xac nhan.' };
      }
      let submitResult = null;
      try { submitResult = await cdp.evaluate(fillSubmitJs(LOGIN_KEY, PASSWORD)); } catch (e) {}
      if (submitResult === 'submitted' && !submittedReported) {
        submittedReported = true;
        progress('Đã điền Email/Shopee Password và bấm "Log In", đang chờ Shopee phản hồi...');
      }
    }
    await sleep(POLL);
  }
  return { status: 'timeout', detail: 'current url: ' + (href || 'unknown') };
}

// Goi module giai captcha da xay dung san (shopee_captcha_solver.mjs) khi pollLoginOutcome()
// tra ve status 'captcha'. QUAN TRONG: module do TU DOI HOI khong duoc goi CDP Runtime.enable
// tren phien lam viec cua no (vector chong-detect automation da xac nhan qua thuc nghiem - xem
// dau file shopee_captcha_solver.mjs, ghi chu #4) - nhung ket noi CDP CUA CHUNG TA (bien `cdp`
// trong runLogin()/runActivate()) DA goi Runtime.enable truoc do (can cho cdp.evaluate() de
// dien form/doc trang thai). Dong ket noi cua chung ta (va thu Runtime.disable truoc khi dong,
// best-effort) roi de solver TU MO ket noi CDP RIENG cua no toi CUNG tab (qua port, khong dung
// lai object `cdp` nay) - day la cach giam thieu xung dot ma KHONG can sua module dung chung
// (module nay con duoc cac workflow khac tai su dung). Gioi han da biet: trang thai domain
// Runtime tren 1 target CDP co the khong tay het ngay khi 1 WS session dong/disable - chua co
// cach khac phuc trong pham vi hien tai.
async function trySolveCaptcha(port, cdp) {
  progress('Phát hiện captcha/traffic - đang gọi module tự động giải captcha...');
  try { await cdp.send('Runtime.disable'); } catch (e) {}
  try { cdp.close(); } catch (e) {}
  let result = null;
  try {
    result = await solveShopeeCaptcha({
      port,
      url: '', // gan vao tab HIEN CO dang hien captcha, khong navigate di noi khac
      maxAttempts: 6,
      onLog: (msg) => progress('[captcha] ' + msg),
    });
  } catch (e) {
    process.stderr.write('CAPTCHA_SOLVE_ERR: ' + (e && e.message) + '\n');
    return { solved: false };
  }
  progress(result.pass
    ? 'Đã giải captcha thành công, đang kiểm tra lại trạng thái đăng nhập...'
    : 'Giải captcha thất bại sau nhiều lần thử.');
  return { solved: result.pass };
}

// Bao ngoai pollLoginOutcome(): neu ket qua la 'captcha', thu giai roi POLL LAI (toi da
// MAX_CAPTCHA_ROUNDS lan, tranh vong lap vo han neu Shopee cu lien tuc bat captcha moi). Tra ve
// ket qua CUOI CUNG + ket noi CDP DANG CON SONG (co the la 1 object `cdp` MOI neu da phai giai
// captcha it nhat 1 lan) de goi noi tiep dung cho extractCookieString().
const MAX_CAPTCHA_ROUNDS = 2;
async function handleCaptchaIfNeeded(cdp, port, tabId, deadline, dungFillSubmit) {
  let res = await pollLoginOutcome(cdp, deadline, dungFillSubmit);
  let rounds = 0;
  while (res.status === 'captcha' && rounds < MAX_CAPTCHA_ROUNDS) {
    rounds++;
    const { solved } = await trySolveCaptcha(port, cdp);
    if (!solved) return { res, cdp: null };
    cdp = new Cdp(`ws://127.0.0.1:${port}/devtools/page/${tabId}`);
    await cdp.connect();
    await cdp.send('Page.enable').catch(() => {});
    await cdp.send('Runtime.enable').catch(() => {});
    const nextDeadline = Date.now() + Math.min(TIMEOUT, 60000);
    res = await pollLoginOutcome(cdp, nextDeadline, dungFillSubmit);
  }
  return { res, cdp };
}

// Sau khi DA XAC NHAN dang nhap thanh cong (status 'ok'), TIEN LAY LUON COOKIE ngay trong
// CUNG phien CDP nay (tab dang mo san, khong can dong roi mo browser rieng qua nut "Get
// Cookie" nua) - xem yeu cau nguoi dung 2026-09-11 "lấy luôn cookie nếu đã login thành công,
// tài khoản nào fail thì bỏ qua" (fail = khong goi ham nay, xem runLogin()/runActivate() chi
// goi khi status==='ok'). Dung LAI dung ky thuat Network.getCookies + suffix co dinh nhu
// cdp_get_cookie.mjs de chuoi cookie GIONG HET dinh dang nut "Get Cookie" hien co. Tra ve
// chuoi cookie, hoac null neu khong lay duoc SPC_U hop le (hiem khi xay ra vi da xac nhan
// dang nhap thanh cong ngay truoc do, nhung van phong ho).
async function extractCookieString(cdp, currentUrl) {
  await cdp.send('Network.enable').catch(() => {});
  let cookies = [];
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    try {
      const r = await cdp.send('Network.getCookies', { urls: [currentUrl] });
      cookies = (r && r.cookies) || [];
    } catch (e) { cookies = []; }
    const spcU = cookies.find((c) => c.name === 'SPC_U');
    if (spcU && spcU.value && spcU.value.trim() !== '' && spcU.value !== '-') break;
    await sleep(700);
  }
  const spcU = cookies.find((c) => c.name === 'SPC_U');
  if (!(spcU && spcU.value && spcU.value.trim() !== '' && spcU.value !== '-')) return null;
  const existingNames = new Set(cookies.map((c) => c.name));
  const suffix = FIXED_SUFFIX_PAIRS.filter(([n]) => !existingNames.has(n))
    .map(([n, v]) => `${n}=${v}`).join('; ');
  return cookies.map((c) => `${c.name}=${c.value}`).join('; ') + (suffix ? '; ' + suffix : '');
}

// Truoc khi lay cookie, DIEU HUONG VE TRANG CHU Shopee (khong lay ngay tren trang
// /user/account/profile dang dung) - yeu cau nguoi dung 2026-09-11 sau khi thay 1 lan
// "cookie_saved:false" du da dang nhap OK that su (TH-00008): trang chu la noi nut "Get Cookie"
// hien co van dang dung on dinh, va viec dieu huong + doi Page.loadEventFired that su (thay vi
// doc cookie ngay tren trang profile) cho SPC_U/cac cookie phien du thoi gian on dinh hoa hon.
// Lay origin qua location.origin CUA CHINH TAB (khong dung bien module-level `URL` - o
// --step activate bien do la CHUOI RONG vi Python khong truyen --url cho buoc nay, xem
// mail_accounts_login_shopee() trong affiliate_scrape_server.py) nen hoat dong dung cho CA 2
// step. LUU Y: bien module-level `URL` trong file nay LA 1 CHUOI (tu --url arg), che khuat
// global class `URL` cua JS - KHONG dung duoc `new URL(...)` trong file nay.
async function navigateHomeAndExtractCookie(cdp) {
  let origin = '';
  try { origin = String(await cdp.evaluate('location.origin') || ''); } catch (e) {}
  const homeUrl = origin ? origin + '/' : URL;
  progress('Đăng nhập thành công, đang chuyển về trang chủ Shopee để lấy cookie...');
  const loadEventPromise = cdp.waitForEvent('Page.loadEventFired', 15000);
  try { await cdp.send('Page.navigate', { url: homeUrl }); } catch (e) {}
  const loaded = await loadEventPromise;
  await sleep(loaded ? 800 : 2000);
  const cookie = await extractCookieString(cdp, homeUrl);
  await showOverlay(cdp, cookie ? 'Get cookie thành công' : 'Get cookie thất bại', !!cookie);
  return cookie;
}

async function runLogin() {
  if (!PROFILE || !URL || !LOGIN_KEY || !PASSWORD) { process.stderr.write('thieu --profile/--url/--login-key/--password\n'); process.exit(2); }
  progress('Đang mở trình duyệt profile (GPM/GEM)...');
  let st;
  try { st = await startProfile(); }
  catch (e) { process.stderr.write('START_ERR: ' + e.message + '\n'); process.exit(3); }
  if (!(await waitCdpUp(st.port))) { process.stderr.write('CDP khong len port ' + st.port + '\n'); process.exit(4); }
  const tab = await pickTab(st.port, 'about:blank');
  if (!tab) { process.stderr.write('Mo tab that bai\n'); process.exit(5); }
  dbg('port=', st.port, 'picked tab id=', tab.id, 'url(before nav)=', tab.url);
  const cdp = new Cdp(tab.webSocketDebuggerUrl);
  try { await cdp.connect(); } catch (e) { process.stderr.write('WS loi: ' + e.message + '\n'); process.exit(6); }
  await cdp.send('Page.enable').catch(() => {});
  await cdp.send('Runtime.enable').catch(() => {});
  progress('Đang tải trang đăng nhập...');
  // Dang ky doi SU KIEN TAI TRANG THAT SU (Page.loadEventFired) TRUOC KHI goi Page.navigate
  // (tranh race - neu dang ky sau, load co the da fire truoc khi kip lang nghe). Day la fix
  // cho bug nguoi dung bao 2026-09-11 LAN 3: da bao 'ok' chi sau ~7s, KHONG HE dien form (log
  // khong co dong "Đã điền Email/Shopee Password...") - nguyen nhan THAT SU: tab dang dung lai
  // (pickTab() uu tien tab co san) co the con giu NOI DUNG CU tu 1 lan chay TRUOC DO (vd dang
  // dung o /user/account/profile that su tu phien truoc), va Page.navigate KHONG dam bao
  // location.href/DOM cap nhat NGAY khi no resolve - resolve chi co nghia "da yeu cau dieu
  // huong", KHONG phai "da tai xong tai lieu moi". Doc SUCCESS_JS qua som se an nham vao NOI
  // DUNG CU do. Cho load event that su (hoac toi da 20s) truoc khi tin bat ky gia tri nao.
  const loadEventPromise = cdp.waitForEvent('Page.loadEventFired', Math.min(TIMEOUT, 20000));
  try { await cdp.send('Page.navigate', { url: URL }); } catch (e) { process.stderr.write('NAV loi: ' + (e && e.message) + '\n'); process.exit(7); }
  const loaded = await loadEventPromise;
  dbg('Page.loadEventFired nhan duoc trong han:', loaded);
  // Du da co load event that su, van giu 1 khoang settle nho SAU DO (SPA con can hydrate/tu
  // dieu huong client-side rieng, KHONG tinh trong load event cua trinh duyet) - ngan hon neu
  // DA xac nhan load event that (1200ms), dai hon neu KHONG nhan duoc load event trong han
  // (3500ms, phong ho truong hop hiem load event khong ban duoc vi ly do nao do).
  await sleep(loaded ? 1200 : 3500);

  const { res, cdp: liveCdp } = await handleCaptchaIfNeeded(cdp, st.port, tab.id, Date.now() + TIMEOUT, true);
  if (res.status === 'verify_email_link') {
    out({ status: res.status, detail: res.detail, url: URL, port: st.port, tab_id: tab.id });
  } else if (res.status === 'ok') {
    await showOverlay(liveCdp, 'Login thành công', true);
    await sleep(1500); // giu overlay hien du lau de nguoi dung kip nhin thay truoc khi dieu huong di
    const cookie = await navigateHomeAndExtractCookie(liveCdp);
    out({ status: res.status, detail: res.detail, url: URL, cookie });
  } else {
    await showOverlay(liveCdp, 'Login thất bại', false);
    out({ status: res.status, detail: res.detail, url: URL });
  }
  process.exit(0);
}

async function runActivate() {
  if (!PORT || !TAB0_ID || !LINK) { process.stderr.write('thieu --port/--tab0-id/--link\n'); process.exit(2); }
  if (!(await waitCdpUp(PORT, 20))) { process.stderr.write('CDP khong len port ' + PORT + '\n'); process.exit(4); }

  progress('Đang mở link xác thực từ email...');
  const tab1 = await openTab(PORT, LINK);
  if (!tab1) { process.stderr.write('Mo tab kich hoat that bai\n'); process.exit(5); }
  const cdp1 = new Cdp(tab1.webSocketDebuggerUrl);
  let approved = false;
  try {
    await cdp1.connect();
    await cdp1.send('Page.enable').catch(() => {});
    await cdp1.send('Runtime.enable').catch(() => {});
    progress('Đang chờ Shopee xác nhận email đã được duyệt...');
    const innerDeadline = Date.now() + 30000;
    while (Date.now() < innerDeadline) {
      try { approved = await cdp1.evaluate(APPROVED_TEXT_JS); } catch (e) {}
      if (approved) break;
      await sleep(POLL);
    }
  } catch (e) {
    process.stderr.write('Tab kich hoat loi: ' + (e && e.message) + '\n');
  } finally {
    cdp1.close();
    // Dong han tab kich hoat (khong chi dong ket noi CDP) - tranh solveShopeeCaptcha's
    // findShopeeTab() sau nay (neu tab0 gap captcha) tim/gan nham vao tab nay thay vi tab0.
    await closeTab(PORT, tab1.id).catch(() => {});
  }
  progress(approved ? 'Email đã được duyệt, đang quay lại tab đăng nhập để kiểm tra kết quả...'
                     : 'Chưa thấy xác nhận duyệt email, vẫn quay lại tab đăng nhập để kiểm tra...');

  // Quay lai tab 0 (dang cho ket qua dang nhap) - ws url tab suy ra truc tiep tu tab id (on
  // dinh qua cac lan navigate, khong doi khi chi doi state tren cung 1 tab).
  const cdp0 = new Cdp(`ws://127.0.0.1:${PORT}/devtools/page/${TAB0_ID}`);
  try { await cdp0.connect(); }
  catch (e) { process.stderr.write('Ket noi lai tab 0 loi: ' + e.message + '\n'); fail('error', 'Khong ket noi lai duoc tab dang nhap (tab 0): ' + e.message, { approved }); return; }
  await cdp0.send('Page.enable').catch(() => {});
  await cdp0.send('Runtime.enable').catch(() => {});
  try { await cdp0.send('Page.bringToFront'); } catch (e) {}

  const { res, cdp: liveCdp } = await handleCaptchaIfNeeded(cdp0, PORT, TAB0_ID, Date.now() + TIMEOUT, false);
  if (res.status === 'ok') {
    await showOverlay(liveCdp, 'Login thành công', true);
    await sleep(1500); // giu overlay hien du lau de nguoi dung kip nhin thay truoc khi dieu huong di
    const cookie = await navigateHomeAndExtractCookie(liveCdp);
    out({ status: res.status, detail: res.detail, url: URL, approved, cookie });
  } else {
    await showOverlay(liveCdp, 'Login thất bại', false);
    out({ status: res.status, detail: res.detail, url: URL, approved });
  }
  process.exit(0);
}

async function main() {
  if (STEP === 'activate') return runActivate();
  return runLogin();
}

main().then(() => process.exit(0)).catch((e) => { process.stderr.write('FATAL: ' + (e && e.message) + '\n'); process.exit(1); });

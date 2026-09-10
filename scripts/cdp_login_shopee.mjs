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

class Cdp {
  constructor(wsUrl) { this.wsUrl = wsUrl; this.ws = null; this.id = 0; this.pending = new Map(); }
  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    this.ws.onmessage = (ev) => {
      let msg = null; try { msg = JSON.parse(String(ev.data)); } catch (e) {}
      if (!msg) return;
      const p = this.pending.get(msg.id);
      if (p) { this.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
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
  close() { try { this.ws && this.ws.close(); } catch (e) {} }
}

// ---- JS chay trong trang (qua cdp.evaluate) - tung IIFE doc lap, khong phu thuoc lan nhau ----

// Chi bao "ok" khi: (a) KHONG con o URL /login hay /buyer/login, VA (b) form dang nhap
// (input[name="loginKey"]) KHONG con trong DOM - 2 dieu kien nay chan false-positive tung
// gap (URL/text co the tam thoi khop "profile"/"my profile" ngay sau Page.navigate, truoc khi
// app client-side kip dieu huong sang trang dang nhap that su - xem bao loi nguoi dung
// 2026-09-11: da bao 'ok' trong 5s trong khi man hinh van con popup chon ngon ngu + form login).
const SUCCESS_JS = `(() => {
  const url = location.href;
  if (/\\/(buyer\\/)?login(\\?|$)/i.test(url)) return false;
  if (document.querySelector('input[name="loginKey"]')) return false;
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
const LANG_POPUP_CLICK_JS = `(() => {
  const nodes = Array.from(document.querySelectorAll('button, a, div, span, li'));
  const match = nodes.find(n => n.children.length === 0 && n.textContent.trim() === 'English');
  if (!match) return false;
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
    // TRUOC KHI SPA kip tu dieu huong sang trang dang nhap that su (bug nguoi dung bao
    // 2026-09-11: "ok" sau 5s trong khi man hinh van con trang login) - bat buoc SUCCESS_JS
    // phai dung 2 LAN LIEN TIEP (cach nhau 1 nhip POLL) moi cong nhan, tranh false-positive
    // vao dung khoanh khac "cua so hep" do.
    successStreak = success ? successStreak + 1 : 0;
    dbg('iter', iter, 'success=', JSON.stringify(success), 'successStreak=', successStreak);
    if (successStreak >= 2) return { status: 'ok', detail: 'Da dang nhap Shopee thanh cong.' };
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
  try { await cdp.send('Page.navigate', { url: URL }); } catch (e) { process.stderr.write('NAV loi: ' + (e && e.message) + '\n'); process.exit(7); }
  // Doi 1 nhip truoc khi check gi ca - ngay sau Page.navigate resolve app SPA co the chua kip
  // client-side redirect/hydrate xong (URL/DOM tam thoi con o trang thai cu), tranh false-
  // positive nhu bug nguoi dung bao 2026-09-11.
  await sleep(1200);

  const res = await pollLoginOutcome(cdp, Date.now() + TIMEOUT, true);
  if (res.status === 'verify_email_link') {
    out({ status: res.status, detail: res.detail, url: URL, port: st.port, tab_id: tab.id });
  } else {
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

  const res = await pollLoginOutcome(cdp0, Date.now() + TIMEOUT, false);
  out({ status: res.status, detail: res.detail, url: URL, approved });
  process.exit(0);
}

async function main() {
  if (STEP === 'activate') return runActivate();
  return runLogin();
}

main().then(() => process.exit(0)).catch((e) => { process.stderr.write('FATAL: ' + (e && e.message) + '\n'); process.exit(1); });

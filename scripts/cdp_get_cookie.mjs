// cdp_get_cookie.mjs - Lay chuoi cookie dang nhap Shopee (dinh dang "name=value; name=value...")
// cua profile dang dang nhap, dung chung cho engine GPM (GPMLogin) va GEM (GemLogin).
//  1) start profile (qua Local API) -> CDP port that
//  2) mo trang chu Shopee theo market ({url}) - cookie da ton tai trong cookie jar cua
//     profile tu truoc (khong can dieu huong sang trang cu the nao), chi can 1 request cung
//     domain de xac nhan trang thai dang nhap / bi chan captcha.
//  3) dung CDP Network.getCookies({urls}) de lay TOAN BO cookie (KE CA HttpOnly/Secure - cai
//     ma document.cookie trong trang KHONG doc duoc, vd SPC_ST/SPC_STK/SPC_T_ID/AC_CERT_D...)
//     - tuong duong chrome.cookies.getAll() cua extension mau, khac voi content.js (chi doc
//     document.cookie nen thieu dung cac cookie session quan trong nhat).
//  4) kiem tra SPC_U (cookie danh dau da dang nhap) + URL hien tai de phan loai:
//          URL bi dan sang login (buyer/seller/accounts) -> chua dang nhap
//          URL chua /verify/...                    -> captcha/traffic
//          SPC_U rong/'-'                           -> chua dang nhap (dan du URL khong lo)
//          con lai                                  -> ok, ghep chuoi cookie + suffix co dinh
//     (suffix co dinh giong extension mau: language/app version - KHONG phai cookie thuc cua
//     trinh duyet, chi la thong tin app co dinh duoc noi vao cuoi chuoi theo yeu cau dinh dang).
//  5) IN RA DUY NHAT 1 dong JSON len stdout de server doc; log loi ra stderr.
//
// Args: --engine gpm|gem --profile <id> --url <home-url>
//       [--gpm-base http://127.0.0.1:9495] [--gem-base http://127.0.0.1:1010]
//       [--timeout 60000]
// exit 0 khi da phan loai xong (ke ca login/captcha), exit !=0 khi loi fatal (khong start
// duoc profile / khong CDP). Voi login/captcha de cua so do lai cho nguoi dung xu ly.

const arg = (n, d) => { const i = process.argv.indexOf(n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const ENGINE = arg('--engine', 'gpm');
const PROFILE = arg('--profile', '');
const URL = arg('--url', '');
const GPM_BASE = arg('--gpm-base', 'http://127.0.0.1:9495');
const GEM_BASE = arg('--gem-base', 'http://127.0.0.1:1010');
const TIMEOUT = parseInt(arg('--timeout', '60000'), 10) || 60000;

// Suffix co dinh giong extension mau (popup.js: additionalCookies) - KHONG phai cookie thuc,
// chi la thong tin app dinh kem theo dinh dang yeu cau.
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
function fail(status, detail) { out({ status, cookie: null, url: URL, detail }); process.exit(0); }

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

// Tab DAU TIEN (tab 0) da co san khi profile vua start (GPM/GEM thuong tu mo 1 tab trong) -
// dung lai tab nay (roi Page.navigate sau) thay vi tao tab moi qua openTab(), tranh tinh
// trang tab 0 bo trong con Shopee lai bi mo o tab 1 (yeu cau nguoi dung 2026-09-09). CDP port
// "len" (waitCdpUp qua) KHONG co nghia tab dau tien da xuat hien trong /json/list ngay - co
// khoang tre nho luc browser vua khoi dong, nen thu lai vai lan truoc khi bo cuoc.
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

// Uu tien tab 0 co san (can Page.navigate rieng sau khi connect, vi khong tu dieu huong nhu
// openTab); chi tao tab moi (da tro thang toi URL) neu khong tim thay tab nao (edge case).
async function pickTab(port, url) {
  const existing = await firstPageTarget(port);
  if (existing) return { tab: existing, needsNavigate: true };
  const created = await openTab(port, url);
  return created ? { tab: created, needsNavigate: false } : null;
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

async function main() {
  if (!PROFILE || !URL) { process.stderr.write('thieu --profile/--url\n'); process.exit(2); }
  let st;
  try { st = await startProfile(); }
  catch (e) { process.stderr.write('START_ERR: ' + e.message + '\n'); process.exit(3); }
  if (!(await waitCdpUp(st.port))) { process.stderr.write('CDP khong len port ' + st.port + '\n'); process.exit(4); }
  const picked = await pickTab(st.port, URL);
  if (!picked) { process.stderr.write('Mo tab that bai\n'); process.exit(5); }
  const cdp = new Cdp(picked.tab.webSocketDebuggerUrl);
  try { await cdp.connect(); } catch (e) { process.stderr.write('WS loi: ' + e.message + '\n'); process.exit(6); }
  await cdp.send('Page.enable').catch(() => {});
  await cdp.send('Network.enable').catch(() => {});
  await cdp.send('Runtime.enable').catch(() => {});
  if (picked.needsNavigate) {
    try { await cdp.send('Page.navigate', { url: URL }); } catch (e) { process.stderr.write('NAV loi: ' + (e && e.message) + '\n'); process.exit(7); }
  }

  const deadline = Date.now() + TIMEOUT;
  let current = URL;
  let lastErr = '';
  // Cho trang dieu huong on dinh (neu chua login, Shopee se redirect sang trang login/verify).
  while (Date.now() < deadline) {
    try { current = String(await cdp.evaluate('location.href') || current); } catch (e) { lastErr = e.message; }
    if (/\/login(\?|$)/.test(current) || /accounts\.shopee/.test(current) || /\/verify\/(captcha|traffic)/.test(current)) break;
    let ready = false;
    try { ready = await cdp.evaluate("document.readyState === 'complete'"); } catch (e) {}
    if (ready) break;
    await sleep(500);
  }

  if (/\/login(\?|$)/.test(current) || /accounts\.shopee/.test(current)) { fail('no_login', 'Chua dang nhap: ' + current); return; }
  if (/\/verify\/(captcha|traffic)/.test(current)) { fail('captcha', 'Bi chan captcha/traffic: ' + current); return; }

  // Network.getCookies({urls}) - GIONG chrome.cookies.getAll({url}) cua extension mau: chi
  // tra ve dung nhung cookie se duoc gui kem request toi URL nay (khop domain/path chuan cua
  // trinh duyet), KHONG lay tat ca cookie trong browser nhu Network.getAllCookies (cach do da
  // bi loi thuc te: gom luon cookie cua cac subdomain Shopee khac nhau - vd c0./deo./banner.
  // shopee.<tld> - moi subdomain giu ban rieng cua cung ten cookie -> ra chuoi gap 5-6 lan,
  // trung ten hang loat, khong khop dinh dang file mau). Van thu lai vai lan (khong doc 1 lan
  // la xong) vi ngay sau khi profile vua start co the cookie store chua kip san sang.
  let cookies = [];
  let lastCookieErr = '';
  const cookieDeadline = Date.now() + Math.min(15000, Math.max(4000, deadline - Date.now()));
  while (Date.now() < cookieDeadline) {
    try {
      const r = await cdp.send('Network.getCookies', { urls: [current] });
      cookies = (r && r.cookies) || [];
    } catch (e) { lastCookieErr = e.message; cookies = []; }
    const spcU = cookies.find((c) => c.name === 'SPC_U');
    if (spcU && spcU.value && spcU.value.trim() !== '' && spcU.value !== '-') break;
    await sleep(700);
  }

  const spcU = cookies.find((c) => c.name === 'SPC_U');
  const loggedIn = !!(spcU && spcU.value && spcU.value.trim() !== '' && spcU.value !== '-');
  if (!loggedIn) {
    const names = cookies.map((c) => c.name).join(',') || '(khong co cookie nao khop URL)';
    fail('no_login', `Khong tim thay SPC_U hop le tai ${current} - cookie doc duoc: ${names}` + (lastCookieErr ? ` | loi: ${lastCookieErr}` : ''));
    return;
  }

  // Chi ghep them cac key trong suffix co dinh CHUA CO san trong cookie thuc (vd 'language'
  // co the da la cookie thuc cua trinh duyet) - tranh trung ten nhu file mau khong co.
  const existingNames = new Set(cookies.map((c) => c.name));
  const suffix = FIXED_SUFFIX_PAIRS.filter(([n]) => !existingNames.has(n))
    .map(([n, v]) => `${n}=${v}`).join('; ');
  const cookieString = cookies.map((c) => `${c.name}=${c.value}`).join('; ') + (suffix ? '; ' + suffix : '');
  out({ status: 'ok', cookie: cookieString, url: current, detail: 'Lay tu CDP Network.getCookies (' + cookies.length + ' cookie).' });
  cdp.close();
  process.exit(0);
}

main().then(() => process.exit(0)).catch((e) => { process.stderr.write('FATAL: ' + (e && e.message) + '\n'); process.exit(1); });

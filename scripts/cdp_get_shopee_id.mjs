// cdp_get_shopee_id.mjs - Lấy "Shopee ID" (ten user) cua profile dang dang nhap Shopee.
// Dung chung cho engine GPM (GPMLogin) va GEM (GemLogin):
//  1) start profile (qua Local API) -> CDP port that
//  2) mo trang buyer {url} (https://shopee.<tld>/user/account/profile) - khong vao seller
//  3) cai hook fetch/XHR tu document-start: BAT RESPONSE cua request den seller.shopee.*
//     (webchat .../mini/login) -> lay o.user.name lam Shopee ID (thay XPath da loi); hook
//     chay duoc ca trong iframe (seller) nho postMessage day nguoc len tab chinh.
//  4) poll: URL bi dan sang login (buyer/seller/accounts) -> chua dang nhap
//          URL chua /verify/...                    -> captcha/traffic
//          da bat duoc user.name                   -> ok
//  5) IN RA DUY NHAT 1 dong JSON len stdout de server doc; log loi ra stderr.
//
// Args: --engine gpm|gem --profile <id> --url <profile-url>
//       [--gpm-base http://127.0.0.1:9495] [--gem-base http://127.0.0.1:1010]
//       [--timeout 60000] [--poll 900]
// exit 0 khi da phan loai xong (ke ca login/captcha/timeout), exit !=0 khi loi fatal (khong
// start duoc profile / khong CDP). Voi login/captcha de cua so do lai cho nguoi dung xu ly.

const arg = (n, d) => { const i = process.argv.indexOf(n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const ENGINE = arg('--engine', 'gpm');
const PROFILE = arg('--profile', '');
const URL = arg('--url', '');
const GPM_BASE = arg('--gpm-base', 'http://127.0.0.1:9495');
const GEM_BASE = arg('--gem-base', 'http://127.0.0.1:1010');
const TIMEOUT = parseInt(arg('--timeout', '60000'), 10) || 60000;
const POLL = parseInt(arg('--poll', '900'), 10) || 900;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// fetch noi bo (GPM/GEM/CDP) phai co timeout - neu khong the treo vi nhan trang.
const ft = (u, opts = {}) => {
  const ms = opts.ms || 20000;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  return fetch(u, Object.assign({}, opts, { signal: ctl.signal })).finally(() => clearTimeout(t));
};

function out(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
function fail(status, detail) { out({ status, shopee_id: null, url: URL, detail }); process.exit(0); }

async function startProfile() {
  // GPM: khong yeu cau port -> GPM tra remote_debugging_port that (cung kieu _gpm_ensure_browser)
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
// dung lai tab nay thay vi tao tab moi qua openTab(), tranh tinh trang tab 0 bo trong con
// Shopee lai bi mo o tab 1 (yeu cau nguoi dung 2026-09-09).
async function firstPageTarget(port) {
  try {
    const r = await ft(`http://127.0.0.1:${port}/json/list`, { ms: 8000 });
    if (r.ok) {
      const list = await r.json();
      if (Array.isArray(list)) {
        const tab = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
        if (tab) return tab;
      }
    }
  } catch (e) {}
  return null;
}

// Uu tien tab 0 co san; chi tao tab moi (openTab) neu khong tim thay tab nao (edge case).
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

async function main() {
  if (!PROFILE || !URL) { process.stderr.write('thieu --profile/--url\n'); process.exit(2); }
  let st;
  try { st = await startProfile(); }
  catch (e) { process.stderr.write('START_ERR: ' + e.message + '\n'); process.exit(3); }
  if (!(await waitCdpUp(st.port))) { process.stderr.write('CDP khong len port ' + st.port + '\n'); process.exit(4); }
  // Dung tab 0 co san (khong tao tab moi) -> cai hook fetch/XHR (document-start) -> moi dieu
  // huong sang URL. Page.addScriptToEvaluateOnNewDocument ap dung cho LAN navigate KE TIEP
  // nen khong can tab dang o about:blank truoc do.
  const tab = await pickTab(st.port, 'about:blank');
  if (!tab) { process.stderr.write('Mo tab that bai\n'); process.exit(5); }
  const cdp = new Cdp(tab.webSocketDebuggerUrl);
  try { await cdp.connect(); } catch (e) { process.stderr.write('WS loi: ' + e.message + '\n'); process.exit(6); }
  await cdp.send('Page.enable').catch(() => {});
  await cdp.send('Runtime.enable').catch(() => {});
  const HOOK = `(() => {
    if (window.__dshHooked) return; window.__dshHooked = 1;
    const setAndPropagate = (n) => {
      if (!n) return;
      if (!window.__dshName) window.__dshName = String(n);
      try { if (window.parent && window.parent !== window) window.parent.postMessage({ __dsh: String(window.__dshName) }, '*'); } catch (e) {}
    };
    window.addEventListener('message', (ev) => { try { const d = ev.data; if (d && d.__dsh) setAndPropagate(d.__dsh); } catch (e) {} });
    const wanted = /seller\\.shopee/i;          // request den seller.shopee.* (webchat mini/login)
    const pathWanted = /mini\\/login/i;
    const parse = (txt, u) => {
      try {
        const us = String(u || '');
        if (!wanted.test(us) || !pathWanted.test(us)) return;
        const o = JSON.parse(txt);
        if (o && o.user && o.user.name) setAndPropagate(o.user.name);
      } catch (e) {}
    };
    const of = window.fetch;
    if (of) window.fetch = function () {
      const u = typeof arguments[0] === 'string' ? arguments[0] : (arguments[0] && arguments[0].url) || '';
      const p = of.apply(this, arguments);
      p.then(function (r) { if (r && r.ok) { try { const c = r.clone(); c.text().then(function (t) { parse(t, u); }); } catch (e) {} } }).catch(function () {});
      return p;
    };
    const ox = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (m, u) { this.__dshUrl = u; return ox.apply(this, arguments); };
    const osend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function () {
      this.addEventListener('readystatechange', function () {
        if (this.readyState === 4 && this.status === 200) { try { parse(this.responseText, this.__dshUrl); } catch (e) {} }
      });
      return osend.apply(this, arguments);
    };
  })();`;
  try { await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: HOOK }); } catch (e) {}
  try { await cdp.send('Page.navigate', { url: URL }); } catch (e) { process.stderr.write('NAV loi: ' + (e && e.message) + '\n'); process.exit(7); }

  const deadline = Date.now() + TIMEOUT;
  let current = '';
  let lastErr = '';
  while (Date.now() < deadline) {
    try { current = String(await cdp.evaluate('location.href') || ''); } catch (e) { lastErr = e.message; }
    const u = current || '';
    if (/\/login(\?|$)/.test(u) || /accounts\.shopee/.test(u)) { fail('no_login', 'Chua dang nhap: ' + u); return; }
    if (/\/verify\/(captcha|traffic)/.test(u)) { fail('captcha', 'Bi chan captcha/traffic: ' + u); return; }
    let name = null;
    try { name = String(await cdp.evaluate('window.__dshName || ""') || ''); } catch (e) { lastErr = e.message; }
    if (name) { out({ status: 'ok', shopee_id: name, url: URL, detail: 'Lay tu response mini/login (user.name).' }); process.exit(0); }
    await sleep(POLL);
  }
  fail('timeout', (lastErr ? 'last err: ' + lastErr + ' | ' : '') + 'current url: ' + (current || 'unknown'));
}

main().then(() => process.exit(0)).catch((e) => { process.stderr.write('FATAL: ' + (e && e.message) + '\n'); process.exit(1); });

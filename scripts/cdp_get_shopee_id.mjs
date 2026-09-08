// cdp_get_shopee_id.mjs - Lấy "Shopee ID" (Username) cua profile dang dang nhap Shopee.
// Dung chung cho engine GPM (GPMLogin) va GEM (GemLogin):
//  1) start profile (qua Local API) -> CDP port that
//  2) mo tab toi {url} (trang user/account/profile)
//  3) poll: neu bi chuyen sang /buyer/login  -> chua dang nhap
//          neu bi chuyen sang /verify/...     -> captcha/traffic
//          neu DOM co "Username" (XPath nhu UI yeu cau) -> lay text lam Shopee ID
//  4) IN RA DUY NHAT 1 dong JSON len stdout de server doc; log loi ra stderr.
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

function out(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
function fail(status, detail) { out({ status, shopee_id: null, url: URL, detail }); process.exit(0); }

async function startProfile() {
  // GPM: khong yeu cau port -> GPM tra remote_debugging_port that (cung kieu _gpm_ensure_browser)
  const base = ENGINE === 'gem' ? GEM_BASE : GPM_BASE;
  const path = ENGINE === 'gem' ? `/api/profiles/start/${PROFILE}` : `/api/v1/profiles/start/${PROFILE}`;
  const doStart = async () => {
    const r = await fetch(base + path);
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch (e) {}
    if (!r.ok || !(j && j.success)) return { ok: false, raw: t };
    return { ok: true, data: (j && j.data) || {} };
  };
  let res = await doStart();
  if (!res.ok && /InUse/i.test(res.raw)) {
    try {
      const stop = ENGINE === 'gem' ? `/api/profiles/close/${PROFILE}` : `/api/v1/profiles/stop/${PROFILE}`;
      await fetch(base + stop);
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
    try { const v = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); if (v.Browser) return true; } catch (e) {}
    await sleep(500);
  }
  return false;
}

async function openTab(port, url) {
  const u = `http://127.0.0.1:${port}/json/new?${url}`;
  for (const method of ['PUT', 'GET']) {
    try {
      const r = await fetch(u, { method });
      if (r.ok) {
        const tab = await r.json();
        if (tab && tab.webSocketDebuggerUrl) return tab;
      }
    } catch (e) {}
  }
  return null;
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
  const tab = await openTab(st.port, URL);
  if (!tab) { process.stderr.write('Mo tab that bai\n'); process.exit(5); }
  const cdp = new Cdp(tab.webSocketDebuggerUrl);
  try { await cdp.connect(); } catch (e) { process.stderr.write('WS loi: ' + e.message + '\n'); process.exit(6); }
  await cdp.send('Runtime.enable').catch(() => {});
  const xpathExpr = `(() => {
    try {
      const el = document.evaluate('//*[text()="Username"]/parent::*/parent::*//div//div',
        document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
      return el ? String(el.innerText || el.textContent || '').trim() : null;
    } catch (e) { return null; }
  })()`;

  const deadline = Date.now() + TIMEOUT;
  let current = '';
  let lastErr = '';
  while (Date.now() < deadline) {
    try { current = String(await cdp.evaluate('location.href') || ''); } catch (e) { lastErr = e.message; }
    const u = current || '';
    if (/\/buyer\/login(\?|$)/.test(u)) { fail('no_login', 'Chua dang nhap: ' + u); return; }
    if (/\/verify\/(captcha|traffic)/.test(u)) { fail('captcha', 'Bi chan captcha/traffic: ' + u); return; }
    let idText = null;
    try { idText = await cdp.evaluate(xpathExpr); } catch (e) { lastErr = e.message; }
    if (idText) { out({ status: 'ok', shopee_id: idText, url: URL, detail: '' }); return; }
    await sleep(POLL);
  }
  fail('timeout', (lastErr ? 'last err: ' + lastErr + ' | ' : '') + 'current url: ' + (current || 'unknown'));
}

main().catch((e) => { process.stderr.write('FATAL: ' + (e && e.message) + '\n'); process.exit(1); });

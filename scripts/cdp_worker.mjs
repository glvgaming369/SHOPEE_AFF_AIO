// CDP Worker v2 — moi root bang co che DA CHUNG MINH code:0:
//   (1) fast path: Runtime.evaluate fetch(offer) trong page (SDK tu gan header/cookie);
//   (2) fallback chinh: dieu huong tab toi product_offer/<id> (load that -> report moi)
//       roi CHUP response offer/product qua Network (request do chinh trang gui) -> nav_complete.
// Gap bi chan (verify/403/90309999) -> clean_reload 1 lan. Item loi that (error_not_found sau load
// that) -> danh fail va chay tiep. Khong can Tampermonkey.
// Chay: node scripts/cdp_worker.mjs --port 9333 --device-key <ten> --market ph [--max-roots N]
import { appendFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawn } from 'node:child_process';

const arg = (n, d) => { const i = process.argv.indexOf(n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const PORT = parseInt(arg('--port', '9333'), 10);
const DEVICE = arg('--device-key', 'cdp-worker');
const MARKET = arg('--market', 'ph');
const SERVER = arg('--server', 'http://127.0.0.1:8877');
// Che do GPM Login: neu co --gpm-profile (uuid), worker start profile qua GPM local API
// roi attach vao CDP port -> KHONG tu dong mo Chrome nua.
const GPM_PORT = parseInt(arg('--gpm-port', '9495'), 10);
const GPM_PROFILE = arg('--gpm-profile', '');
const STOP_ON_EXIT = arg('--stop-on-exit', '1') === '1';
const HIDDEN = arg('--hidden', '0') === '1'; // headed + tu thu nho cua so (an tam nhin, fingerprint that)

async function minimizeWindowByPid(pid) {
  // goi PowerShell: ShowWindow(hwnd, SW_MINIMIZE=6) cho process id
  const script = `Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class GpmW{[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int c);}'; try { $p = Get-Process -Id ${pid} -ErrorAction Stop; [GpmW]::ShowWindow($p.MainWindowHandle, 6) | Out-Null } catch { }`;
  try { spawn('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { stdio: 'ignore' }); } catch (e) {}
}
const POLL_MS = parseInt(arg('--poll', '3500'), 10);
const MAX_ROOTS = parseInt(arg('--max-roots', '0'), 10);
const MIN_DELAY_MS = parseInt(arg('--min-delay', '500'), 10);
const MAX_DELAY_MS = parseInt(arg('--max-delay', '1500'), 10);
const NAV_TIMEOUT_MS = parseInt(arg('--nav-timeout', '16000'), 10);
const LOG = resolve(arg('--log', 'artifacts/cdp_worker.log'));
const HOST = { ph: 'affiliate.shopee.ph', th: 'affiliate.shopee.co.th', my: 'affiliate.shopee.com.my', vn: 'affiliate.shopee.vn', sg: 'affiliate.shopee.sg' }[MARKET];
if (!HOST) { console.error('market khong ho tro:', MARKET); process.exit(2); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  try { appendFileSync(LOG, line + '\n'); } catch (e) {}
}

async function serverJson(method, path, body) {
  const r = await fetch(SERVER + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) {}
  if (!r.ok) throw new Error('server ' + path + ' -> HTTP ' + r.status + ' ' + t.slice(0, 160));
  return j;
}

async function cdpConnect(page) {
  return new Promise((resolve_, reject) => {
    const ws = new WebSocket(page.webSocketDebuggerUrl);
    const pending = new Map(); const listeners = new Set(); let id = 0;
    ws.onopen = () => resolve_({
      send(m, p = {}) { return new Promise((res, rej) => { const mid = ++id; pending.set(mid, { res, rej }); ws.send(JSON.stringify({ id: mid, method: m, params: p })); }); },
      on(fn) { listeners.add(fn); },
      off(fn) { listeners.delete(fn); },
      close() { try { ws.close(); } catch (e) {} },
    });
    ws.onerror = () => reject(new Error('ws error'));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); return; }
      if (msg.method) for (const fn of listeners) fn(msg);
    };
  });
}

async function getOrOpenPage(port) {
  let list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  let page = list.find((x) => x.type === 'page' && /affiliate\.shopee/.test(x.url || ''));
  if (page) return page;
  const ver = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const bws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((r) => (bws.onopen = r));
  const target = await new Promise((res, rej) => {
    bws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id === 1) res(m.result); };
    bws.onerror = rej;
    bws.send(JSON.stringify({ id: 1, method: 'Target.createTarget', params: { url: `https://${HOST}/` } }));
  });
  try { bws.close(); } catch (e) {}
  for (let i = 0; i < 20; i++) { await sleep(500); list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = list.find((x) => x.type === 'page' && /affiliate\.shopee/.test(x.url || '')); if (p) return p; }
  return null;
}

async function evaluate(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error('eval err: ' + JSON.stringify(r.exceptionDetails).slice(0, 200));
  return r.result && r.result.value;
}

// ---------- Fast path: bare fetch trong page ----------
async function pageFetchOffer(cdp, itemid) {
  const expr = `(async () => {
    try {
      const r = await fetch('/api/v3/offer/product?item_id=${itemid}', {
        credentials: 'include',
        headers: { 'accept': 'application/json, text/plain, */*', 'affiliate-program-type': '1' },
      });
      const t = await r.text();
      let j = null; try { j = JSON.parse(t); } catch (e) {}
      return { status: r.status, code: j && j.code, data: (j && j.data) || null, head: t.slice(0, 80) };
    } catch (e) { return { err: String(e).slice(0, 200) }; }
  })()`;
  return evaluate(cdp, expr);
}

// ---------- Fallback: dieu huong + chup offer do CHINH TRANG gui ----------
function captureOfferByNav(cdp, itemid) {
  return new Promise((resolve_) => {
    let done = false;
    const offerReqs = new Map(); // requestId -> true (la offer cua dung item)
    const finish = (v) => { if (!done) { done = true; cdp.off(onEvent); resolve_(v); } };
    const onEvent = (msg) => {
      (async () => {
        try {
          if (msg.method === 'Network.responseReceived') {
            const { requestId, response } = msg.params;
            if ((response.url || '').includes('/api/v3/offer/product') && response.url.includes('item_id=' + itemid)) {
              offerReqs.set(requestId, true);
            }
          }
          if (msg.method === 'Network.loadingFinished' && offerReqs.has(msg.params.requestId)) {
            offerReqs.delete(msg.params.requestId);
            const b = await cdp.send('Network.getResponseBody', { requestId: msg.params.requestId });
            const t = b && b.body ? (b.base64Encoded ? Buffer.from(b.body, 'base64').toString('utf8') : b.body) : '';
            let j = null; try { j = JSON.parse(t); } catch (e) {}
            finish({ code: j && j.code, data: (j && j.data) || null, head: t.slice(0, 80) });
          }
        } catch (e) {}
      })();
    };
    cdp.on(onEvent);
    cdp.send('Page.navigate', { url: `https://${HOST}/offer/product_offer/${itemid}` }).catch(() => {});
  });
}

async function captureOfferByNavTimed(cdp, itemid) {
  const p = captureOfferByNav(cdp, itemid);
  const timer = new Promise((res) => setTimeout(() => res({ code: null, data: null, head: '(timeout nav)' }), NAV_TIMEOUT_MS));
  const res = await Promise.race([p, timer]);
  return res;
}

function looksBlocked(res) {
  if (!res) return true;
  if (res.status === 403 || res.status === 429) return true;
  if (res.code === 90309999) return true;
  if (res.code === null && res.head && /verify\/traffic|page unavailable|captcha/i.test(res.head)) return true;
  return false;
}

async function failRoot(root, reason) {
  try {
    await serverJson('POST', `/api/roots/${encodeURIComponent(root.itemid)}/fail`, { reason: String(reason).slice(0, 200), market: root.market || MARKET });
    log(`    Da danh dau root ${root.itemid} la fail (${String(reason).slice(0, 80)})`);
  } catch (e) { log('    failRoot loi: ' + e.message); }
}

async function cleanReload(cdp) {
  log('clean_reload...');
  await evaluate(cdp, `(function(){ try { document.cookie='shopee_webUnique_ccd=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT'; } catch(e){} location.reload(); return true; })()`);
  await sleep(8000);
}

// Kiem tra tab co dang o trang CAPTCHA/verify cua Shopee khong (URL dang
// /verify/captcha?...anti_bot_tracking_id=... hoac /verify/traffic...). Neu dung -> profile
// dang dinh captcha: worker phai DUNG va khong nhan root moi nua cho den khi nguoi dung
// giai captcha (tra ve true).
async function pageIsCaptcha(cdp) {
  try {
    const u = await evaluate(cdp, 'location.href');
    return /\/verify\/(captcha|traffic)(\?|$)/i.test(u || '') || /anti_bot_tracking_id/i.test(u || '');
  } catch (e) { return false; }
}

async function processOfferData(root, res) {
  const itemid = String(root.itemid);
  try {
    const nav = await serverJson('POST', '/api/roots/nav_complete', { offer_data: res.data, market: MARKET });
    log(`=== XONG root ${itemid}: outcome=${nav.outcome} (${(nav.member_count || 0) + 1}/6)`);
    if (nav.detail) {
      const d = nav.detail;
      log(`  [nav] similar=${d.similar_total} claimed=${d.claimed} sold_pass=${d.sold_passed} outcomes=${JSON.stringify(d.outcomes)}`);
    }
    return true;
  } catch (e) {
    log('!!! nav_complete loi: ' + e.message + ' - thu lai 1 lan');
    try {
      const nav = await serverJson('POST', '/api/roots/nav_complete', { offer_data: res.data, market: MARKET });
      log(`=== XONG root ${itemid} (lan 2): outcome=${nav.outcome}`);
      return true;
    } catch (e2) { log('!!! nav_complete loi lan 2: ' + e2.message); }
    return false;
  }
}

async function handleRoot(cdp, root, heartbeat) {
  const itemid = String(root.itemid);
  log(`>>> Xu ly root ${itemid}`);
  await heartbeat('working', itemid);

  // 1) fast path
  let res = await pageFetchOffer(cdp, itemid);
  let mode = 'fetch';
  if (!res || res.code !== 0 || !res.data) {
    mode = 'nav';
    log(`    fast-path khong ok (${JSON.stringify(res).slice(0, 100)}) -> dieu huong + chup`);
    res = await captureOfferByNavTimed(cdp, itemid);
  }
  // Sau khi truy cap truc tiep 1 san pham: neu URL roi vao trang verify/captcha (VD
  // /verify/captcha?...anti_bot_tracking_id=...) thi profile DANG DINH CAPTCHA -> DUNG ngay,
  // khong nhan root moi (cho nguoi giai captcha roi chay lai).
  if (await pageIsCaptcha(cdp)) {
    log(`!!! Root ${itemid}: tab dang o trang CAPTCHA/verify (${String(await evaluate(cdp, 'location.href')).slice(0, 160)}) - DUNG worker, can giai captcha tren profile nay.`);
    await heartbeat('blocked', itemid);
    return 'blocked';
  }
  if (looksBlocked(res)) {
    log(`    bi chan (${JSON.stringify(res).slice(0, 100)}) -> clean_reload roi thu lai 1 lan`);
    await cleanReload(cdp);
    await sleep(2000);
    if (await pageIsCaptcha(cdp)) {
      log(`!!! Root ${itemid}: sau reload van o trang CAPTCHA - DUNG worker (can giai captcha).`);
      await heartbeat('blocked', itemid);
      return 'blocked';
    }
    res = mode === 'nav' ? await captureOfferByNavTimed(cdp, itemid) : await pageFetchOffer(cdp, itemid);
  }
  if (await pageIsCaptcha(cdp)) {
    log(`!!! Root ${itemid}: tab van o trang CAPTCHA sau khi thu lai - DUNG worker (can giai captcha).`);
    await heartbeat('blocked', itemid);
    return 'blocked';
  }
  if (looksBlocked(res)) {
    log(`!!! Root ${itemid} VAN bi chan sau reload - dung worker, kiem tra login/captcha`);
    await heartbeat('blocked', itemid);
    return 'blocked';
  }
  if (!res || res.code !== 0 || !res.data) {
    // Truoc khi danh fail (co the la item het han), kiem tra lai 1 lan xem co phai dang
    // ke 1 trang captcha khac dang load cham (de khong fail nham roi nhan root tiep theo).
    if (await pageIsCaptcha(cdp)) {
      log(`!!! Root ${itemid}: response khong chuan + tab dang o trang CAPTCHA - DUNG worker.`);
      await heartbeat('blocked', itemid);
      return 'blocked';
    }
    const reason = (res && (res.head || ('code=' + res.code))) || 'unknown';
    await failRoot(root, reason);
    return 'ok';
  }
  await processOfferData(root, res);
  await heartbeat('idle');
  return 'ok';
}

async function gpmStartProfile() {
  const doStart = async () => {
    // GET http://127.0.0.1:<gpm-port>/api/v1/profiles/start/<uuid>?remote_debugging_port=<port>
    const url = `http://127.0.0.1:${GPM_PORT}/api/v1/profiles/start/${GPM_PROFILE}?remote_debugging_port=${PORT}`;
    const r = await fetch(url);
    const t = await r.text();
    let j = null; try { j = JSON.parse(t); } catch (e) {}
    if (!r.ok || !(j && j.success)) return { ok: false, raw: t };
    return { ok: true, data: (j && j.data) || {} };
  };
  let res = await doStart();
  if (!res.ok && /InUse/i.test(res.raw)) {
    log('GPM bao ProfileInUse - tu stop roi start lai 1 lan...');
    try { await fetch(`http://127.0.0.1:${GPM_PORT}/api/v1/profiles/stop/${GPM_PROFILE}`); } catch (e) {}
    await sleep(2500);
    res = await doStart();
  }
  if (!res.ok) throw new Error('GPM start fail: ' + res.raw.slice(0, 200));
  const d = res.data;
  log(`GPM start: ${GPM_PROFILE} -> port=${d.remote_debugging_port} ws=${d.websocket_debugging_url} ${(d.addition_info || {}).profile_name || ''}`);
  if (HIDDEN) {
    const pid = (d.addition_info || {}).process_id;
    if (pid) { minimizeWindowByPid(pid); log('(hidden) da yeu cau thu nho cua so worker.'); }
    else log('(hidden) khong co process_id de thu nho - de cua so nhu thuong.');
  }
  return d;
}

async function waitCdpUp(port, tries = 40) {
  for (let i = 0; i < tries; i++) {
    try { const v = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); if (v.Browser) return true; } catch (e) {}
    await sleep(500);
  }
  return false;
}

async function main() {
  let cdpPort = PORT; // co the bi GPM cap port khac (khi browser da chay san) - dung port THAT
  log(`CDP worker start: port=${PORT} device=${DEVICE} market=${MARKET}` + (GPM_PROFILE ? ` gpm-profile=${GPM_PROFILE}` : ' (chrome debug co san)'));
  if (GPM_PROFILE) {
    log('Start profile qua GPM Login...');
    try {
      const d = await gpmStartProfile();
      if (d && d.remote_debugging_port && d.remote_debugging_port !== PORT) {
        cdpPort = d.remote_debugging_port;
        log(`GPM cap port khac -> dung port ${cdpPort}`);
      }
    } catch (e) {
      log('GPM start loi: ' + e.message);
    }
    if (!(await waitCdpUp(cdpPort))) {
      log('KHONG mo duoc CDP port ' + cdpPort + ' sau khi GPM start - kiem tra GPM app/port');
      return;
    }
    log('CDP san sang qua GPM (port ' + cdpPort + ').');
  }
  const page = await getOrOpenPage(cdpPort);
  if (!page) { log('KHONG mo duoc tab affiliate - kiem tra Chrome debug port ' + PORT); return; }
  const cdp = await cdpConnect(page);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Network.enable');
  const cur = await evaluate(cdp, 'location.href');
  if (!/affiliate\.shopee/.test(cur || '')) {
    log('tab khong o affiliate, dieu huong ve home: ' + cur);
    await cdp.send('Page.navigate', { url: `https://${HOST}/` });
    await sleep(8000);
  }
  const hb = (s, root) => {
    try { serverJson('POST', '/api/workers/heartbeat', { device_key: DEVICE, status: s, current_root: root || null, market: MARKET }); } catch (e) {}
  };
  await hb('idle');

  let doneRoots = 0;
  while (true) {
    if (MAX_ROOTS > 0 && doneRoots >= MAX_ROOTS) {
      log(`Da xu ly du ${doneRoots} root (max-roots) - dung worker.`);
      break;
    }
    let resp;
    try {
      resp = await serverJson('GET', `/api/workers/${encodeURIComponent(DEVICE)}/assigned_root?market=${encodeURIComponent(MARKET)}`);
    } catch (e) {
      log('loi hoi server: ' + e.message + ' - thu lai sau 5s');
      await sleep(5000);
      continue;
    }
    const root = resp && resp.root;
    if (!root) { await hb('idle'); await sleep(POLL_MS); continue; }
    const outcome = await handleRoot(cdp, root, hb);
    if (outcome === 'blocked') break;
    doneRoots++;
    const d = MIN_DELAY_MS + Math.floor(Math.random() * (MAX_DELAY_MS - MIN_DELAY_MS));
    await sleep(d);
  }
  log('Worker ket thuc.');
  cdp.close();
  if (GPM_PROFILE && STOP_ON_EXIT) {
    try {
      const r = await fetch(`http://127.0.0.1:${GPM_PORT}/api/v1/profiles/stop/${GPM_PROFILE}`);
      log('GPM stop: HTTP ' + r.status);
    } catch (e) { log('GPM stop loi: ' + e.message); }
  }
}

main().catch((e) => { console.error('FATAL:', e.message); process.exit(1); });

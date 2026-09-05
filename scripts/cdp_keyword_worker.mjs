// CDP Keyword Worker v2 - cao root AFF theo TU KHOA (tinh nang "Cào root AFF", xem
// Cao_root_aff.txt). Moi tu khoa: claim tu server (/api/keywords/claim) -> goi TRUC TIEP
// /api/v3/offer/product/list bang fetch() ben trong CHINH trang affiliate (SFU wrapper cua
// trang tu gan header chong bot + cookie session cua profile) -> lap qua cac page_offset
// cho toi khi het total_count -> day tung trang ve /api/keywords/page_done de server loc
// (loc theo "Điều kiện lọc chung" - sold/hoa hong tien cua market - phia server luc
// page_done; xem keyword_page_done fallback get_settings) + insert root pending.
//
// DA XAC MINH BANG PROBE THAT (2026-09-05, profile SHOPEE 001, affiliate.shopee.ph):
//  - goi product/list lien tuc 4+ lan trong CUNG page-view deu code:0 - khong gap loi
//    "token 1 page-view dung 1 lan" (90309999) nhu offer/product cua root (xem RE_PLAN.md).
//  - page_limit: chap nhan toi da 50 (>=60 tra code:400 "params error,property:page_limit";
//    500 cung bi choi). Worker mac dinh PAGE_LIMIT=50 de giam request.
//  - page_offset la so ITEM da bo qua (item-offset, KHONG phai so trang): offset=1 tra list
//    bat dau tu item thu 2 -> moi trang sau phai nhay offset += PAGE_LIMIT (0, 50, 100...).
// Do do chi can fetch trực tiếp voi delay nho giua cac call, kem co che clean_reload+retry
// neu bi chan (403/90309999/Page Unavailable).
//
// Chay: node scripts/cdp_keyword_worker.mjs --port 9701 --device-key <ten> --market ph
//       [--gpm-profile <uuid> --gpm-port 9495] [--max-keywords N]
//       [--sort-type 2 --filter-types 0 --page-limit 50]
import { appendFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawn } from 'node:child_process';

const arg = (n, d) => { const i = process.argv.indexOf(n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const PORT = parseInt(arg('--port', '9701'), 10);
const DEVICE = arg('--device-key', 'cdp-keyword-worker');
const MARKET = arg('--market', 'ph');
const SERVER = arg('--server', 'http://127.0.0.1:8877');
const GPM_PORT = parseInt(arg('--gpm-port', '9495'), 10);
const GPM_PROFILE = arg('--gpm-profile', '');
const STOP_ON_EXIT = arg('--stop-on-exit', '1') === '1';
const HIDDEN = arg('--hidden', '0') === '1';
const POLL_MS = parseInt(arg('--poll', '4000'), 10);
const MAX_KEYWORDS = parseInt(arg('--max-keywords', '0'), 10);
const MIN_DELAY_MS = parseInt(arg('--min-delay', '2500'), 10);
const MAX_DELAY_MS = parseInt(arg('--max-delay', '5000'), 10);
const NAV_TIMEOUT_MS = parseInt(arg('--nav-timeout', '20000'), 10);
// Gioi han page_limit DA TEST THAT (profile SHOPEE 001): 50 la toi da chap nhan, >=60 tra
// code:400 "params error,property:page_limit" (500 cung bi choi). Mac dinh dung 50 de giam
// so request: keyword total_count=500 -> chi 10 trang (so voi 25 neu page_limit=20).
const PAGE_LIMIT = parseInt(arg('--page-limit', '50'), 10) || 50;
// Cau hinh "khi cào" - nhap o tab Vận hành GPM luc start worker (server truyen qua args),
// KHONG luu o import. Worker gui kem MOI trang page_done de server loc dung 1 lan nay.
const SORT_TYPE = parseInt(arg('--sort-type', '2'), 10) || 2;        // 1=Relevance 2=Top Sales
const FILTER_TYPES = parseInt(arg('--filter-types', '0'), 10) || 0;  // 2=Comm Xtra
const LOG = resolve(arg('--log', 'artifacts/cdp_keyword_worker.log'));

const HOST = { ph: 'affiliate.shopee.ph', th: 'affiliate.shopee.co.th', my: 'affiliate.shopee.com.my', vn: 'affiliate.shopee.vn', sg: 'affiliate.shopee.sg' }[MARKET];
if (!HOST) { console.error('market khong ho tro:', MARKET); process.exit(2); }
const LISTING_BASE = `https://${HOST}/offer/product_offer`;
const MAX_PAGES = 300; // tranh vong lap vo han

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
  if (!r.ok && !j) throw new Error('server ' + path + ' -> HTTP ' + r.status + ' ' + t.slice(0, 200));
  return j || {};
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
  await new Promise((res, rej) => {
    bws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id === 1) res(m.result); };
    bws.onerror = rej;
    bws.send(JSON.stringify({ id: 1, method: 'Target.createTarget', params: { url: `https://${HOST}/` } }));
  });
  try { bws.close(); } catch (e) {}
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const p = list.find((x) => x.type === 'page' && /affiliate\.shopee/.test(x.url || ''));
    if (p) return p;
  }
  return null;
}

async function evaluate(cdp, expression) {
  const r = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) return { evalError: String((r.exceptionDetails.exception || {}).description || r.exceptionDetails.text).slice(0, 300) };
  return r.result && r.result.value;
}

async function minimizeWindowByPid(pid) {
  const script = `Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class GpmW{[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int c);}'; try { $p = Get-Process -Id ${pid} -ErrorAction Stop; [GpmW]::ShowWindow($p.MainWindowHandle, 6) | Out-Null } catch { }`;
  try { spawn('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { stdio: 'ignore' }); } catch (e) {}
}

async function gpmStartProfile() {
  const doStart = async () => {
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
  log(`GPM start: ${GPM_PROFILE} -> port=${d.remote_debugging_port} ${(d.addition_info || {}).profile_name || ''}`);
  if (HIDDEN) {
    const pid = (d.addition_info || {}).process_id;
    if (pid) { minimizeWindowByPid(pid); log('(hidden) da yeu cau thu nho cua so worker.'); }
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

async function heartbeat(cdp, status, kwText) {
  try {
    await serverJson('POST', '/api/workers/heartbeat', { device_key: DEVICE, status, current_root: kwText || null, market: MARKET });
  } catch (e) {}
}

// ---------- Truc tiep goi product/list ben trong trang (fast-path, da xac minh OK nhieu lan) ----------
function listCallExpr(keyword, offset) {
  return `(async () => {
    const url = '/api/v3/offer/product/list?list_type=0&keyword=' + encodeURIComponent(${JSON.stringify(keyword)})
      + '&sort_type=${SORT_TYPE}&page_offset=' + ${offset} + '&page_limit=${PAGE_LIMIT}'
      + '&client_type=1&filter_types=${FILTER_TYPES}';
    try {
      const r = await fetch(url, { credentials: 'include', headers: { 'accept': 'application/json, text/plain, */*', 'affiliate-program-type': '1' } });
      const t = await r.text();
      let j = null; try { j = JSON.parse(t); } catch (e) {}
      return {
        http: r.status,
        code: j && j.code, msg: j && j.msg,
        list: (j && j.data && Array.isArray(j.data.list)) ? j.data.list : [],
        page_offset: j && j.data && j.data.page_offset,
        page_limit: j && j.data && j.data.page_limit,
        total_count: (j && j.data && j.data.total_count) != null ? j.data.total_count : null,
        head: t.slice(0, 100),
      };
    } catch (e) { return { fetchErr: String(e).slice(0, 200) }; }
  })()`;
}

function looksBlocked(res) {
  if (!res) return true;
  if (res.evalError || res.fetchErr) return false; // loi ky thuat, khong phai chan
  if (res.http === 403 || res.http === 429) return true;
  if (res.code === 90309999) return true;
  if (res.code === null && res.head && /page unavailable|verify|captcha|login/i.test(res.head)) return true;
  return false;
}

async function settlePage(cdp, ms) {
  // cho SDK/engine cua trang nap xong (report/token) truoc khi goi list
  await sleep(ms || 12000);
  const st = await evaluate(cdp, `({ href: location.href, login: /login/i.test(location.href) })`);
  if (st && st.login) return { ok: false, reason: 'login-page' };
  return { ok: true };
}

async function cleanReload(cdp) {
  log('clean_reload + cho SDK nap lai...');
  await evaluate(cdp, `(function(){ try { document.cookie='shopee_webUnique_ccd=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT'; } catch(e){} location.href='${LISTING_BASE}'; return true; })()`);
  await sleep(13000);
}

async function ensureOnListing(cdp) {
  const cur = await evaluate(cdp, 'location.href');
  if (!/affiliate\.shopee/.test(String(cur || '')) || !/offer\/product_offer/.test(String(cur || ''))) {
    log('tab chua o product_offer - dieu huong ve listing: ' + JSON.stringify(cur));
    await evaluate(cdp, `location.href='${LISTING_BASE}'; true`);
    await sleep(6000);
  }
  const st = await settlePage(cdp);
  if (!st.ok) log('!!! Co ve dang o trang LOGIN - kiem tra profile da dang nhap affiliate.');
  return st.ok;
}

async function postPage(keywordId, offset, total, items) {
  const res = await serverJson('POST', '/api/keywords/page_done', {
    keyword_id: keywordId, device_key: DEVICE, market: MARKET,
    page_offset: offset, page_limit: PAGE_LIMIT, total_count: total, items,
    // Cau hinh KHI CAO (API param) - sort/filter goi trong URL. Lượt bán & Hoa hồng khong
    // gui o day: server tu lay "Điều kiện lọc chung" (settings) khi loc (xem keyword_page_done).
    filter_types: FILTER_TYPES,
  });
  if (res && res.ok === false) throw new Error('server page_done: ' + (res.error || 'unknown'));
  return res;
}

async function handleKeyword(cdp, kw) {
  const id = kw.id;
  const text = kw.keyword;
  const label = `#${id} "${text}" (${kw.market})`;
  log(`>>> Xu ly keyword ${label} | cau hinh cao: sort=${SORT_TYPE} filter=${FILTER_TYPES} page_limit=${PAGE_LIMIT} (sold/hoa hong lay tu Điều kiện lọc chung tren server)`);
  await heartbeat(cdp, 'working', `kw#${id} ${text}`);

  let blockedCount = 0;
  let badRespCount = 0; // response loi thoang qua (khong phai chan, khong phai data hop le)
  let pagesSinceReload = 0;
  let calls = 0; // so lan goi API (canh phong vong lap vo han; offset nhay PAGE_LIMIT/lan)
  let last = null;
  let offset = 0;
  let total = null;

  while (calls < MAX_PAGES) {
    calls++;
    const res = await evaluate(cdp, listCallExpr(text, offset));
    if (res && (res.evalError || res.fetchErr)) {
      log('  loi goi list (ky thuat): ' + JSON.stringify(res).slice(0, 200) + ' - clean_reload + thu lai');
      await cleanReload(cdp);
      const st = await settlePage(cdp);
      if (!st.ok) { blockedCount++; log('  van o trang login sau reload - danh blocked'); }
      if (++blockedCount >= 3) break;
      continue; // thu lai dung offset sau khi reload
    }
    if (looksBlocked(res)) {
      blockedCount++;
      log(`  BI CHAN (lan ${blockedCount}): ${JSON.stringify({ http: res.http, code: res.code, head: res.head }).slice(0, 160)} - clean_reload roi thu lai trang ${offset}`);
      await cleanReload(cdp);
      if (blockedCount >= 3) {
        log(`!!! Keyword ${label} bi chan lien tuc - dung worker (kiem tra login/captcha).`);
        await heartbeat(cdp, 'blocked', `kw#${id} ${text}`);
        return 'blocked';
      }
      continue;
    }
    if (!res || res.code !== 0 || !Array.isArray(res.list)) {
      // response khong hop le nhung cung khong phai chan ro rang - co the la loi thoang qua
      // (SDK chua san, HTML loi khong khop heuristic, code khac 0 tam thoi...). Reload va thu
      // lai CUNG offset toi 3 lan truoc khi danh fail that su.
      const detail = JSON.stringify({ http: res && res.http, code: res && res.code, msg: res && res.msg, head: res && res.head }).slice(0, 200);
      badRespCount++;
      log(`  response loi (lan ${badRespCount}): ${detail} - clean_reload + thu lai offset ${offset}`);
      await cleanReload(cdp);
      if (badRespCount >= 3) {
        log(`  van loi sau 3 lan reload -> danh fail keyword ${label}.`);
        const reason = 'list_bad_response:' + JSON.stringify({ http: res && res.http, code: res && res.code, msg: res && res.msg });
        try { await serverJson('POST', `/api/keywords/${id}/fail`, { reason }); } catch (e) {}
        return 'fail';
      }
      continue;
    }
    badRespCount = 0; // co response hop le - reset dem loi thoang qua

    const list = res.list;
    if (res.total_count != null) total = res.total_count;
    log(`  [${text}] offset ${offset}: ${list.length} item | page_limit=${PAGE_LIMIT} | total_count=${total}`);
    try {
      last = await postPage(id, offset, total, list);
    } catch (e) {
      log('  post trang ' + offset + ' loi: ' + e.message + ' - thu lai 1 lan');
      try { last = await postPage(id, offset, total, list); } catch (e2) {
        log('  post loi lan 2 - danh fail.');
        try { await serverJson('POST', `/api/keywords/${id}/fail`, { reason: 'page_done_error' }); } catch (e3) {}
        return 'fail';
      }
    }
    pagesSinceReload++;
    if (last && last.finished) {
      log(`=== XONG keyword ${label}: het du lieu o offset ${offset} (insert them=${last.inserted} dup=${last.dup_skipped}; tong root ${last.roots_inserted_total}) ===`);
      return 'ok';
    }
    if (list.length === 0) {
      log(`  offset ${offset} rong -> server da tu danh done; ket thuc keyword.`);
      return 'ok';
    }
    if (total != null && offset + list.length >= total) {
      log(`  da quet toi duoi total (offset ${offset}) nhung server chua bao done - danh done de tranh loop.`);
      return 'ok';
    }
    // page_offset la so ITEM da bo qua (item-offset, da probe xac nhan) -> moi trang sau
    // nhay them dung PAGE_LIMIT item.
    offset += PAGE_LIMIT;
    // lam moi session sau ~20 call phong khi (chua thay chan o <10 call, chi la do an toan)
    if (pagesSinceReload >= 20) {
      log('  sau 20 call -> clean_reload lam moi session.');
      await cleanReload(cdp);
      pagesSinceReload = 0;
    } else {
      const d = MIN_DELAY_MS + Math.floor(Math.random() * Math.max(1, MAX_DELAY_MS - MIN_DELAY_MS));
      await sleep(d);
    }
  }
  log(`  vuot qua ${MAX_PAGES} lan goi API - danh fail de kiem tra.`);
  try { await serverJson('POST', `/api/keywords/${id}/fail`, { reason: 'too_many_pages' }); } catch (e) {}
  return 'fail';
}

async function main() {
  let cdpPort = PORT;
  log(`CDP keyword worker v2 start: port=${PORT} device=${DEVICE} market=${MARKET} page_limit=${PAGE_LIMIT}` + (GPM_PROFILE ? ` gpm-profile=${GPM_PROFILE}` : ' (chrome debug co san)'));
  if (GPM_PROFILE) {
    log('Start profile qua GPM Login...');
    try {
      const d = await gpmStartProfile();
      if (d && d.remote_debugging_port && d.remote_debugging_port !== PORT) {
        cdpPort = d.remote_debugging_port;
        log(`GPM cap port khac -> dung port ${cdpPort}`);
      }
    } catch (e) { log('GPM start loi: ' + e.message); }
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
  const onListing = await ensureOnListing(cdp);
  if (!onListing) {
    log('Profile co ve chua dang nhap affiliate - dung worker.');
    await heartbeat(cdp, 'blocked', 'login-required');
    cdp.close();
    return;
  }
  await heartbeat(cdp, 'idle');

  let doneKeywords = 0;
  while (true) {
    if (MAX_KEYWORDS > 0 && doneKeywords >= MAX_KEYWORDS) {
      log(`Da xu ly du ${doneKeywords} keyword (max-keywords) - dung worker.`);
      break;
    }
    let resp = null;
    try {
      resp = await serverJson('POST', '/api/keywords/claim', { device_key: DEVICE, market: MARKET });
    } catch (e) {
      log('loi claim keyword: ' + e.message + ' - thu lai sau ' + POLL_MS + 'ms');
      await sleep(POLL_MS);
      continue;
    }
    const kw = resp && resp.keyword;
    if (!kw) {
      await heartbeat(cdp, 'idle');
      await sleep(POLL_MS);
      continue;
    }
    const outcome = await handleKeyword(cdp, kw);
    if (outcome === 'blocked') break;
    doneKeywords++;
  }
  log('Keyword worker ket thuc.');
  cdp.close();
  if (GPM_PROFILE && STOP_ON_EXIT) {
    try {
      const r = await fetch(`http://127.0.0.1:${GPM_PORT}/api/v1/profiles/stop/${GPM_PROFILE}`);
      log('GPM stop: HTTP ' + r.status);
    } catch (e) { log('GPM stop loi: ' + e.message); }
  }
}

main().catch((e) => { console.error('FATAL:', e.message); process.exit(1); });

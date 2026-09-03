// PoC CDP v2: chup chinh xac request offer/product that (headers+status+body) sau 2 lan
// navigate, bang Network events (khong dung Fetch intercept).
//  Chay: node scripts/cdp_probe.mjs --port 9333 --item <root_id>
import { writeFileSync } from 'node:fs';

const args = process.argv.slice(2);
const argVal = (n, d) => { const i = args.indexOf(n); return i >= 0 && args[i + 1] ? args[i + 1] : d; };
const PORT = parseInt(argVal('--port', '9333'), 10);
const ITEM = argVal('--item', '');
const OUT = argVal('--out', 'artifacts/cdp_probe_result.json');
const MARKET_HOST = { ph: 'affiliate.shopee.ph' };
const HOST = MARKET_HOST.ph;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getTarget() {
  const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
  const t = list.find((x) => x.type === 'page' && /affiliate\.shopee/.test(x.url || ''));
  if (!t) {
    console.log('KHONG thay tab affiliate. Cac tab:');
    for (const x of list.filter((x) => x.type === 'page')) console.log('  -', x.url);
    return null;
  }
  return t;
}
function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map(); const listeners = new Set(); let id = 0;
    ws.onopen = () => resolve({
      send(m, p = {}) { return new Promise((res, rej) => { const mid = ++id; pending.set(mid, { res, rej }); ws.send(JSON.stringify({ id: mid, method: m, params: p })); }); },
      on(fn) { listeners.add(fn); }, close() { try { ws.close(); } catch (e) {} },
    });
    ws.onerror = () => reject(new Error('ws error'));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); return; }
      if (msg.method) for (const fn of listeners) fn(msg);
    };
  });
}

async function main() {
  if (!ITEM) { console.log('thieu --item'); return; }
  const target = await getTarget();
  if (!target) return;
  const cdp = await connect(target.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  await cdp.send('Network.enable');

  const offers = new Map(); // requestId -> info
  const results = [];
  cdp.on((msg) => {
    if (msg.method === 'Network.requestWillBeSent') {
      const { requestId, request } = msg.params;
      if (request.url && request.url.includes('/api/v3/offer/product')) {
        offers.set(requestId, { url: request.url, headers: request.headers });
      }
    }
    if (msg.method === 'Network.responseReceived') {
      const { requestId, response } = msg.params;
      if (offers.has(requestId)) {
        const it = offers.get(requestId);
        it.status = response.status;
        it.mime = response.mimeType;
      }
    }
    if (msg.method === 'Network.loadingFinished') {
      const { requestId } = msg.params;
      if (offers.has(requestId)) {
        (async () => {
          try {
            const b = await cdp.send('Network.getResponseBody', { requestId });
            const text = b && b.body ? (b.base64Encoded ? Buffer.from(b.body, 'base64').toString('utf8') : b.body) : '';
            const it = offers.get(requestId);
            it.bodyHead = text.slice(0, 300);
            results.push(it);
            offers.delete(requestId);
            const code = (text.match(/"code"\s*:\s*(-?\d+)/) || [])[1];
            console.log(`>>> OFFER ${it.url.includes('item_id=') ? it.url.split('item_id=')[1] : it.url} | status=${it.status} | code=${code}`);
            if (code !== '0') console.log('    body:', text.slice(0, 200));
          } catch (e) { console.log('getBody err', e.message); }
        })();
      }
    }
  });

  const nav = async (url) => { await cdp.send('Page.navigate', { url }); await sleep(9000); };
  const probeUrl = `https://${HOST}/offer/product_offer/${ITEM}`;
  await nav(probeUrl);
  await nav(probeUrl + '?t=2');

  // trang thai cuoi
  try {
    const st = await cdp.send('Runtime.evaluate', { expression: '({href:location.href, body:(document.body?document.body.innerText.slice(0,120):"")})', returnByValue: true });
    console.log('TRANG CUOI:', JSON.stringify(st.result.value));
  } catch (e) {}

  writeFileSync(OUT, JSON.stringify({ item: ITEM, results }, null, 2), 'utf8');
  console.log('\n=== HEADER THAT (request offer dau tien) ===');
  if (results.length) {
    const h = results[0].headers || {};
    for (const k of ['af-ac-enc-sz-token', 'af-ac-enc-dat', 'x-sap-ri', 'x-sap-sec', 'x-sz-sdk-version', 'd-nonptcha-sync', 'referer', 'user-agent']) {
      const v = h[k];
      console.log('  ' + k + ':', v ? String(v).slice(0, 150) + (String(v).length > 150 ? '...' : '') : '(KHONG CO)');
    }
    const h2 = (results[1] && results[1].headers) || {};
    console.log('  --- xoay vong? token lan2 != lan1:', h2['af-ac-enc-sz-token'] !== h['af-ac-enc-sz-token']);
    console.log('  --- xap-sec lan2 != lan1:', h2['x-sap-sec'] !== h['x-sap-sec']);
  } else {
    console.log('KHONG bat duoc request offer nao - kiem tra root co ton tai / bi redirect khong.');
  }
  console.log('Da luu:', OUT);
  cdp.close();
}
main().catch((e) => { console.error('LOI:', e.message); process.exit(1); });

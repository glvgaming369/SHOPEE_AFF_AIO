// Test: click trusted (CDP) co kich engine gui report khong, va co cho phep goi offer lien tiep (khong reload) khong.
// Dung khi da co Chrome debug (port 9333) dang mo trang affiliate da dang nhap.
import { writeFileSync } from 'node:fs';
const arg = (n, d) => { const i = process.argv.indexOf(n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const PORT = parseInt(arg('--port', '9333'), 10);
const ITEM2 = arg('--item2', '26433663046');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const target = list.find((x) => x.type === 'page' && /affiliate\.shopee/.test(x.url || ''));
if (!target) { console.log('Khong thay tab affiliate'); process.exit(1); }

const cdp = await new Promise((resolve, reject) => {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map(); const listeners = new Set(); let id = 0;
  ws.onopen = () => resolve({
    send(m, p = {}) { return new Promise((res, rej) => { const mid = ++id; pending.set(mid, { res, rej }); ws.send(JSON.stringify({ id: mid, method: m, params: p })); }); },
    on(fn) { listeners.add(fn); },
    close() { try { ws.close(); } catch (e) {} },
  });
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); return; }
    if (msg.method) for (const fn of listeners) fn(msg);
  };
  ws.onerror = () => reject(new Error('ws err'));
});

await cdp.send('Runtime.enable');
await cdp.send('Network.enable');
let reportCount = 0;
cdp.on((m) => {
  if (m.method === 'Network.requestWillBeSent' && (m.params.request.url || '').includes('df.infra')) {
    reportCount++;
    console.log('>>> [report] df.infra xuat hien, count =', reportCount);
  }
});

async function trustedClick() {
  // click that vao toa do trung tam trang (neutral)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: 260, y: 300, button: 'left', buttons: 1, clickCount: 1 });
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: 260, y: 300, button: 'left', buttons: 0, clickCount: 1 });
}

async function fetchOfferViaPage(itemid) {
  // goi qua window.fetch (wrapper se tu do header theo engine) - giong tool v0.16
  const r = await cdp.send('Runtime.evaluate', {
    expression: `fetch('/api/v3/offer/product?item_id=${itemid}', {credentials:'include', headers:{'accept':'application/json, text/plain, */*','affiliate-program-type':'1'}}).then(r=>r.text()).then(t=>t.slice(0,140))`,
    awaitPromise: true, returnByValue: true,
  });
  return r.result && r.result.value;
}

console.log('Trang hien tai:', target.url);
console.log('--- Vong 1: click trusted -> fetch item2 ---');
await trustedClick();
await sleep(2500);
console.log('report count sau click:', reportCount);
console.log('fetch1:', await fetchOfferViaPage(ITEM2));
await sleep(1500);
console.log('--- Vong 2: click trusted -> fetch item2 lan nua ---');
await trustedClick();
await sleep(2500);
console.log('report count sau click 2:', reportCount);
console.log('fetch2:', await fetchOfferViaPage(ITEM2));
writeFileSync('artifacts/cdp_click_result.txt', JSON.stringify({ reportCount }, null, 2));
cdp.close();
